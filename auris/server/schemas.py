from __future__ import annotations

from pydantic import BaseModel, model_validator


class TTSRequest(BaseModel):
    text: str
    voice: str
    speed: float = 1.0
    format: str | None = None  # None => settings.default_format
    owner: str | None = None  # disambiguates an ambiguous shared voice name, by owner's email


class SpeechRequest(BaseModel):  # OpenAI /v1/audio/speech
    model: str = "f5_tts"
    input: str
    voice: str
    response_format: str = "mp3"  # OpenAI default; compact over the network
    speed: float = 1.0
    stream_format: str | None = None  # "audio" => chunked stream; else full
    owner: str | None = None  # disambiguates an ambiguous shared voice name, by owner's email


class VoiceInfo(BaseModel):
    name: str
    clips: int
    duration_s: float
    default_model: str
    kind: str = "cloned"  # "cloned" | "preset"
    description: str | None = None
    owner: str | None = None  # owner's email; None for presets and legacy (unowned) voices
    visibility: str | None = None  # "private" | "shared"; None for presets


class CloneResponse(BaseModel):
    voice: str
    clip_index: int
    transcript: str
    duration_s: float


class ClipInfo(BaseModel):
    index: int
    filename: str
    duration_s: float
    transcript: str
    created_at: str


class VoiceUpdate(BaseModel):
    """PATCH /v1/voices/{name}. Both optional, but at least one is required — an empty body
    is a caller mistake, and silently returning 200 would hide it."""

    name: str | None = None
    visibility: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.name is None and self.visibility is None:
            raise ValueError("provide name, visibility, or both")
        return self
