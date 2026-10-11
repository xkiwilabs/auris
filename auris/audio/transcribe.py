from __future__ import annotations

import functools
import threading

import numpy as np

from auris.audio import io
from auris.audio.transcript import Segment, TranscribeOptions, Transcript, Word
from auris.config import Settings, load_settings

ASR_SAMPLE_RATE = 16000  # faster-whisper expects 16 kHz mono

# Keyed on the full runtime tuple, so switching model/device/precision loads a second
# resident copy instead of silently reusing the first.
_MODEL_CACHE: dict[tuple[str, str, int, str], object] = {}
# `get_whisper` runs on real worker threads (TranscriptionPool picks a non-resident model
# there), so the check-then-set below has to be atomic: two callers naming the same cold
# model would otherwise both build one, wasting a load and a chunk of VRAM.
_CACHE_LOCK = threading.Lock()


def _reset_model_cache() -> None:
    """Test hook: drop cached WhisperModel instances."""
    _MODEL_CACHE.clear()


@functools.lru_cache(maxsize=1)  # probed by /v1/info per request; the answer never changes
def _cuda_available() -> bool:
    try:
        import ctranslate2
    except ImportError:  # no model extra installed => no CUDA backend either
        return False
    return ctranslate2.get_cuda_device_count() > 0


def _resolve_runtime(device: str, compute_type: str) -> tuple[str, int, str]:
    """Map AURIS_ASR_DEVICE / AURIS_ASR_COMPUTE_TYPE onto ctranslate2's arguments.

    ctranslate2 takes the GPU ordinal as a separate `device_index` — it does *not*
    understand the "cuda:N" string the TTS models use — and has no Metal backend, so
    "mps" falls back to CPU rather than failing at load time. "auto" prefers the GPU."""
    name, _, index = device.partition(":")
    name = name.strip().lower() or "auto"
    if name == "auto":
        name = "cuda" if _cuda_available() else "cpu"
    elif name == "mps":
        name = "cpu"
    device_index = int(index) if index.isdigit() else 0
    if compute_type == "auto":
        compute_type = "float16" if name == "cuda" else "int8"
    return name, device_index, compute_type


def load_whisper(model: str, device: str, compute_type: str):
    """Load a **fresh** WhisperModel. Uncached — `TranscriptionPool` needs N distinct
    workers, and handing it the same instance N times would serialise every decode."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "transcription needs faster-whisper (the 'model' extra). "
            "Install it with: pip install -e '.[model]'  (or run scripts/install.sh --model)."
        ) from exc
    name, index, compute = _resolve_runtime(device, compute_type)
    return WhisperModel(model, device=name, device_index=index, compute_type=compute)


def get_whisper(model: str, device: str, compute_type: str):
    """Cached counterpart of `load_whisper`, for the single-user local path and for a
    pool request that names a model the resident workers don't hold."""
    key = (model, *_resolve_runtime(device, compute_type))
    with _CACHE_LOCK:  # held across the load so a cold model is built exactly once
        cached = _MODEL_CACHE.get(key)
        if cached is None:
            cached = load_whisper(model, device, compute_type)
            _MODEL_CACHE[key] = cached
    return cached


def run_whisper(
    whisper, audio: np.ndarray, sample_rate: int, options: TranscribeOptions
) -> Transcript:
    """Decode with an already-loaded model. Pure: no settings lookup, no model resolution,
    so the pool worker can call it on a thread without touching config."""
    options.validated()
    audio16 = io.resample(audio, sample_rate, ASR_SAMPLE_RATE)
    segments, info = whisper.transcribe(
        np.ascontiguousarray(audio16, dtype=np.float32),
        language=options.language,  # None => faster-whisper auto-detects
        task=options.task,
        word_timestamps=options.word_timestamps,
        vad_filter=options.vad_filter,
        initial_prompt=options.initial_prompt,
    )
    out: list[Segment] = []
    for index, seg in enumerate(segments):  # the generator runs the decode
        words = tuple(
            Word(
                start=float(w.start),
                end=float(w.end),
                word=w.word.strip(),
                probability=float(getattr(w, "probability", 0.0)),
            )
            for w in (seg.words or ())
        )
        out.append(
            Segment(
                id=int(getattr(seg, "id", index)),
                start=float(seg.start),
                end=float(seg.end),
                text=seg.text.strip(),
                words=words,
                no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0)),
            )
        )
    return Transcript(
        text=" ".join(s.text for s in out).strip(),
        segments=tuple(out),
        language=str(getattr(info, "language", "") or ""),
        language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        duration_s=float(getattr(info, "duration", 0.0) or 0.0),
    )


def transcribe_detailed(
    audio: np.ndarray,
    sample_rate: int,
    options: TranscribeOptions | None = None,
    settings: Settings | None = None,
) -> Transcript:
    """Resolve the model from Settings, then decode. The local (non-server) path; the
    server goes through TranscriptionPool instead so concurrency stays bounded."""
    options = (options or TranscribeOptions()).validated()
    settings = settings or load_settings()
    whisper = get_whisper(
        options.model or settings.asr_model, settings.asr_device, settings.asr_compute_type
    )
    return run_whisper(whisper, audio, sample_rate, options)


def transcribe_reference(
    audio: np.ndarray,
    sample_rate: int,
    model: str | None = None,
    settings: Settings | None = None,
) -> Transcript:
    """The cloning decode on the local path: `reference.clone_options` (English, no VAD, word
    timestamps), so `reference.reference_for_clone` can cut in a pause and keep only the words
    that survive the cut. The server runs the same options through its TranscriptionPool."""
    from auris.audio.reference import clone_options

    return transcribe_detailed(audio, sample_rate, clone_options(model), settings)


def transcribe_audio(
    audio: np.ndarray,
    sample_rate: int,
    model: str | None = None,
    device: str | None = None,
    compute_type: str | None = None,
    settings: Settings | None = None,
) -> str:
    """Transcribe mono float32 audio to text with faster-whisper.

    Used so a reference clip's transcript matches whatever was actually said — f5-TTS
    conditions on an accurate transcript. `faster_whisper` is imported lazily so the core
    package and the model-free test suite never require it. Any argument left None comes
    from Settings (`AURIS_ASR_MODEL` / `_DEVICE` / `_COMPUTE_TYPE`).

    **Language is forced to English here on purpose.** This is the cloning path: an
    accented English reference that auto-detected as another language would yield a worse
    transcript, and f5-TTS conditions on it. STT surfaces auto-detect instead."""
    settings = settings or load_settings()
    whisper = get_whisper(
        model or settings.asr_model,
        device if device is not None else settings.asr_device,
        compute_type if compute_type is not None else settings.asr_compute_type,
    )
    return run_whisper(
        whisper, audio, sample_rate, TranscribeOptions(language="en", vad_filter=False)
    ).text
