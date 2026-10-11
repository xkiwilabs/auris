from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import numpy as np
import pytest

from auris.audio.transcript import TranscribeOptions, Transcript
from auris.server.asr_pool import TranscriptionJob, TranscriptionPool
from auris.server.pool import PoolFull


class FakeWhisper:
    """Stand-in for a resident WhisperModel. `name` identifies which one ran."""

    def __init__(self, name="base.en", delay=0.0):
        self.name = name
        self.delay = delay
        self.calls = 0


def _run(whisper, audio, sample_rate, options):
    """Patched in for transcribe.run_whisper: records which model decoded."""
    whisper.calls += 1
    if whisper.delay:
        time.sleep(whisper.delay)
    return Transcript(text=f"from {whisper.name}", language="en")


def _job(model=None):
    return TranscriptionJob(
        audio=np.zeros(16000, dtype=np.float32),
        sample_rate=16000,
        options=TranscribeOptions(model=model),
    )


@pytest.fixture(autouse=True)
def _patch_run(monkeypatch):
    import auris.server.asr_pool as mod

    monkeypatch.setattr(mod.transcribe, "run_whisper", _run)


@pytest.mark.asyncio
async def test_submit_returns_a_transcript():
    pool = TranscriptionPool([FakeWhisper()], 8, "base.en", "cpu", "int8")
    result = await pool.submit(_job())
    assert result.text == "from base.en"


@pytest.mark.asyncio
async def test_two_workers_run_in_parallel():
    pool = TranscriptionPool(
        [FakeWhisper("a", delay=0.2), FakeWhisper("b", delay=0.2)], 8, "base.en", "cpu", "int8"
    )
    t0 = time.perf_counter()
    await asyncio.gather(pool.submit(_job()), pool.submit(_job()))
    assert time.perf_counter() - t0 < 0.35  # ~0.2s in parallel, not ~0.4s serial


@pytest.mark.asyncio
async def test_overflow_raises_pool_full():
    pool = TranscriptionPool(
        [FakeWhisper(delay=0.3)],
        queue_maxsize=1,
        resident_model="base.en",
        device="cpu",
        compute_type="int8",
    )
    running = asyncio.create_task(pool.submit(_job()))  # takes the only worker
    await asyncio.sleep(0.05)
    waiting = asyncio.create_task(pool.submit(_job()))  # fills the queue of 1
    await asyncio.sleep(0.05)
    with pytest.raises(PoolFull):
        await pool.submit(_job())  # one too many
    await running
    await waiting


@pytest.mark.asyncio
async def test_worker_is_returned_after_an_error():
    class Boom(FakeWhisper):
        pass

    import auris.server.asr_pool as mod

    def _raise(whisper, audio, sample_rate, options):
        raise RuntimeError("decode failed")

    pool = TranscriptionPool([Boom()], 8, "base.en", "cpu", "int8")
    original = mod.transcribe.run_whisper
    mod.transcribe.run_whisper = _raise
    try:
        with pytest.raises(RuntimeError, match="decode failed"):
            await pool.submit(_job())
    finally:
        mod.transcribe.run_whisper = original
    # the slot came back: a following request still works
    assert (await pool.submit(_job())).text == "from base.en"


@pytest.mark.asyncio
async def test_non_resident_model_uses_the_cache_but_still_occupies_a_slot(monkeypatch):
    import auris.server.asr_pool as mod

    other = FakeWhisper("small.en")
    monkeypatch.setattr(mod.transcribe, "get_whisper", lambda m, d, c: other)

    resident = FakeWhisper("base.en")
    pool = TranscriptionPool([resident], 8, "base.en", "cpu", "int8")

    result = await pool.submit(_job(model="small.en"))
    assert result.text == "from small.en"  # the cached model decoded
    assert resident.calls == 0  # the resident one did not
    assert pool.available == 1  # but the slot was released


@pytest.mark.asyncio
async def test_cancelled_request_holds_the_slot_until_the_worker_thread_stops(monkeypatch):
    """The task's headline invariant: cancelling `submit` must NOT hand the still-busy,
    device-pinned model to the next caller. The slot comes back only once the decode
    thread has actually left `run_whisper`."""
    import auris.server.asr_pool as mod

    started = threading.Event()
    release = threading.Event()

    def _block(whisper, audio, sample_rate, options):
        started.set()
        assert release.wait(5), "test deadlock: the decode was never released"
        return Transcript(text=f"from {whisper.name}")

    monkeypatch.setattr(mod.transcribe, "run_whisper", _block)
    pool = TranscriptionPool([FakeWhisper()], 8, "base.en", "cpu", "int8")

    task = asyncio.create_task(pool.submit(_job()))
    await asyncio.to_thread(started.wait, 5)  # the worker is inside run_whisper
    task.cancel()
    await asyncio.sleep(0.05)  # let the cancellation be delivered and the finally run
    assert pool.available == 0  # still decoding => the slot must NOT be back yet

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pool.available == 1  # thread stopped => slot returned, exactly once


@pytest.mark.asyncio
async def test_timed_out_request_returns_the_slot_after_the_thread_stops():
    """The Task-7 timeout path. Note `wait_for` only raises once the worker thread has
    stopped, so it bounds admission, not decode latency — holding the slot longer is the
    safe direction (a shared model would corrupt a concurrent decode)."""
    pool = TranscriptionPool([FakeWhisper(delay=0.2)], 8, "base.en", "cpu", "int8")

    t0 = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(pool.submit(_job()), 0.05)
    assert time.perf_counter() - t0 >= 0.2  # it waited for the decode thread to finish
    assert pool.available == 1
    assert (await pool.submit(_job())).text == "from base.en"  # pool still usable


@pytest.mark.asyncio
async def test_a_failed_model_load_does_not_leak_the_slot(monkeypatch):
    """A remote caller can name any allowlisted model; resolving an uncached one can fail
    (download error, bad compute_type, CUDA OOM). That must not consume the slot."""
    import auris.server.asr_pool as mod

    def _boom(model, device, compute_type):
        raise RuntimeError("weights download failed")

    monkeypatch.setattr(mod.transcribe, "get_whisper", _boom)
    pool = TranscriptionPool([FakeWhisper()], 8, "base.en", "cpu", "int8")

    with pytest.raises(RuntimeError, match="weights download failed"):
        await pool.submit(_job(model="small.en"))
    assert pool.available == pool.size == 1
    assert (await pool.submit(_job())).text == "from base.en"  # pool still usable


@pytest.mark.asyncio
async def test_a_failed_thread_start_does_not_leak_the_slot(monkeypatch):
    """The other way out of `submit` before the try/finally: if the thread can't start
    there is nothing to join, but the slot is already checked out and must go back."""
    import auris.server.asr_pool as mod

    class _ThreadingShim:
        class Thread:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                raise RuntimeError("can't start new thread")

    monkeypatch.setattr(mod, "threading", _ThreadingShim)
    pool = TranscriptionPool([FakeWhisper()], 8, "base.en", "cpu", "int8")

    with pytest.raises(RuntimeError, match="can't start new thread"):
        await pool.submit(_job())
    assert pool.available == 1

    # restore just this patch (monkeypatch.undo() would also drop the run_whisper fake)
    monkeypatch.setattr(mod, "threading", threading)
    assert (await pool.submit(_job())).text == "from base.en"  # pool still usable


@pytest.mark.asyncio
async def test_a_second_cancellation_during_the_join_does_not_leak_the_slot():
    """Cancelling a request puts us in the `finally`, awaiting the worker thread's join.
    A *second* cancellation delivered on that await must not skip the return — that would
    shrink the pool by one slot for the life of the process."""
    real_to_thread = asyncio.to_thread

    async def _cancel_on_join(fn, *args, **kwargs):
        await real_to_thread(fn, *args, **kwargs)  # the join itself still happens
        raise asyncio.CancelledError  # ...and then the second cancellation lands

    pool = TranscriptionPool([FakeWhisper()], 8, "base.en", "cpu", "int8")

    # Patched by hand, not with monkeypatch: undo() would also drop this module's autouse
    # run_whisper fake, which the assertions below still need.
    asyncio.to_thread = _cancel_on_join
    try:
        with pytest.raises(asyncio.CancelledError):
            await pool.submit(_job())
    finally:
        asyncio.to_thread = real_to_thread

    assert pool.available == pool.size == 1
    assert (await pool.submit(_job())).text == "from base.en"  # pool still usable


@pytest.mark.asyncio
async def test_admit_bounds_callers_to_workers_plus_queue():
    """The gate sizes to exactly what the pool will hold — N decoding + queue_maxsize
    waiting — so a caller `submit` would reject is rejected before it decodes anything."""
    pool = TranscriptionPool([FakeWhisper(), FakeWhisper()], 1, "base.en", "cpu", "int8")
    async with contextlib.AsyncExitStack() as held:
        for _ in range(3):  # 2 workers + 1 queue slot
            await held.enter_async_context(pool.admit())
        with pytest.raises(PoolFull):
            async with pool.admit():
                pass
    async with pool.admit():  # released => the next caller gets in
        pass


@pytest.mark.asyncio
async def test_admit_releases_when_the_body_raises():
    pool = TranscriptionPool([FakeWhisper()], 0, "base.en", "cpu", "int8")
    with pytest.raises(RuntimeError, match="decode failed"):
        async with pool.admit():
            raise RuntimeError("decode failed")
    async with pool.admit():  # the one slot came back
        pass


@pytest.mark.asyncio
async def test_admit_releases_when_the_body_is_cancelled():
    pool = TranscriptionPool([FakeWhisper()], 0, "base.en", "cpu", "int8")

    async def _held():
        async with pool.admit():
            await asyncio.sleep(10)

    task = asyncio.create_task(_held())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with pool.admit():  # cancellation unwound the context manager
        pass
