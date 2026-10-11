from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from auris.config import Settings
from auris.models import build_model_on_device
from auris.models.base import Conditioning, PreparedReference, PresetVoice, TTSModel
from auris.models.presets import PRESETS
from auris.text.chunker import split_into_chunks
from auris.log import get_logger

_DONE = object()  # sentinel: streaming worker finished
_log = get_logger("auris.server.pool")


class PoolFull(Exception):
    """Raised when all workers are busy and the waiting queue is full (→ HTTP 429)."""


@dataclass
class SynthesisJob:
    text: str
    conditioning: Conditioning  # a cloning reference (f5) or a preset voice (kokoro)
    speed: float = 1.0


class SynthesisPool:
    """Holds N resident, device-pinned models. Up to N synthesise calls run in parallel;
    additional callers wait, bounded by queue_maxsize (excess → PoolFull)."""

    def __init__(
        self, models: list[TTSModel], queue_maxsize: int, devices: list[str] | None = None
    ):
        if not models:
            raise ValueError("SynthesisPool needs at least one model")
        self._available: asyncio.Queue[TTSModel] = asyncio.Queue()
        for m in models:
            self._available.put_nowait(m)
        self._size = len(models)
        self._waiting = 0
        self._max_waiting = queue_maxsize
        self.devices: list[str] = list(devices or [])  # reported by /v1/info

    @property
    def size(self) -> int:
        return self._size

    async def _checkout(self) -> TTSModel:
        if self._available.empty() and self._waiting >= self._max_waiting:
            raise PoolFull()
        self._waiting += 1
        try:
            return await self._available.get()
        finally:
            self._waiting -= 1

    def _return(self, model: TTSModel) -> None:
        self._available.put_nowait(model)

    async def submit_full(self, job: SynthesisJob) -> np.ndarray:
        """Synthesise the whole utterance on a worker thread. The model is returned to the
        pool only after the thread has actually stopped (so a cancelled/timed-out request
        can't leave a later caller sharing a device-pinned model mid-synthesis)."""
        model = await self._checkout()
        out: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                audio = model.synthesise(job.text, job.conditioning, job.speed)
                loop.call_soon_threadsafe(out.put_nowait, audio)
            except Exception as exc:  # surfaced to the caller
                loop.call_soon_threadsafe(out.put_nowait, exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            item = await out.get()
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            await asyncio.to_thread(thread.join)  # wait off the event loop
            self._return(model)

    async def submit_stream(self, job: SynthesisJob) -> AsyncIterator[np.ndarray]:
        """Yield one audio chunk per sentence as it is synthesised. Occupies one worker
        slot for the stream's lifetime; the model is returned only after the worker thread
        has actually stopped touching it (so a later request can't share it mid-call)."""
        model = await self._checkout()
        out: asyncio.Queue = asyncio.Queue()
        cancel = threading.Event()
        loop = asyncio.get_running_loop()
        chunks = split_into_chunks(job.text)

        def worker() -> None:
            try:
                for chunk_text in chunks:
                    if cancel.is_set():
                        break
                    audio = model.synthesise(chunk_text, job.conditioning, job.speed)
                    if cancel.is_set():
                        break
                    loop.call_soon_threadsafe(out.put_nowait, audio)
            except Exception as exc:  # surfaced to the consumer
                loop.call_soon_threadsafe(out.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(out.put_nowait, _DONE)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            while True:
                item = await out.get()
                if item is _DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            cancel.set()
            await asyncio.to_thread(thread.join)  # wait off the event loop
            self._return(model)


def resolve_devices(spec: str) -> list[str]:
    """Parse AURIS_GPU_DEVICES. 'auto' => all visible CUDA devices (or ['cpu'])."""
    if spec != "auto":
        return [d.strip() for d in spec.split(",") if d.strip()]
    import torch

    count = torch.cuda.device_count()
    if count == 0:
        return ["cpu"]
    return [f"cuda:{i}" for i in range(count)]


def build_pools(settings: Settings) -> dict[str, SynthesisPool]:
    """Build one pool per model: the f5 cloning pool (spread across resolved GPU devices) and,
    if enabled, a small Kokoro preset-voice pool on kokoro_device. Heavy (imports torch, loads
    weights) — call off the event loop at startup."""
    pools: dict[str, SynthesisPool] = {
        "f5_tts": _build_one(
            settings.default_model,
            resolve_devices(settings.gpu_devices),
            settings.gpu_workers,
            settings.queue_maxsize,
            _f5_warm_conditioning(),
        )
    }
    if settings.kokoro_enabled:
        first = PRESETS[0]
        try:
            pools["kokoro"] = _build_one(
                "kokoro",
                resolve_devices(settings.kokoro_device),
                settings.kokoro_workers,
                settings.queue_maxsize,
                PresetVoice(first.voice_id, first.lang_code),
            )
        except Exception as exc:  # noqa: BLE001 — optional model: degrade to f5-only, don't crash
            # e.g. the `kokoro` extra or espeak-ng isn't installed yet. Keep the f5 service up.
            _log.warning("Kokoro preset voices disabled — could not load the model: %s", exc)
    return pools


def _build_one(
    model_name: str,
    devices: list[str],
    workers: int,
    queue_maxsize: int,
    warm_conditioning: Conditioning,
) -> SynthesisPool:
    models: list[TTSModel] = []
    assigned: list[str] = []
    for i in range(workers):
        device = devices[i % len(devices)]
        model = build_model_on_device(model_name, device)
        model.load()
        _warm(model, warm_conditioning)
        models.append(model)
        assigned.append(device)
    return SynthesisPool(models, queue_maxsize=queue_maxsize, devices=assigned)


def _f5_warm_conditioning() -> PreparedReference:
    return PreparedReference(np.zeros(24000, dtype=np.float32), 24000, "warm up")


def _warm(model: TTSModel, conditioning: Conditioning) -> None:
    """Best-effort warm-up so the first real request isn't slow. Never fatal."""
    try:
        model.synthesise("Warm up.", conditioning, 1.0)
    except Exception:  # noqa: BLE001 — warm-up failure must not block startup
        pass
