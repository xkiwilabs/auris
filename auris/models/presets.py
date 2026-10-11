from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str  # user-facing friendly name
    model: str  # "kokoro"
    voice_id: str  # Kokoro voice id
    lang_code: str  # "a" = US English, "b" = UK English
    desc: str


# Curated handful of Kokoro presets so a user can synthesise before cloning. Names are friendly
# and reserved (clone rejects them); voice_ids are Kokoro's built-in voices.
PRESETS: tuple[Preset, ...] = (
    Preset("narrator-us-f", "kokoro", "af_heart", "a", "US English, female"),
    Preset("narrator-us-m", "kokoro", "am_michael", "a", "US English, male"),
    Preset("narrator-uk-f", "kokoro", "bf_emma", "b", "UK English, female"),
    Preset("narrator-uk-m", "kokoro", "bm_george", "b", "UK English, male"),
    Preset("bright-f", "kokoro", "af_bella", "a", "US English, female, bright"),
    Preset("calm-m", "kokoro", "am_adam", "a", "US English, male, calm"),
)
PRESETS_BY_NAME: dict[str, Preset] = {p.name: p for p in PRESETS}
