from __future__ import annotations

import numpy as np

from auris.models.base import PresetVoice, TTSModel


class KokoroModel(TTSModel):
    """Preset-voice model (Kokoro-82M). Apache-2.0 weights, 24 kHz native (no resampling).

    Like f5_tts, all heavy imports (kokoro, torch) are lazy so the core/test suite stays
    model-free. Holds one KPipeline per language front-end ('a' US, 'b' UK)."""

    name = "kokoro"
    sample_rate = 24000

    def __init__(self, device: str = "auto"):
        self._device = device
        self._pipelines: dict[str, object] = {}  # lang_code -> KPipeline
        self._mixes: dict[tuple[tuple[str, float], ...], object] = {}  # blend recipe -> tensor

    def _resolve_device(self) -> str:
        if self._device != "auto":
            return self._device
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"

    def load(self) -> None:
        try:
            from kokoro import KPipeline
        except ImportError as exc:  # mirror transcribe.py's "install the X extra" pattern
            raise RuntimeError(
                "Kokoro needs the 'kokoro' extra and the espeak-ng system package. "
                "Install with: pip install -e '.[kokoro]' and apt-get install espeak-ng"
            ) from exc
        device = self._resolve_device()
        for lang in ("a", "b"):
            if lang not in self._pipelines:
                self._pipelines[lang] = KPipeline(lang_code=lang, device=device)

    def synthesise(self, text: str, conditioning, speed: float = 1.0) -> np.ndarray:
        if not isinstance(conditioning, PresetVoice):
            raise TypeError("kokoro requires a PresetVoice, not a cloned reference")
        if not self._pipelines:
            self.load()
        pipeline = self._pipelines[conditioning.lang_code]
        voice = (
            self._mixed(pipeline, conditioning.mix) if conditioning.mix else conditioning.voice_id
        )
        chunks = [
            np.asarray(audio, dtype=np.float32)
            for _gs, _ps, audio in pipeline(text, voice=voice, speed=speed)
        ]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)

    _MAX_MIXES = 64  # recipes are tiny, but every distinct blend would otherwise stay resident

    def _mixed(self, pipeline, mix: tuple[tuple[str, float], ...]):
        """A blend's voice: the weighted sum of its components' style tensors. Kokoro voices
        are vectors, so this is a genuine in-between voice (see voice_library/blends.py)."""
        voice = self._mixes.get(mix)
        if voice is None:
            voice = sum(pipeline.load_voice(voice_id) * weight for voice_id, weight in mix)
            if len(self._mixes) >= self._MAX_MIXES:
                self._mixes.pop(next(iter(self._mixes)))
            self._mixes[mix] = voice
        return voice
