from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle — config imports this module
    from auris.config import Settings

# The model id real OpenAI clients send to /v1/audio/transcriptions. It is NOT a
# faster-whisper id, so it means "whatever this server is configured to use".
OPENAI_ALIAS = "whisper-1"

# The best model in the catalogue, not the fastest: cloning conditions every generation on this
# transcript, so one misheard word costs far more than the half second turbo adds per clip on a
# GPU. A CPU-only install may prefer AURIS_ASR_MODEL=base.en (turbo runs, but slowly there).
DEFAULT_MODEL = "large-v3-turbo"


@dataclass(frozen=True)
class AsrModel:
    id: str  # faster-whisper model id (see faster_whisper.utils._MODELS)
    params: str
    lang: str  # "en" = English-only | "multi"
    desc: str


# Curated subset of faster-whisper's ids — the ones worth offering for the ≤10 s reference
# clips auris transcribes, smallest first. Pre-download them with `auris asr pull`; anything
# not already in the HuggingFace cache is fetched on first use. Mirrors models/presets.py.
CATALOGUE: tuple[AsrModel, ...] = (
    AsrModel("tiny.en", "39M", "en", "fastest; weakest on names and numbers"),
    AsrModel("base.en", "74M", "en", "fast, fine for clean speech; the light choice on a CPU"),
    AsrModel("distil-small.en", "166M", "en", "distilled: small.en accuracy near base.en speed"),
    AsrModel("small.en", "244M", "en", "good accuracy/speed balance"),
    AsrModel("medium.en", "769M", "en", "high accuracy, English-only"),
    AsrModel("distil-large-v3", "756M", "en", "near large-v3 accuracy, much faster"),
    AsrModel("large-v3-turbo", "809M", "multi", "default — best accuracy here; multilingual"),
)
CATALOGUE_BY_ID: dict[str, AsrModel] = {m.id: m for m in CATALOGUE}


def resolve_asr_model(requested: str | None, settings: Settings) -> str:
    """Map a caller-supplied STT model id to a faster-whisper id, gated by the allowlist.

    None, "" and the OpenAI alias "whisper-1" all mean "the server's default", so an
    off-the-shelf OpenAI client keeps working. Anything else must be in
    `settings.asr_allowed` or this raises ValueError (→ 422 from the server)."""
    if not requested or requested == OPENAI_ALIAS:
        return settings.asr_model
    allowed = settings.asr_allowed
    if requested not in allowed:
        raise ValueError(f"unknown STT model {requested!r}; allowed: {', '.join(allowed)}")
    return requested
