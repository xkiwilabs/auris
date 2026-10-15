"""Blended voices: a recipe that mixes 2–3 built-in (Kokoro) voices at chosen weights.

A blend is stored on an ordinary `speakers` row (`speakers.blend`, JSON) so ownership, sharing,
rename and delete all apply unchanged; synthesis averages the Kokoro voice tensors (see
`KokoroModel`). Only presets can be blended: a cloned voice is a recording plus its transcript,
and two recordings cannot be averaged — the 2026-10-12 blending spike found every
recording-prompt method snaps to one speaker. Import-light: no torch, no FastAPI.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from auris.models.base import PresetVoice
from auris.models.presets import PRESETS

MIN_COMPONENTS = 2
MAX_COMPONENTS = 3

_PRESETS_CI = {p.name.casefold(): p for p in PRESETS}


@dataclass(frozen=True)
class Component:
    preset: str  # the friendly preset name, for display
    voice_id: str  # the Kokoro voice it pointed at when saved, so the sound never drifts
    weight: float  # normalised: a blend's weights sum to 1


@dataclass(frozen=True)
class Blend:
    components: tuple[Component, ...]
    lang_code: str  # Kokoro front-end: the heaviest component's accent

    def describe(self) -> str:
        return " + ".join(f"{round(c.weight * 100)}% {c.preset}" for c in self.components)

    def conditioning(self) -> PresetVoice:
        heaviest = max(self.components, key=lambda c: c.weight)
        return PresetVoice(
            heaviest.voice_id,
            self.lang_code,
            mix=tuple((c.voice_id, c.weight) for c in self.components),
        )

    def public(self) -> list[dict]:
        """What an API client sees: preset names and their share, rounded for display."""
        return [{"voice": c.preset, "weight": round(c.weight, 4)} for c in self.components]


def make(items: list[tuple[str, float]]) -> Blend:
    """Validate (preset name, weight) pairs and normalise the weights. ValueError (→ 422)."""
    if not MIN_COMPONENTS <= len(items) <= MAX_COMPONENTS:
        raise ValueError(
            f"a blend mixes {MIN_COMPONENTS} or {MAX_COMPONENTS} built-in voices, not {len(items)}"
        )
    picked, seen = [], set()
    for name, weight in items:
        preset = _PRESETS_CI.get(str(name).strip().casefold())
        if preset is None:
            raise ValueError(
                f"{name!r} is not a built-in voice; only built-in voices can be blended "
                "(a cloned voice is a recording, and two recordings can't be averaged)"
            )
        if preset.name in seen:
            raise ValueError(f"{preset.name!r} appears twice in the blend")
        seen.add(preset.name)
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            raise ValueError(f"the weight for {preset.name!r} is not a number") from None
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"the weight for {preset.name!r} must be a positive number")
        picked.append((preset, weight))
    total = sum(w for _, w in picked)
    components = tuple(Component(p.name, p.voice_id, w / total) for p, w in picked)
    heaviest = max(range(len(picked)), key=lambda i: (components[i].weight, -i))
    return Blend(components, picked[heaviest][0].lang_code)


def parse_arg(arg: str) -> tuple[str, float]:
    """`narrator-uk-f=60` → ("narrator-uk-f", 60.0), for the CLI. Preset names hold no `=`."""
    name, sep, weight = arg.rpartition("=")
    if not sep or not name.strip():
        raise ValueError(f"{arg!r}: give each voice as NAME=WEIGHT, e.g. narrator-uk-f=60")
    try:
        return name.strip(), float(weight)
    except ValueError:
        raise ValueError(f"{arg!r}: the weight after '=' must be a number") from None


def to_json(blend: Blend) -> str:
    return json.dumps(
        {
            "lang": blend.lang_code,
            "components": [
                {"preset": c.preset, "voice_id": c.voice_id, "weight": c.weight}
                for c in blend.components
            ],
        }
    )


def from_json(text: str) -> Blend:
    data = json.loads(text)
    return Blend(
        tuple(
            Component(c["preset"], c["voice_id"], float(c["weight"])) for c in data["components"]
        ),
        data["lang"],
    )
