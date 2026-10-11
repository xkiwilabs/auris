from __future__ import annotations

import asyncio

import numpy as np
from typer.testing import CliRunner

import auris.cli.commands.voices as voices_cmd
from auris.audio.transcript import Segment, Transcript, Word
from auris.cli.main import app
from auris.config import load_settings
from auris.voice_library import cloning, db, speakers

runner = CliRunner()
SR = 24000


def _speech(spec):
    """Tone bursts ("s") and quiet pauses ("p"), plus Words timed to the bursts."""
    parts, words, t = [], [], 0.0
    rng = np.random.default_rng(0)
    for kind, seconds, word in spec:
        n = int(seconds * SR)
        if kind == "s":
            parts.append(0.3 * np.sin(2 * np.pi * 180 * np.arange(n) / SR))
            words.append(Word(start=t, end=t + seconds, word=word, probability=1.0))
        else:
            parts.append(1e-4 * rng.standard_normal(n))
        t += seconds
    return np.concatenate(parts).astype(np.float32), words


# Stored by the old blind cap: speech right up to the last sample, mid-way through "and".
# Each burst stands in for a phrase, so a "word" here is several real ones.
_CUT = [
    ("p", 0.3, None),
    ("s", 3.0, "Good one, I'm going to try."),
    ("p", 0.4, None),
    ("s", 3.0, "Talking through this mic,"),
    ("p", 0.4, None),
    ("s", 2.9, "and"),
]
_CLEAN = [
    ("p", 0.3, None),
    ("s", 3.5, "Hello, this is me talking,"),
    ("p", 0.3, None),
    ("s", 3.5, "and it's a clean clip."),
    ("p", 0.5, None),
]
_CUT_OLD = "Good one, I'm going to try. Talking through this mic, and I guess more words."
_CUT_NEW = "Good one, I'm going to try. Talking through this mic,"
_CLEAN_TEXT = "Hello, this is me talking, and it's a clean clip."


def _seed(name, spec, transcript):
    audio, words = _speech(spec)

    async def go():
        settings = load_settings()
        conn = await db.connect(settings.db_path)
        try:
            await db.create_tables(conn)
            sp = await cloning.get_or_create_speaker(conn, name)
            await cloning.add_reference(
                conn, settings.voices_dir, sp.id, audio, SR, transcript, len(audio) / SR
            )
        finally:
            await conn.close()

    asyncio.run(go())
    return words


def _clips(name):
    async def go():
        conn = await db.connect(load_settings().db_path)
        try:
            sp = await speakers.get_speaker_by_name_and_owner(conn, name, None)
            return await speakers.get_clips(conn, sp.id)
        finally:
            await conn.close()

    return asyncio.run(go())


def _decoder(words_by_len):
    """A fake transcribe_reference answering with the words seeded for a clip of that length."""

    def fake(audio, sample_rate, model=None, settings=None):
        words = words_by_len[round(len(audio) / sample_rate, 1)]
        text = " ".join(w.word for w in words)
        return Transcript(text=text, segments=(Segment(0, 0.0, 1.0, text, tuple(words)),))

    return fake


class _FakeVoice:
    """Stands in for f5-TTS: "speaks" by remembering what it was asked, and leaks the reference's
    last word whenever that reference is one of the `leaky` lengths (the clipped originals)."""

    sample_rate = SR

    def __init__(self, leaky_seconds, always=False):
        self.leaky = {round(x, 1) for x in leaky_seconds}
        self.always = always
        self.said = None

    def synthesise(self, text, ref, speed=1.0):
        leaks = self.always or round(len(ref.audio) / ref.sample_rate, 1) in self.leaky
        self.said = ("and " if leaks else "") + text
        return np.zeros(SR, np.float32)


def _probe(monkeypatch, *leaky_seconds, always=False):
    voice = _FakeVoice(leaky_seconds, always)
    monkeypatch.setattr(voices_cmd, "_load_probe_model", lambda settings: voice)
    monkeypatch.setattr(
        voices_cmd,
        "transcribe_detailed",
        lambda wav, sr, options=None, settings=None: Transcript(text=voice.said),
    )
    return voice


def test_a_dry_run_reports_the_clip_that_ends_mid_word_and_changes_nothing(
    cli_data_dir, monkeypatch
):
    cut = _seed("cutoff", _CUT, _CUT_OLD)
    clean = _seed("clean", _CLEAN, _CLEAN_TEXT)
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({10.0: cut, 8.1: clean}))
    _probe(monkeypatch, 10.0)
    r = runner.invoke(app, ["voices", "refit"])
    assert r.exit_code == 0, r.output
    assert "cutoff" in r.output and "stray words in 6/6" in r.output
    assert ": 0/6" in r.output  # the cut-back clip was re-tested
    assert _CUT_NEW in r.output
    assert "dry run" in r.output.lower()
    assert len(_clips("cutoff")) == 1 and len(_clips("clean")) == 1


def test_apply_adds_a_refit_clip_and_keeps_the_old_one(cli_data_dir, monkeypatch):
    cut = _seed("cutoff", _CUT, _CUT_OLD)
    clean = _seed("clean", _CLEAN, _CLEAN_TEXT)
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({10.0: cut, 8.1: clean}))
    _probe(monkeypatch, 10.0)
    r = runner.invoke(app, ["voices", "refit", "--apply"])
    assert r.exit_code == 0, r.output
    old, new = _clips("cutoff")
    assert old.transcript == _CUT_OLD
    assert new.transcript == _CUT_NEW  # the newest clip is the one synthesis uses
    assert new.duration_s < 7.5
    assert len(_clips("clean")) == 1  # a clean clip is left alone


def test_retranscribe_replaces_a_misheard_transcript_but_keeps_the_audio(cli_data_dir, monkeypatch):
    clean = _seed("clean", _CLEAN, "Hello, this is me talking, and its a clean clip.")
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({8.1: clean}))
    _probe(monkeypatch)
    assert "transcript differs" in runner.invoke(app, ["voices", "refit"]).output
    r = runner.invoke(app, ["voices", "refit", "--apply", "--retranscribe"])
    assert r.exit_code == 0, r.output
    old, new = _clips("clean")
    assert new.transcript == _CLEAN_TEXT
    assert abs(new.duration_s - old.duration_s) < 0.01


def test_a_transcript_that_covers_little_of_the_audio_is_flagged_for_re_recording(
    cli_data_dir, monkeypatch
):
    # Five seconds of sound and one word: Whisper hallucinating on noise or music.
    _seed("noisy", [("s", 5.0, "You")], "You")
    monkeypatch.setattr(
        voices_cmd,
        "transcribe_reference",
        _decoder({5.0: [Word(start=0.0, end=0.5, word="You", probability=0.2)]}),
    )
    _probe(monkeypatch)
    r = runner.invoke(app, ["voices", "refit", "--apply"])
    assert r.exit_code == 0, r.output
    assert "re-record" in r.output
    assert len(_clips("noisy")) == 1  # nothing to fix automatically


def test_refit_names_one_voice(cli_data_dir, monkeypatch):
    cut = _seed("cutoff", _CUT, "x")
    _seed("clean", _CLEAN, _CLEAN_TEXT)
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({10.0: cut}))
    _probe(monkeypatch)
    r = runner.invoke(app, ["voices", "refit", "cutoff"])
    assert r.exit_code == 0, r.output
    assert "clean" not in r.output


def test_a_clean_ending_that_still_leaks_is_left_alone_when_there_is_nothing_better(
    cli_data_dir, monkeypatch
):
    # Leaky whatever the cut — cutting back doesn't help, so nothing is stored.
    cut = _seed("cutoff", _CUT, _CUT_OLD)
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({10.0: cut}))
    _probe(monkeypatch, always=True)
    r = runner.invoke(app, ["voices", "refit", "--apply"])
    assert r.exit_code == 0, r.output
    assert "didn't help" in r.output and "re-record" in r.output
    assert len(_clips("cutoff")) == 1


def test_a_voice_that_does_not_leak_is_not_cut_even_if_it_ends_abruptly(cli_data_dir, monkeypatch):
    # The old tight trim left most stored clips with no quiet after them; if they test clean,
    # cutting them shorter would only cost voice.
    cut = _seed("cutoff", _CUT, _CUT_OLD)
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({10.0: cut}))
    _probe(monkeypatch)
    r = runner.invoke(app, ["voices", "refit", "--apply"])
    assert "ok (0/6 stray)" in r.output
    assert len(_clips("cutoff")) == 1


def test_refit_refuses_to_run_against_a_remote_server(cli_data_dir, monkeypatch):
    monkeypatch.setenv("AURIS_BASE_URL", "http://127.0.0.1:9")
    r = runner.invoke(app, ["voices", "refit"])
    assert r.exit_code == 1
    assert "on the server box" in r.output


def test_a_missing_clip_file_is_reported_and_the_rest_are_still_checked(cli_data_dir, monkeypatch):
    _seed("broken", _CLEAN, _CLEAN_TEXT)
    clean = _seed("clean", _CLEAN, _CLEAN_TEXT)
    for wav in (cli_data_dir / "voices").rglob("clip_001.wav"):
        wav.unlink()
        break  # the first voice's clip only
    monkeypatch.setattr(voices_cmd, "transcribe_reference", _decoder({8.1: clean}))
    _probe(monkeypatch)
    r = runner.invoke(app, ["voices", "refit"])
    assert r.exit_code == 0, r.output
    assert "skipped" in r.output
    assert "ok (0/6 stray)" in r.output  # the other voice was still checked


def test_refit_without_the_model_extra_fails_cleanly(cli_data_dir, monkeypatch):
    _seed("clean", _CLEAN, _CLEAN_TEXT)

    def _missing(settings):
        raise ImportError("No module named 'f5_tts'")

    monkeypatch.setattr(voices_cmd, "_load_probe_model", _missing)
    r = runner.invoke(app, ["voices", "refit"])
    assert r.exit_code == 1
    assert "model extra" in r.output
    assert "Traceback" not in r.output
