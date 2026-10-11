from __future__ import annotations

from auris.models.presets import PRESETS, PRESETS_BY_NAME


def test_presets_are_well_formed():
    assert len(PRESETS) == 11
    names = [p.name for p in PRESETS]
    assert len(set(names)) == len(names)  # unique friendly names
    for p in PRESETS:
        assert p.model == "kokoro"
        assert p.lang_code in ("a", "b")
        assert p.voice_id and p.desc
    assert PRESETS_BY_NAME["narrator-uk-f"].voice_id == "bf_emma"


def test_calm_m_no_longer_uses_the_lowest_graded_kokoro_voice():
    # am_adam is graded F+ in Kokoro's VOICES.md; the name stays so scripts keep working.
    assert PRESETS_BY_NAME["calm-m"].voice_id == "am_puck"
    assert "am_adam" not in {p.voice_id for p in PRESETS}
