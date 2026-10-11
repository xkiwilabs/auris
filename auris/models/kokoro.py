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
        chunks = [
            np.asarray(audio, dtype=np.float32)
            for _gs, _ps, audio in pipeline(text, voice=conditioning.voice_id, speed=speed)
        ]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)
