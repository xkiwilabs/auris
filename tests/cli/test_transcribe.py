from __future__ import annotations

import json
import types

import numpy as np
import soundfile as sf
from typer.testing import CliRunner

import auris.cli.commands.transcribe as transcribe_cmd
from auris.audio.transcript import Segment, Transcript
from auris.cli.main import app

runner = CliRunner()


def _write_wav(path, seconds=2, sr=16000):
    sf.write(str(path), np.zeros(seconds * sr, dtype=np.float32), sr)
    return path


def _fake_transcript(text="hello world"):
    return Transcript(
        text=text,
        segments=(Segment(id=0, start=0.0, end=1.0, text=text),),
        language="en",
        language_probability=0.99,
        duration_s=1.0,
    )


def test_transcribe_prints_text_to_stdout(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", lambda *a, **k: _fake_transcript())
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav)])
    assert result.exit_code == 0, result.output
    assert "hello world" in result.output


def test_transcribe_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", lambda *a, **k: _fake_transcript())
    wav = _write_wav(tmp_path / "a.wav")
    out = tmp_path / "a.txt"
    result = runner.invoke(app, ["transcribe", str(wav), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().strip() == "hello world"


def test_transcribe_srt_format(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", lambda *a, **k: _fake_transcript())
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav), "--format", "srt"])
    assert result.exit_code == 0, result.output
    assert "00:00:00,000 --> 00:00:01,000" in result.output


def test_transcribe_passes_options(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)

    def _capture(audio, sr, options=None, **kwargs):
        seen["options"] = options
        return _fake_transcript()

    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", _capture)
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(
        app,
        [
            "transcribe",
            str(wav),
            "--asr-model",
            "small.en",
            "--language",
            "fr",
            "--translate",
            "--word-timestamps",
            "--prompt",
            "auris",
        ],
    )
    assert result.exit_code == 0, result.output
    o = seen["options"]
    assert o.model == "small.en"
    assert o.language == "fr"
    assert o.task == "translate"
    assert o.word_timestamps is True
    assert o.initial_prompt == "auris"


def test_transcribe_language_auto_means_detect(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(
        transcribe_cmd,
        "transcribe_detailed",
        lambda audio, sr, options=None, **k: (seen.update(o=options), _fake_transcript())[1],
    )
    wav = _write_wav(tmp_path / "a.wav")
    runner.invoke(app, ["transcribe", str(wav), "--language", "auto"])
    assert seen["o"].language is None


def test_transcribe_mic_is_not_capped_at_ten_seconds(monkeypatch):
    """The 10 s cap is f5's cloning limit — transcription records for as long as asked."""
    seen = {}
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", lambda *a, **k: _fake_transcript())

    def _record(seconds, sample_rate, **kwargs):
        seen.update(seconds=seconds, sample_rate=sample_rate)
        return np.zeros(int(seconds * sample_rate), dtype=np.float32)

    monkeypatch.setattr(transcribe_cmd, "record_audio", _record)
    result = runner.invoke(app, ["transcribe", "--mic", "--seconds", "45"])
    assert result.exit_code == 0, result.output
    assert seen["seconds"] == 45.0
    assert seen["sample_rate"] == 24000  # the project's internal rate
    assert "hello world" in result.output


def test_transcribe_remote_uploads_the_file(tmp_path, monkeypatch):
    seen = {}

    def _transcribe(audio_bytes, filename="audio.wav", **kwargs):
        seen.update(filename=filename, kwargs=kwargs, size=len(audio_bytes))
        return {"text": "remote text"}

    monkeypatch.setattr(
        transcribe_cmd, "remote_client", lambda: types.SimpleNamespace(transcribe=_transcribe)
    )
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav), "--asr-model", "small.en"])
    assert result.exit_code == 0, result.output
    assert "remote text" in result.output
    assert seen["filename"] == "a.wav"
    assert seen["kwargs"]["model"] == "small.en"


def test_transcribe_remote_forwards_every_option(tmp_path, monkeypatch):
    """Including --language auto verbatim: the service tells an absent language (use its
    configured default) apart from "auto" (always detect), so collapsing it here would
    silently force the server's default language."""
    seen = {}

    def _transcribe(audio_bytes, filename="audio.wav", **kwargs):
        seen.update(kwargs)
        return {"text": "remote text"}

    monkeypatch.setattr(
        transcribe_cmd, "remote_client", lambda: types.SimpleNamespace(transcribe=_transcribe)
    )
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(
        app,
        [
            "transcribe",
            str(wav),
            "--asr-model",
            "small.en",
            "--language",
            "auto",
            "--translate",
            "--word-timestamps",
            "--prompt",
            "auris",
            "--format",
            "verbose_json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen == {
        "model": "small.en",
        "language": "auto",
        "task": "translate",
        "response_format": "verbose_json",
        "prompt": "auris",
        "word_timestamps": True,
    }


def test_transcribe_remote_text_format_prints_verbatim(tmp_path, monkeypatch):
    """srt/vtt/text come back from the service as a plain string — print it as-is, not
    as a JSON-quoted blob."""
    seen = {}
    srt = "1\n00:00:00,000 --> 00:00:01,000\nhello world\n"

    def _transcribe(audio_bytes, filename="audio.wav", **kwargs):
        seen.update(kwargs)
        return srt

    monkeypatch.setattr(
        transcribe_cmd, "remote_client", lambda: types.SimpleNamespace(transcribe=_transcribe)
    )
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav), "--format", "srt"])
    assert result.exit_code == 0, result.output
    assert seen["response_format"] == "srt"
    assert result.output.strip() == srt.strip()


def test_transcribe_json_keeps_non_ascii_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(
        transcribe_cmd, "transcribe_detailed", lambda *a, **k: _fake_transcript("héllo wörld")
    )
    wav = _write_wav(tmp_path / "a.wav")
    out = tmp_path / "a.json"
    result = runner.invoke(app, ["transcribe", str(wav), "--format", "json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    written = out.read_text(encoding="utf-8")
    assert "héllo wörld" in written  # not escaped to é
    assert json.loads(written) == {"text": "héllo wörld"}


def test_transcribe_rejects_a_file_and_mic_together(tmp_path, monkeypatch):
    """Recording would silently ignore the named file, so refuse instead."""
    called = []
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(transcribe_cmd, "record_audio", lambda *a, **k: called.append(a))
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav), "--mic"])
    assert result.exit_code == 1
    assert "--mic" in result.output
    assert called == []


def test_transcribe_reports_an_unwritable_out_path(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", lambda *a, **k: _fake_transcript())
    wav = _write_wav(tmp_path / "a.wav")
    out = tmp_path / "no-such-dir" / "a.txt"
    result = runner.invoke(app, ["transcribe", str(wav), "--out", str(out)])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)  # no traceback
    assert str(out) in result.output


def test_transcribe_reports_a_missing_dependency(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)

    def _raise(*a, **k):
        raise RuntimeError("transcription needs faster-whisper (the 'model' extra)")

    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", _raise)
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav)])
    assert result.exit_code == 1
    assert "faster-whisper" in result.output


def test_transcribe_requires_audio_or_mic():
    result = runner.invoke(app, ["transcribe"])
    assert result.exit_code == 1
    assert "--mic" in result.output


def test_local_rejects_a_model_outside_the_allowlist(tmp_path, monkeypatch):
    """Locally the id used to go straight to faster-whisper, which tried to download it
    from HuggingFace. It goes through the same resolver as `auris asr pull` and the
    service now, so an unknown id is one clean error on every surface."""
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)

    def _never(*args, **kwargs):
        raise AssertionError("decoded with an unknown model instead of rejecting it")

    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", _never)
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav), "--asr-model", "gpt-4o-transcribe"])
    assert result.exit_code == 1, result.output
    assert "unknown STT model" in result.output


def test_local_accepts_the_openai_whisper_1_alias(tmp_path, monkeypatch):
    # The alias means "the configured default" everywhere, remote or local.
    monkeypatch.setattr(transcribe_cmd, "remote_client", lambda: None)
    seen = {}

    def _capture(audio, sample_rate, options=None, settings=None):
        seen["model"] = options.model
        return _fake_transcript()

    monkeypatch.setattr(transcribe_cmd, "transcribe_detailed", _capture)
    wav = _write_wav(tmp_path / "a.wav")
    result = runner.invoke(app, ["transcribe", str(wav), "--asr-model", "whisper-1"])
    assert result.exit_code == 0, result.output
    assert seen["model"] == "base.en"
