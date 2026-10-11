from __future__ import annotations

from auris.audio.transcript import Transcript

FORMATS: tuple[str, ...] = ("json", "verbose_json", "text", "srt", "vtt")

_JSON = "application/json"
_TEXT = "text/plain; charset=utf-8"


def _clock(seconds: float, sep: str) -> str:
    """HH:MM:SS<sep>mmm — `,` for SubRip, `.` for WebVTT. Hours are not wrapped, so a
    90-minute recording formats as 01:30:00 rather than rolling over."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def to_srt(transcript: Transcript) -> str:
    blocks = [
        f"{i}\n{_clock(s.start, ',')} --> {_clock(s.end, ',')}\n{s.text}\n"
        for i, s in enumerate(transcript.segments, start=1)
    ]
    return "\n".join(blocks)


def to_vtt(transcript: Transcript) -> str:
    blocks = [
        f"{_clock(s.start, '.')} --> {_clock(s.end, '.')}\n{s.text}\n" for s in transcript.segments
    ]
    return "WEBVTT\n\n" + "\n".join(blocks)


def to_verbose_json(transcript: Transcript, word_timestamps: bool, task: str) -> dict:
    body: dict = {
        "task": task,
        "language": transcript.language,
        "language_probability": transcript.language_probability,
        "duration": transcript.duration_s,
        "text": transcript.text,
        "segments": [
            {
                "id": s.id,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "no_speech_prob": s.no_speech_prob,
            }
            for s in transcript.segments
        ],
    }
    if word_timestamps:
        body["words"] = [
            {"word": w.word, "start": w.start, "end": w.end}
            for s in transcript.segments
            for w in s.words
        ]
    return body


def render(
    transcript: Transcript,
    response_format: str,
    word_timestamps: bool,
    task: str = "transcribe",
) -> tuple[str | dict, str]:
    """Return (body, content_type). A dict body is handed to FastAPI as JSON; a str body
    is returned as a plain-text Response. `task` is the decode task the caller asked for
    ("transcribe" | "translate"); verbose_json echoes it back, so pass the real one."""
    if response_format not in FORMATS:
        raise ValueError(f"response_format must be one of {', '.join(FORMATS)}")
    if response_format == "json":
        return {"text": transcript.text}, _JSON
    if response_format == "verbose_json":
        return to_verbose_json(transcript, word_timestamps, task), _JSON
    if response_format == "text":
        return transcript.text, _TEXT
    if response_format == "srt":
        return to_srt(transcript), _TEXT
    return to_vtt(transcript), _TEXT
