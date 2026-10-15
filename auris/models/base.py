from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class PreparedReference:
    """The conditioning for in-context cloning models like f5-TTS:
    the reference audio plus its transcript (not a fixed-dim embedding vector).

    audio is float32, mono, at sample_rate Hz (the project internal rate is 24000)."""

    audio: np.ndarray  # float32, mono
    sample_rate: int  # e.g. 24000
    transcript: str


@dataclass
class PresetVoice:
    """Conditioning for a preset-voice model: a built-in voice id (e.g. 'af_heart').

    lang_code selects the model's G2P front-end (Kokoro: 'a' = US English, 'b' = UK English).
    `mix`, when set, makes this a blend: (voice_id, weight) pairs, weights summing to 1, which
    the model averages into one voice (voice_library/blends.py); voice_id is then the heaviest
    component, kept so anything that only reads voice_id still names a real voice."""

    voice_id: str
    lang_code: str
    mix: tuple[tuple[str, float], ...] = ()


# Either an in-context cloning reference (f5-TTS) or a built-in preset voice (Kokoro).
Conditioning = PreparedReference | PresetVoice


class TTSModel(ABC):
    # Subclasses MUST set these as class attributes.
    name: str
    sample_rate: int  # output sample rate in Hz

    @abstractmethod
    def load(self) -> None:
        """Load weights into device memory. Subclasses must make this safe to
        call more than once (idempotent)."""

    @abstractmethod
    def synthesise(self, text: str, conditioning: Conditioning, speed: float = 1.0) -> np.ndarray:
        """Return float32 PCM (mono) at self.sample_rate.

        Each model accepts only the conditioning it understands (a cloning model a
        PreparedReference, a preset model a PresetVoice) and raises TypeError otherwise."""
