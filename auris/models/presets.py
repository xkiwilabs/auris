from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    name: str  # user-facing friendly name
    model: str  # "kokoro"
    voice_id: str  # Kokoro voice id
    lang_code: str  # "a" = US English, "b" = UK English
    desc: str


# Curated Kokoro presets so a user can synthesise before cloning. Names are friendly and reserved
# (clone rejects them); voice_ids are Kokoro's built-in voices. Chosen from Kokoro's VOICES.md
# grades (C or better), and named from measured pitch and pace rather than by ear. `calm-m` was
# am_adam until its F+ grade (the lowest of all 54) came to light; am_puck — lowest and steadiest
# of the US male voices — took the name so existing scripts keep working.
PRESETS: tuple[Preset, ...] = (
    Preset("narrator-us-f", "kokoro", "af_heart", "a", "US English, female"),
    Preset("narrator-us-m", "kokoro", "am_michael", "a", "US English, male"),
    Preset("narrator-uk-f", "kokoro", "bf_emma", "b", "UK English, female"),
    Preset("narrator-uk-m", "kokoro", "bm_george", "b", "UK English, male"),
    Preset("bright-f", "kokoro", "af_bella", "a", "US English, female, bright"),
    Preset("calm-m", "kokoro", "am_puck", "a", "US English, male, low and steady"),
    Preset("low-f", "kokoro", "af_kore", "a", "US English, female, lower-pitched"),
    Preset("soft-f", "kokoro", "af_nicole", "a", "US English, female, soft and slow"),
    Preset("lively-m", "kokoro", "am_fenrir", "a", "US English, male, lively"),
    Preset("bright-uk-f", "kokoro", "bf_isabella", "b", "UK English, female, bright"),
    Preset("deep-uk-m", "kokoro", "bm_fable", "b", "UK English, male, deep"),
)
PRESETS_BY_NAME: dict[str, Preset] = {p.name: p for p in PRESETS}
