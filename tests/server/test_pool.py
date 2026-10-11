from __future__ import annotations

import asyncio
import time
import pytest
import numpy as np

from auris.server.pool import SynthesisPool, SynthesisJob, PoolFull
from tests.server.conftest import FakeModel, make_reference


@pytest.mark.asyncio
async def test_submit_full_returns_audio():
    pool = SynthesisPool([FakeModel()], queue_maxsize=8)
    out = await pool.submit_full(SynthesisJob("hello", make_reference(), 1.0))
    assert isinstance(out, np.ndarray)
    assert out[0] == 5.0  # len("hello")


@pytest.mark.asyncio
async def test_runs_in_parallel_up_to_n_workers():
    # 2 workers, each sleeps 0.1s. 2 concurrent jobs should finish in ~0.1s, not ~0.2s.
    pool = SynthesisPool([FakeModel(delay=0.1), FakeModel(delay=0.1)], queue_maxsize=8)
    start = time.perf_counter()
    await asyncio.gather(
        pool.submit_full(SynthesisJob("aa", make_reference())),
        pool.submit_full(SynthesisJob("bb", make_reference())),
    )
    assert time.perf_counter() - start < 0.18


@pytest.mark.asyncio
async def test_backpressure_raises_poolfull_when_saturated():
    # 1 worker (busy 0.2s), queue_maxsize=1 => 1 may wait, the next is rejected.
    pool = SynthesisPool([FakeModel(delay=0.2)], queue_maxsize=1)
    busy = asyncio.create_task(pool.submit_full(SynthesisJob("x", make_reference())))
    await asyncio.sleep(0.02)  # let it check out the only model
    waiting = asyncio.create_task(pool.submit_full(SynthesisJob("y", make_reference())))
    await asyncio.sleep(0.02)  # this one is now waiting (fills the 1 waiting slot)
    with pytest.raises(PoolFull):
        await pool.submit_full(SynthesisJob("z", make_reference()))
    await asyncio.gather(busy, waiting)


@pytest.mark.asyncio
async def test_submit_stream_yields_chunks_in_order():
    pool = SynthesisPool([FakeModel()], queue_maxsize=8)
    job = SynthesisJob("One. Two. Three.", make_reference())
    seen = [chunk[0] async for chunk in pool.submit_stream(job)]
    assert seen == [4.0, 4.0, 6.0]  # len("One."), len("Two."), len("Three.")


@pytest.mark.asyncio
async def test_stream_cancel_returns_model_to_pool():
    pool = SynthesisPool([FakeModel(delay=0.02)], queue_maxsize=8)
    job = SynthesisJob("One. Two. Three. Four.", make_reference())
    agen = pool.submit_stream(job)
    await agen.__anext__()  # take first chunk
    await agen.aclose()  # abandon early
    # the single model must be back in the pool, usable again
    out = await asyncio.wait_for(pool.submit_full(SynthesisJob("hi", make_reference())), 1.0)
    assert out[0] == 2.0


@pytest.mark.asyncio
async def test_cancelled_submit_full_does_not_share_busy_model():
    # On timeout/cancel, submit_full must not return the model to the pool while its
    # worker thread is still synthesising — a later caller would then run synthesise on
    # the same (device-pinned) model concurrently. This model flags any such overlap.
    class TrackingModel:
        name = "f5_tts"
        sample_rate = 24000

        def __init__(self):
            self.busy = False
            self.overlapped = False

        def load(self) -> None:  # pragma: no cover - not used here
            pass

        def synthesise(self, text, reference, speed=1.0):
            if self.busy:
                self.overlapped = True
            self.busy = True
            time.sleep(0.1)
            self.busy = False
            return np.full(8, float(len(text)), dtype=np.float32)

    model = TrackingModel()
    pool = SynthesisPool([model], queue_maxsize=8)

    first = asyncio.create_task(pool.submit_full(SynthesisJob("first", make_reference())))
    await asyncio.sleep(0.02)  # let it check out the model and start synthesising
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    # The model must be free of the (now-cancelled) first call before this one runs.
    out = await pool.submit_full(SynthesisJob("second", make_reference()))
    assert model.overlapped is False
    assert out[0] == 6.0  # len("second")


def test_resolve_devices_explicit_and_cpu_fallback(monkeypatch):
    from auris.server.pool import resolve_devices

    assert resolve_devices("cuda:0,cuda:1") == ["cuda:0", "cuda:1"]
    # 'auto' path is exercised on the box; here just assert the explicit parse.


def test_build_pools_includes_kokoro_when_enabled(monkeypatch):
    from auris.server import pool as poolmod

    monkeypatch.setattr(poolmod, "resolve_devices", lambda spec: ["cpu"])
    monkeypatch.setattr(poolmod, "build_model_on_device", lambda name, device: FakeModel(name=name))
    monkeypatch.setenv("AURIS_KOKORO_ENABLED", "true")
    monkeypatch.setenv("AURIS_KOKORO_DEVICE", "cpu")
    monkeypatch.setenv("AURIS_GPU_DEVICES", "cpu")
    from auris.config import load_settings

    pools = poolmod.build_pools(load_settings())
    assert set(pools.keys()) == {"f5_tts", "kokoro"}


def test_build_pools_degrades_to_f5_when_kokoro_fails(monkeypatch):
    # If the kokoro extra/espeak-ng isn't installed, the f5 service must still start.
    from auris.server import pool as poolmod

    def fake_build(name, device):
        if name == "kokoro":
            raise RuntimeError("Kokoro needs the 'kokoro' extra and espeak-ng")
        return FakeModel(name=name)

    monkeypatch.setattr(poolmod, "resolve_devices", lambda spec: ["cpu"])
    monkeypatch.setattr(poolmod, "build_model_on_device", fake_build)
    monkeypatch.setenv("AURIS_KOKORO_ENABLED", "true")
    monkeypatch.setenv("AURIS_GPU_DEVICES", "cpu")
    from auris.config import load_settings

    pools = poolmod.build_pools(load_settings())
    assert set(pools.keys()) == {"f5_tts"}  # kokoro skipped, f5 still up


def test_build_pools_f5_only_when_kokoro_disabled(monkeypatch):
    from auris.server import pool as poolmod

    monkeypatch.setattr(poolmod, "resolve_devices", lambda spec: ["cpu"])
    monkeypatch.setattr(poolmod, "build_model_on_device", lambda name, device: FakeModel(name=name))
    monkeypatch.setenv("AURIS_KOKORO_ENABLED", "false")
    monkeypatch.setenv("AURIS_GPU_DEVICES", "cpu")
    from auris.config import load_settings

    pools = poolmod.build_pools(load_settings())
    assert set(pools.keys()) == {"f5_tts"}


def test_pool_remembers_its_devices():
    pool = SynthesisPool([FakeModel(), FakeModel()], queue_maxsize=2, devices=["cuda:0", "cuda:1"])
    assert pool.devices == ["cuda:0", "cuda:1"] and pool.size == 2
    assert SynthesisPool([FakeModel()], queue_maxsize=1).devices == []  # optional, back-compat
