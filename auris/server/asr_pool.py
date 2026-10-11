from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from auris.audio import transcribe
from auris.audio.transcript import TranscribeOptions, Transcript
from auris.config import Settings
from auris.log import get_logger
from auris.server.pool import PoolFull

_log = get_logger("auris.server.asr_pool")


@dataclass
class TranscriptionJob:
    audio: np.ndarray  # float32 mono, any sample rate (resampled to 16 kHz downstream)
    sample_rate: int
    options: TranscribeOptions


class TranscriptionPool:
    """Holds N resident, device-pinned WhisperModels. Up to N decodes run in parallel;
    additional callers wait, bounded by queue_maxsize (excess => PoolFull => 429).

    Deliberately mirrors SynthesisPool, including returning a worker only after its
    thread has actually stopped. A checkout is a **concurrency slot** that carries a
    default model: a job naming a different (allowlisted) model decodes with the cached
    instance for that id but still occupies the slot, so the GPU never sees more than N
    concurrent decodes however many distinct models are in flight.

    `admit()` extends that admission control *backwards* over the work a caller does before
    checkout — decoding an upload — so memory is bounded by the same numbers as GPU time."""

    def __init__(
        self,
        models: list,
        queue_maxsize: int,
        resident_model: str,
        device: str,
        compute_type: str,
    ):
        if not models:
            raise ValueError("TranscriptionPool needs at least one model")
        self._available: asyncio.Queue = asyncio.Queue()
        for m in models:
            self._available.put_nowait(m)
        # Admission gate for the pre-checkout work (see `admit`). Sized to exactly the
        # number of callers the pool will ever hold at once: N decoding + queue_maxsize
        # waiting. Like the queue above it binds to the loop on first use, not here, so
        # building the pool on a startup worker thread stays fine.
        self._gate = asyncio.Semaphore(len(models) + max(0, queue_maxsize))
        self._size = len(models)
        self._waiting = 0
        self._max_waiting = queue_maxsize
        self._resident_model = resident_model
        self._device = device
        self._compute_type = compute_type

    @property
    def size(self) -> int:
        return self._size

    @property
    def available(self) -> int:
        return self._available.qsize()

    @property
    def resident_model(self) -> str:
        return self._resident_model

    @contextlib.asynccontextmanager
    async def admit(self) -> AsyncIterator[None]:
        """Hold one admission slot for a whole STT request, including the decode that runs
        *before* `submit`.

        Without this, `429` guards GPU slots only: an upload is read and decoded to float32
        (a 250 MB compressed file becomes hundreds of MB, held at the native rate until the
        decode reaches `run_whisper`) before `submit` ever gets a chance to reject it, so
        peak memory was bounded by uvicorn's concurrency rather than by our own settings.

        The gate is full exactly when `submit` would raise `PoolFull` anyway, so it raises
        the same error — no new shape for the client, and the caller is rejected before it
        has paid for a decode."""
        if self._gate.locked():
            raise PoolFull()
        await self._gate.acquire()  # cannot block: not locked => acquires without yielding
        try:
            yield
        finally:
            self._gate.release()

    async def _checkout(self):
        if self._available.empty() and self._waiting >= self._max_waiting:
            raise PoolFull()
        self._waiting += 1
        try:
            return await self._available.get()
        finally:
            self._waiting -= 1

    def _return(self, model) -> None:
        self._available.put_nowait(model)

    def _pick(self, slot_model, options: TranscribeOptions):
        """Choose the model this slot decodes with. Called on the worker thread, never on
        the event loop: a cache miss below downloads weights and allocates GPU memory."""
        wanted = options.model
        if not wanted or wanted == self._resident_model:
            return slot_model
        # Not resident: use (and cache) a separate instance, still inside this slot. Note
        # the cached instance is shared, so two slots naming the same non-resident model
        # decode on it concurrently — this path does not give the per-instance isolation
        # the resident models do. The slot count still caps total concurrency.
        return transcribe.get_whisper(wanted, self._device, self._compute_type)

    async def submit(self, job: TranscriptionJob) -> Transcript:
        """Decode on a worker thread. The slot is released only after the thread has
        actually stopped, so a cancelled request can't leave a later caller sharing a
        device-pinned model mid-decode. Every path out of here after a successful
        `_checkout` re-queues the slot exactly once, including a failed model load."""
        slot_model = await self._checkout()
        out: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                whisper = self._pick(slot_model, job.options)
                result = transcribe.run_whisper(whisper, job.audio, job.sample_rate, job.options)
                loop.call_soon_threadsafe(out.put_nowait, result)
            except Exception as exc:  # surfaced to the caller
                loop.call_soon_threadsafe(out.put_nowait, exc)

        thread = threading.Thread(target=worker, daemon=True)
        try:
            thread.start()
        except BaseException:
            # The worker never ran, so there is nothing to join — but the slot is checked
            # out and must go back, or a failed start would wedge the pool for good.
            self._return(slot_model)
            raise
        try:
            item = await out.get()
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            # Nested so a *second* cancellation delivered while we await the join can't skip
            # the return and leak the slot for the life of the process. In that (rare) case
            # the slot goes back with the worker possibly still running — a brief overlap
            # beats permanently shrinking the pool.
            try:
                await asyncio.to_thread(thread.join)  # wait off the event loop
            finally:
                self._return(slot_model)


def build_asr_pool(settings: Settings) -> TranscriptionPool:
    """Load `asr_workers` resident copies of AURIS_ASR_MODEL. Heavy (downloads weights on
    first use, allocates GPU memory) — call off the event loop at startup."""
    models = [
        transcribe.load_whisper(settings.asr_model, settings.asr_device, settings.asr_compute_type)
        for _ in range(max(1, settings.asr_workers))
    ]
    _log.info(
        "transcription pool ready",
        model=settings.asr_model,
        workers=len(models),
        device=settings.asr_device,
    )
    return TranscriptionPool(
        models,
        settings.queue_maxsize,
        settings.asr_model,
        settings.asr_device,
        settings.asr_compute_type,
    )
