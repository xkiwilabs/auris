"""Audio fixtures and assertions for the end-to-end suite.

Nothing here imports torch, kokoro or faster_whisper at module level: `tests/test_e2e_helpers.py`
runs in the fast suite, on machines with no model extras installed.
"""

from __future__ import annotations

import functools
import hashlib
import re
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

# A sentence with no homophone traps, no numbers and no proper nouns, so a whisper
# transcription of it should be exact. Used as both the clone reference and the STT target.
REFERENCE_TEXT = (
    "The quick brown fox jumps over the lazy dog while the morning light "
    "spreads slowly across the empty field."
)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(text: str) -> list[str]:
    """Casefold, drop punctuation, split on whitespace — the comparison form for WER."""
    return _PUNCT.sub("", text.casefold()).split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: Levenshtein distance over tokens, divided by reference length.

    Hand-rolled on purpose — a dependency for twenty lines of dynamic programming would be the
    only thing the test suite adds to the install.
    """
    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    # Classic edit-distance DP; prev[j] is "cost to turn ref[:i-1] into hyp[:j]".
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "auris-e2e-audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


@functools.lru_cache(maxsize=8)
def speech_wav(text: str = REFERENCE_TEXT, voice: str = "narrator-uk-f") -> Path:
    """Synthesise `text` with Kokoro **in this process** and return a 24 kHz PCM_16 WAV path.

    Deliberately not synthesised through the server under test: an e2e transcription test must
    not be able to pass because a TTS bug and an STT bug cancelled each other out.

    Cached on disk across runs (the filename is a digest of the inputs), so a second run of the
    suite does not reload Kokoro just to regenerate identical audio.
    """
    from auris.audio import io
    from auris.config import load_settings
    from auris.models import build_model
    from auris.models.base import PresetVoice
    from auris.models.presets import PRESETS_BY_NAME

    digest = hashlib.sha256(f"{voice}\x00{text}".encode()).hexdigest()[:16]
    out = _cache_dir() / f"{digest}.wav"
    if out.exists():
        return out
    preset = PRESETS_BY_NAME[voice]
    model = build_model("kokoro", load_settings())
    model.load()
    audio = model.synthesise(text, PresetVoice(preset.voice_id, preset.lang_code))
    io.save_wav(out, audio, model.sample_rate)
    return out


def reference_wav() -> tuple[Path, str]:
    """A clone-able reference: over the route's 3 s floor, and capped to 10 s when stored."""
    return speech_wav(REFERENCE_TEXT), REFERENCE_TEXT


def assert_audible(
    path: str | Path, *, expect_sample_rate: int = 24000, min_seconds: float = 0.3
) -> np.ndarray:
    """Decode and assert this is real, non-silent mono audio. Returns the samples.

    This is the assertion that catches a 200 response carrying a buffer of zeros.
    """
    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    assert sr == expect_sample_rate, f"expected {expect_sample_rate} Hz, got {sr}"
    assert audio.ndim == 1, f"expected mono, got shape {audio.shape}"
    seconds = len(audio) / sr
    assert seconds >= min_seconds, f"only {seconds:.2f}s of audio"
    rms = float(np.sqrt(np.mean(np.square(audio))))
    assert rms > 0.005, f"audio is effectively silent (rms={rms:.5f})"
    return audio
