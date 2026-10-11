from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

from auris.audio import io
from auris.models.base import PreparedReference, TTSModel


def _resolve_device(pref: str) -> str:
    if pref != "auto":
        return pref
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class F5TTSModel(TTSModel):
    name = "f5_tts"
    sample_rate = 24000

    def __init__(self, device: str = "auto"):
        self._device_pref = device
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from f5_tts.api import F5TTS

        self._model = F5TTS(device=_resolve_device(self._device_pref))

    def synthesise(self, text: str, conditioning, speed: float = 1.0) -> np.ndarray:
        if not isinstance(conditioning, PreparedReference):
            raise TypeError("f5_tts requires a PreparedReference (a cloned voice), not a preset")
        reference = conditioning
        self.load()
        with tempfile.TemporaryDirectory() as tmp:
            ref_path = Path(tmp) / "ref.wav"
            io.save_wav(ref_path, reference.audio, reference.sample_rate)
            wav, sr, _spec = self._model.infer(
                ref_file=str(ref_path),
                ref_text=reference.transcript,
                gen_text=text,
                speed=speed,
            )
        wav = np.asarray(wav, dtype=np.float32)
        if wav.ndim != 1:
            wav = np.squeeze(wav)
        if wav.ndim != 1:
            raise ValueError(f"f5-TTS returned unexpected audio shape: {wav.shape}")
        if sr != self.sample_rate:
            wav = io.resample(wav, sr, self.sample_rate)
        return wav
