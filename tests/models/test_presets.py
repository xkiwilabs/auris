from __future__ import annotations

from auris.models.presets import PRESETS, PRESETS_BY_NAME


def test_presets_are_well_formed():
    assert len(PRESETS) == 6
    names = [p.name for p in PRESETS]
    assert len(set(names)) == len(names)  # unique friendly names
    for p in PRESETS:
        assert p.model == "kokoro"
        assert p.lang_code in ("a", "b")
        assert p.voice_id and p.desc
    assert PRESETS_BY_NAME["narrator-uk-f"].voice_id == "bf_emma"
