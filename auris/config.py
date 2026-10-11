from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from auris.audio.asr_models import CATALOGUE as ASR_CATALOGUE
from auris.audio.asr_models import DEFAULT_MODEL as ASR_DEFAULT_MODEL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AURIS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    models_dir: Path = Path("./models")
    default_model: str = "f5_tts"
    default_speed: float = 1.0
    device: str = "auto"  # auto | cuda | mps | cpu
    sample_rate: int = 24000  # internal/output sample rate

    # --- server (auris-server) ---
    bind_host: str = "127.0.0.1"
    port: int = 8001
    auth_token: str = ""  # empty => server refuses to start
    gpu_devices: str = "auto"  # "auto" => all visible CUDA devices; or "cuda:0,cuda:1"
    gpu_workers: int = 2  # resident model copies / parallel synthesis slots
    queue_maxsize: int = 8  # max requests waiting for a free worker; beyond => 429
    request_timeout_s: float = 120.0
    max_input_chars: int = 2000
    max_upload_seconds: float = 120.0
    default_format: str = "mp3"  # wav | pcm | mp3 | opus — compact over the network
    session_hours: float = 12.0  # dashboard cookie lifetime

    # --- speech-to-text (faster-whisper) ---
    asr_model: str = ASR_DEFAULT_MODEL  # default faster-whisper id (see audio/asr_models.py)
    asr_device: str = "auto"  # auto | cuda | cuda:N | cpu (auto => GPU when one is visible)
    asr_compute_type: str = "auto"  # auto | float16 | int8_float16 | int8 | float32
    asr_models: str = ""  # comma-separated allowlist for remote callers; "" => the catalogue
    asr_workers: int = 1  # resident whisper copies = parallel transcriptions
    asr_language: str = ""  # server default; "" => auto-detect (cloning always forces "en")
    asr_vad: bool = True  # Silero VAD filter by default on the STT endpoints
    asr_max_upload_mb: int = 250  # STT uploads: an hour of 16-bit 24 kHz WAV is ~170 MB
    # Decoded-duration cap, checked after decode and before the pool (=> 422). The byte cap
    # above admits ~1 h of uncompressed WAV but many hours of a compressed format, which
    # would decode to several GB of float32; this keeps the two limits consistent.
    asr_max_seconds: float = 3600.0
    # How long a caller waits for a queued-plus-running transcription before a 504; it
    # does not cancel or cap the decode itself. Long recordings need more than TTS's 120 s.
    asr_timeout_s: float = 600.0

    # --- which pipelines this process serves ---
    stt_enabled: bool = True  # build the transcription pool
    tts_enabled: bool = True  # build the synthesis pools

    # --- kokoro preset-voice model (built-in voices, usable before cloning) ---
    kokoro_enabled: bool = True
    kokoro_device: str = "auto"  # auto | cuda:N | cpu
    kokoro_workers: int = 1  # resident copies (presets are quick; 1 is plenty)

    @property
    def asr_allowed(self) -> tuple[str, ...]:
        """STT model ids a remote caller may request. An empty AURIS_ASR_MODELS means the
        whole curated catalogue; narrow it to just the ids you pre-pulled if you don't want
        a request triggering a weight download. The configured default is always allowed."""
        listed = tuple(m.strip() for m in self.asr_models.split(",") if m.strip())
        ids = listed or tuple(m.id for m in ASR_CATALOGUE)
        return ids if self.asr_model in ids else (self.asr_model, *ids)

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "auris.db"


def load_settings() -> Settings:
    """Construct Settings fresh (reads env each call — keeps tests isolated)."""
    return Settings()
