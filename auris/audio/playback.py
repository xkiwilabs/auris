from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def _import_sounddevice():
    """Import sounddevice lazily, raising a friendly error if the 'audio' extra is absent."""
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:  # OSError: PortAudio shared lib not found
        raise RuntimeError(
            "audio playback/recording needs the 'audio' extra (sounddevice + PortAudio). "
            "Install it with: pip install -e '.[audio]'  (or run scripts/install.sh). "
            "On Linux you may also need the system library: apt install libportaudio2"
        ) from exc
    return sd


def play_audio(audio: np.ndarray, sample_rate: int) -> None:
    """Play mono float32 audio through the default output device (blocks until done).

    `sounddevice` (and the PortAudio system library) is imported lazily so the rest of
    the package — and the test suite — never require it."""
    sd = _import_sounddevice()

    sd.play(np.asarray(audio, dtype=np.float32), samplerate=sample_rate)
    sd.wait()


def play_stream(chunks: Iterable[np.ndarray], sample_rate: int) -> None:
    """Play a stream of mono float32 audio chunks back-to-back, gaplessly.

    Opens a single output stream and writes each chunk as it arrives, so playback of
    one chunk overlaps generation of the next. `sounddevice` is imported lazily (same
    as play_audio)."""
    sd = _import_sounddevice()

    stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")
    try:
        stream.start()
        for chunk in chunks:
            stream.write(np.asarray(chunk, dtype=np.float32))
    finally:
        stream.stop()
        stream.close()
