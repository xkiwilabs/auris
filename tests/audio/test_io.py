from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from auris.audio import io

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _encode(tmp_path, audio, sr, suffix):
    """Write `audio` to a wav then FFmpeg-encode it to `suffix` (e.g. '.mp3'). Returns the path."""
    import soundfile as sf

    wav = tmp_path / "src.wav"
    sf.write(str(wav), audio, sr, subtype="PCM_16")
    out = tmp_path / f"src{suffix}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(wav), str(out)],
        check=True,
        capture_output=True,
    )
    return out


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg + ffprobe")
def test_decode_with_ffmpeg_reads_mp3(tmp_path):
    sr = 24000
    audio = (0.2 * np.sin(np.linspace(0, 200, 2 * sr))).astype(np.float32)  # 2s tone
    mp3 = _encode(tmp_path, audio, sr, ".mp3")

    decoded, decoded_sr = io._decode_with_ffmpeg(mp3)

    assert decoded.dtype == np.float32
    assert decoded.ndim == 1
    assert decoded_sr == sr
    assert abs(len(decoded) / decoded_sr - 2.0) < 0.3  # ~2s, allow codec padding
    assert decoded.flags.writeable  # must be a real array, not a read-only buffer view


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg + ffprobe")
def test_decode_with_ffmpeg_rejects_garbage(tmp_path):
    junk = tmp_path / "not-audio.mp3"
    junk.write_text("this is not audio")
    with pytest.raises(RuntimeError):
        io._decode_with_ffmpeg(junk)


def test_decode_with_ffmpeg_missing_binary_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(io.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="FFmpeg"):
        io._decode_with_ffmpeg(tmp_path / "anything.mp3")


def test_load_audio_falls_back_to_ffmpeg_on_soundfile_error(tmp_path, monkeypatch):
    import soundfile as sf

    sentinel = (np.ones(10, dtype=np.float32), 16000)

    def _raise(*_a, **_k):
        raise sf.SoundFileError("unsupported format")

    called = {}

    def _fake_decode(path):
        called["path"] = path
        return sentinel

    monkeypatch.setattr(io.sf, "read", _raise)
    monkeypatch.setattr(io, "_decode_with_ffmpeg", _fake_decode)

    audio, sr = io.load_audio(tmp_path / "voice.mp3")

    assert called["path"] == tmp_path / "voice.mp3"  # fallback was used
    assert sr == 16000
    assert audio.dtype == np.float32
    assert audio.ndim == 1


def test_load_audio_missing_file_reraises_and_skips_ffmpeg(tmp_path, monkeypatch):
    # A genuinely missing file raises SoundFileError (code 2 / SF_ERR_SYSTEM). It must
    # re-raise cleanly, NOT get routed into the FFmpeg fallback.
    def _boom(_path):
        raise AssertionError("ffmpeg fallback must not run for a missing file")

    monkeypatch.setattr(io, "_decode_with_ffmpeg", _boom)
    with pytest.raises(sf.SoundFileError):
        io.load_audio(tmp_path / "does-not-exist.wav")


def test_load_audio_wav_does_not_invoke_ffmpeg(tmp_path, monkeypatch):
    sr = 24000
    audio = (0.1 * np.sin(np.linspace(0, 30, sr))).astype(np.float32)
    path = tmp_path / "plain.wav"
    io.save_wav(path, audio, sr)

    def _boom(_path):
        raise AssertionError("ffmpeg fallback must not run for a readable wav")

    monkeypatch.setattr(io, "_decode_with_ffmpeg", _boom)

    loaded, loaded_sr = io.load_audio(path)
    assert loaded_sr == sr
    assert loaded.ndim == 1
    assert len(loaded) == len(audio)


def test_save_then_load_roundtrip(tmp_path):
    sr = 24000
    audio = (0.2 * np.sin(np.linspace(0, 50, sr))).astype(np.float32)
    path = tmp_path / "a.wav"

    io.save_wav(path, audio, sr)
    loaded, loaded_sr = io.load_audio(path)

    assert loaded_sr == sr
    assert loaded.dtype == np.float32
    assert loaded.ndim == 1
    assert len(loaded) == len(audio)
    assert np.allclose(loaded, audio, atol=1e-3)


def test_resample_changes_length(tmp_path):
    audio = np.zeros(24000, dtype=np.float32)
    out = io.resample(audio, orig_sr=24000, target_sr=12000)
    assert abs(len(out) - 12000) <= 1


def test_resample_noop_same_rate():
    audio = np.ones(100, dtype=np.float32)
    out = io.resample(audio, 24000, 24000)
    assert np.array_equal(out, audio)


def test_stereo_is_downmixed_to_mono(tmp_path):
    import soundfile as sf

    sr = 24000
    stereo = np.zeros((sr, 2), dtype=np.float32)
    path = tmp_path / "stereo.wav"
    sf.write(str(path), stereo, sr)

    loaded, _ = io.load_audio(path)
    assert loaded.ndim == 1


def test_duration_s():
    audio = np.zeros(48000, dtype=np.float32)
    assert io.duration_s(audio, 24000) == 2.0


def test_save_wav_creates_parent_dirs(tmp_path):
    audio = np.zeros(100, dtype=np.float32)
    path = tmp_path / "nested" / "dir" / "out.wav"
    io.save_wav(path, audio, 24000)
    assert path.exists()


def test_prepare_reference_caps_to_max_seconds():
    sr = 24000
    audio = np.full(20 * sr, 0.2, dtype=np.float32)  # 20s of constant-energy signal
    out = io.prepare_reference(audio, sr, max_seconds=12.0)
    assert len(out) <= 12 * sr
    assert len(out) >= 11 * sr


def test_prepare_reference_default_cap_is_under_f5tts_limit():
    # The default cap must be safely UNDER f5-TTS's ~12s reference limit. At/over 12s,
    # f5-TTS re-clips the audio but not our transcript → length mismatch → it leaks the
    # reference's trailing word into the start of every generation.
    sr = 24000
    audio = np.full(20 * sr, 0.2, dtype=np.float32)  # 20s, no silence to trim
    out = io.prepare_reference(audio, sr)  # default cap
    assert len(out) <= 10 * sr


def test_prepare_reference_trims_silence():
    sr = 24000
    speech = np.full(2 * sr, 0.3, dtype=np.float32)
    audio = np.concatenate([np.zeros(sr, dtype=np.float32), speech, np.zeros(sr, dtype=np.float32)])
    out = io.prepare_reference(audio, sr)
    assert len(out) < len(audio)  # leading/trailing silence removed
    assert len(out) >= int(1.5 * sr)


def test_prepare_reference_all_silence_falls_back():
    sr = 24000
    audio = np.zeros(5 * sr, dtype=np.float32)
    out = io.prepare_reference(audio, sr)
    assert len(out) == len(audio)  # no crash / no empty clip on pure silence
