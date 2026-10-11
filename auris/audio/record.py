from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np


def record_audio(
    seconds: float,
    sample_rate: int,
    on_tick: Callable[[int], None] | None = None,
) -> np.ndarray:
    """Record `seconds` of mono audio from the default input device.

    Returns float32 mono PCM at `sample_rate`. If `on_tick` is given, it is called once
    per second with the number of seconds remaining (for a live countdown) while the
    recording runs. `sounddevice` (and the PortAudio system library) is imported lazily
    so the rest of the package — and the test suite — never require it."""
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:  # OSError: PortAudio shared lib not found
        raise RuntimeError(
            "recording needs the 'audio' extra (sounddevice + PortAudio). "
            "Install it with: pip install -e '.[audio]'  (or run scripts/install.sh). "
            "On Linux you may also need the system library: apt install libportaudio2"
        ) from exc

    frames = int(seconds * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="float32")
    if on_tick is not None:
        for remaining in range(int(round(seconds)), 0, -1):
            on_tick(remaining)
            time.sleep(1)
    sd.wait()
    return np.asarray(audio, dtype=np.float32).reshape(-1)
