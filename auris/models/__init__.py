from __future__ import annotations

from auris.config import Settings
from auris.models.base import TTSModel


def build_model(name: str, settings: Settings) -> TTSModel:
    if name == "f5_tts":
        from auris.models.f5_tts import F5TTSModel

        return F5TTSModel(device=settings.device)
    if name == "kokoro":
        from auris.models.kokoro import KokoroModel

        return KokoroModel(device=settings.kokoro_device)
    raise ValueError(f"unknown model: {name!r}")


def build_model_on_device(name: str, device: str) -> TTSModel:
    """Build a model pinned to an explicit device (e.g. 'cuda:0', 'cuda:1', 'cpu').

    Used by the server's worker pool to place each resident copy on a chosen GPU."""
    if name == "f5_tts":
        from auris.models.f5_tts import F5TTSModel

        return F5TTSModel(device=device)
    if name == "kokoro":
        from auris.models.kokoro import KokoroModel

        return KokoroModel(device=device)
    raise ValueError(f"unknown model: {name!r}")
