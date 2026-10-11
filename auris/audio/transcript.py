from __future__ import annotations

from dataclasses import dataclass

TASKS = ("transcribe", "translate")


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    word: str
    probability: float


@dataclass(frozen=True)
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: tuple[Word, ...] = ()
    no_speech_prob: float = 0.0


@dataclass(frozen=True)
class Transcript:
    """A finished transcription. `text` is what every pre-existing caller wanted; the
    rest is what an STT client wants."""

    text: str
    segments: tuple[Segment, ...] = ()
    language: str = ""
    language_probability: float = 0.0
    duration_s: float = 0.0


@dataclass(frozen=True)
class TranscribeOptions:
    """Per-request decode settings. `None` means "take the server default"."""

    model: str | None = None  # allowlist-gated; None => AURIS_ASR_MODEL
    language: str | None = None  # None => auto-detect
    task: str = "transcribe"  # "transcribe" | "translate" (into English)
    word_timestamps: bool = False
    vad_filter: bool = True  # faster-whisper's bundled Silero VAD
    initial_prompt: str | None = None  # biases spelling of names/jargon

    def validated(self) -> TranscribeOptions:
        if self.task not in TASKS:
            raise ValueError(f"task must be one of {', '.join(TASKS)} (got {self.task!r})")
        return self
