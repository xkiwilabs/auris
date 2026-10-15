"""Blended voices in the library: the recipe, the speakers row, resolution, the Kokoro mix."""

from __future__ import annotations

import pytest
import pytest_asyncio

from auris.config import load_settings
from auris.models.base import PresetVoice
from auris.voice_library import blends, cloning, db, speakers
from auris.voice_library.resolve import resolve_voice
from auris.voice_library.viewer import Viewer


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.create_tables(c)
    yield c
    await c.close()


def _mix(*items):
    return blends.to_json(blends.make(list(items)))


# ---------------------------------------------------------------- the recipe


def test_make_normalises_weights_and_keeps_voice_ids():
    b = blends.make([("narrator-uk-f", 60), ("Bright-UK-F", 40)])  # names match case-insensitively
    assert [(c.preset, c.voice_id) for c in b.components] == [
        ("narrator-uk-f", "bf_emma"),
        ("bright-uk-f", "bf_isabella"),
    ]
    assert [c.weight for c in b.components] == pytest.approx([0.6, 0.4])
    assert b.describe() == "60% narrator-uk-f + 40% bright-uk-f"
    assert b.public() == [
        {"voice": "narrator-uk-f", "weight": 0.6},
        {"voice": "bright-uk-f", "weight": 0.4},
    ]


def test_the_accent_follows_the_heaviest_voice_and_a_tie_the_first():
    assert blends.make([("narrator-us-f", 1), ("narrator-uk-m", 3)]).lang_code == "b"
    assert blends.make([("narrator-us-f", 3), ("narrator-uk-m", 1)]).lang_code == "a"
    assert blends.make([("narrator-uk-m", 1), ("narrator-us-f", 1)]).lang_code == "b"
    assert blends.make([("narrator-us-f", 1), ("narrator-uk-m", 1)]).lang_code == "a"


def test_conditioning_carries_the_whole_mix():
    cond = blends.make([("calm-m", 1), ("deep-uk-m", 3)]).conditioning()
    assert isinstance(cond, PresetVoice)
    assert cond.voice_id == "bm_fable" and cond.lang_code == "b"
    assert cond.mix == (("am_puck", 0.25), ("bm_fable", 0.75))


@pytest.mark.parametrize(
    "items, needle",
    [
        ([("calm-m", 1)], "not 1"),
        ([("calm-m", 1), ("deep-uk-m", 1), ("low-f", 1), ("soft-f", 1)], "not 4"),
        ([("calm-m", 1), ("my-clone", 1)], "not a built-in voice"),
        ([("calm-m", 1), ("CALM-M", 1)], "twice"),
        ([("calm-m", 0), ("deep-uk-m", 1)], "positive"),
        ([("calm-m", -1), ("deep-uk-m", 1)], "positive"),
        ([("calm-m", float("nan")), ("deep-uk-m", 1)], "positive"),
        ([("calm-m", float("inf")), ("deep-uk-m", 1)], "positive"),
        ([("calm-m", "lots"), ("deep-uk-m", 1)], "not a number"),
    ],
)
def test_bad_recipes_are_refused(items, needle):
    with pytest.raises(ValueError, match=needle):
        blends.make(items)


def test_parse_arg():
    assert blends.parse_arg("narrator-uk-f=60") == ("narrator-uk-f", 60.0)
    assert blends.parse_arg(" calm-m = 2.5 ") == ("calm-m", 2.5)
    for bad in ("calm-m", "=60", "calm-m=lots"):
        with pytest.raises(ValueError):
            blends.parse_arg(bad)


def test_json_round_trip():
    b = blends.make([("calm-m", 1), ("deep-uk-m", 2), ("low-f", 1)])
    assert blends.from_json(blends.to_json(b)) == b


# ---------------------------------------------------------------- the speakers row


@pytest.mark.asyncio
async def test_put_blend_creates_then_re_mixes(conn):
    sp, created = await speakers.put_blend(conn, "warm", None, _mix(("calm-m", 1), ("low-f", 1)))
    assert created and sp.is_blend and sp.default_model == "kokoro"
    assert sp.visibility == speakers.SHARED  # unowned is always shared
    again, created = await speakers.put_blend(conn, "WARM", None, _mix(("calm-m", 3), ("low-f", 1)))
    assert not created and again.id == sp.id
    assert blends.from_json(again.blend).components[0].weight == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_put_blend_refuses_a_cloned_voice_and_an_unowned_private(conn):
    await speakers.create_speaker(conn, "cloned")
    with pytest.raises(ValueError, match="cloned voice"):
        await speakers.put_blend(conn, "cloned", None, _mix(("calm-m", 1), ("low-f", 1)))
    await speakers.put_blend(conn, "warm", None, _mix(("calm-m", 1), ("low-f", 1)))
    with pytest.raises(ValueError):
        await speakers.put_blend(conn, "warm", None, _mix(("calm-m", 1), ("low-f", 1)), "private")


@pytest.mark.asyncio
async def test_a_blend_gets_no_clips(conn, tmp_path):
    sp, _ = await speakers.put_blend(conn, "warm", None, _mix(("calm-m", 1), ("low-f", 1)))
    with pytest.raises(ValueError, match="blended voice"):
        await speakers.add_clip(conn, sp.id, "clip_001.wav", "hi", 2.0)
    assert await speakers.get_clips(conn, sp.id) == []


@pytest.mark.asyncio
async def test_the_listing_carries_the_recipe(conn):
    recipe = _mix(("calm-m", 1), ("low-f", 1))
    await speakers.put_blend(conn, "warm", None, recipe)
    (row,) = await speakers.list_visible_with_stats(conn, None)
    assert row == ("warm", 0, 0.0, "kokoro", None, "shared", recipe)


@pytest.mark.asyncio
async def test_migrate_adds_the_blend_column_to_an_old_speakers_table(tmp_path):
    path = tmp_path / "old.db"
    c = await db.connect(path)
    await db.create_tables(c)
    # Rebuild speakers without the column, as a database from before this feature has it.
    await c.execute("ALTER TABLE speakers DROP COLUMN blend")
    await c.commit()
    cols = {r[1] for r in await (await c.execute("PRAGMA table_info(speakers)")).fetchall()}
    assert "blend" not in cols
    await db.migrate(c)
    await db.migrate(c)  # idempotent
    cols = {r[1] for r in await (await c.execute("PRAGMA table_info(speakers)")).fetchall()}
    assert "blend" in cols
    await c.close()


# ---------------------------------------------------------------- resolution


@pytest.mark.asyncio
async def test_a_blend_resolves_to_kokoro_with_its_mix(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    await speakers.put_blend(conn, "warm", None, _mix(("narrator-uk-f", 60), ("bright-uk-f", 40)))
    for viewer in (None, Viewer("someone", False)):  # unscoped (the box) and an account
        r = await resolve_voice(conn, settings, "warm", viewer=viewer)
        assert r.model == "kokoro" and r.kind == "blend"
        assert r.conditioning.voice_id == "bf_emma"
        assert [v for v, _ in r.conditioning.mix] == ["bf_emma", "bf_isabella"]


@pytest.mark.asyncio
async def test_a_cloned_voice_still_resolves_as_before(conn, tmp_path, monkeypatch):
    import numpy as np

    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    settings = load_settings()
    sp = await speakers.create_speaker(conn, "Me")
    await cloning.add_reference(
        conn, settings.voices_dir, sp.id, np.zeros(24000, np.float32), 24000, "hello", 1.0
    )
    r = await resolve_voice(conn, settings, "Me")
    assert r.kind == "cloned" and r.model == "f5_tts"


# ---------------------------------------------------------------- the Kokoro mix


class _FakePipeline:
    def __init__(self):
        self.loads, self.voices = [], []

    def load_voice(self, voice_id):
        import numpy as np

        self.loads.append(voice_id)
        return {"af_a": np.array([1.0, 0.0]), "af_b": np.array([0.0, 1.0])}[voice_id]

    def __call__(self, text, voice, speed):
        import numpy as np

        self.voices.append(voice)
        yield None, None, np.zeros(4, np.float32)


def test_kokoro_sums_the_weighted_voice_tensors_and_caches_the_mix():
    import numpy as np

    from auris.models import build_model_on_device

    m = build_model_on_device("kokoro", "cpu")
    pipe = _FakePipeline()
    m._pipelines = {"a": pipe}
    mix = PresetVoice("af_a", "a", mix=(("af_a", 0.75), ("af_b", 0.25)))
    m.synthesise("hi", mix)
    m.synthesise("again", mix)
    assert np.allclose(pipe.voices[0], [0.75, 0.25])
    assert pipe.loads == ["af_a", "af_b"]  # the second call reused the cached mix
    m.synthesise("plain", PresetVoice("af_b", "a"))
    assert pipe.voices[-1] == "af_b"  # an ordinary preset still passes its id


@pytest.mark.asyncio
async def test_add_reference_refuses_a_blend_before_writing_the_wav(conn, tmp_path):
    import numpy as np

    sp, _ = await speakers.put_blend(conn, "warm", None, _mix(("calm-m", 1), ("low-f", 1)))
    with pytest.raises(ValueError, match="blended voice"):
        await cloning.add_reference(
            conn, tmp_path, sp.id, np.zeros(24000, np.float32), 24000, "hello", 1.0
        )
    assert not any(tmp_path.rglob("*.wav"))
