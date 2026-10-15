from __future__ import annotations

from collections.abc import Iterator
from urllib.parse import quote

import httpx

# Above the server's AURIS_ASR_TIMEOUT_S default (600s) plus headroom for the upload itself, so a
# long recording doesn't get cut off client-side while the server is still decoding it. Scoped to
# transcribe() only — the constructor's default timeout still governs every other call.
_TRANSCRIBE_TIMEOUT_S = 660.0


class ClientError(Exception):
    """A service call failed; the message is safe to show the user."""


class ServiceClient:
    """Thin wrapper over the auris HTTP service (base URL + bearer token)."""

    def __init__(self, base_url: str, token: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.is_success:
            return r
        if r.status_code == 401:
            raise ClientError("token rejected — set it with `auris config token`")
        msg = r.reason_phrase
        try:
            msg = r.json()["error"]["message"]
        except Exception:  # noqa: BLE001 — fall back to the status text
            pass
        raise ClientError(msg)

    @staticmethod
    def _segment(name: str) -> str:
        """Percent-encode a voice name for use as a path segment.

        `speakers.validate_name` forbids only slashes and control characters, so `#` and `?` are
        legal in a voice name and httpx escapes neither: `a#b` would PATCH `/v1/voices/a` with
        `#b` dropped as a fragment, and `a?b` would send `?b` as a query string. On a caller who
        also owns a voice named `a` that is a write to the *wrong row* reported as success.
        `safe=""` so `/` encodes too, matching the dashboard, which already encodes every one of
        its ten voice paths.
        """
        return quote(name, safe="")

    def ready(self) -> bool:
        try:
            return self._http.get("/ready").is_success
        except httpx.HTTPError:
            return False

    def list_voices(self) -> list[dict]:
        return self._check(self._http.get("/v1/voices")).json()

    def tts(
        self, voice: str, text: str, speed: float = 1.0, fmt: str = "mp3", owner: str | None = None
    ) -> bytes:
        body = {"voice": voice, "text": text, "speed": speed, "format": fmt}
        if owner is not None:
            body["owner"] = owner
        r = self._http.post("/v1/tts", json=body)
        return self._check(r).content

    def stream(
        self, voice: str, text: str, speed: float = 1.0, owner: str | None = None
    ) -> Iterator[bytes]:
        body = {"voice": voice, "text": text, "speed": speed}
        if owner is not None:
            body["owner"] = owner
        with self._http.stream("POST", "/v1/tts/stream", json=body) as r:
            self._check(r)
            yield from r.iter_bytes()

    def update_voice(
        self, name: str, *, visibility: str | None = None, owner: str | None = None
    ) -> dict:
        """PATCH a voice's mutable fields. Only `visibility` is wired up so far (`share`/
        `unshare`'s remote path); `VoiceUpdate` on the server also accepts `name`.

        `owner` (an email) goes on the query string, exactly as `?owner=` already works for
        every other voice route — the server resolves it and applies the same disclosure rule
        (an unknown address comes back 404, not a distinguishing error).
        """
        body = {"visibility": visibility} if visibility is not None else {}
        params = {"owner": owner} if owner is not None else None
        r = self._http.patch(f"/v1/voices/{self._segment(name)}", json=body, params=params)
        return self._check(r).json()

    def put_blend(
        self, name: str, components: list[tuple[str, float]], visibility: str | None = None
    ) -> dict:
        """PUT a blend recipe: create the caller's blended voice `name`, or re-mix it."""
        body: dict = {"components": [{"voice": v, "weight": w} for v, w in components]}
        if visibility is not None:
            body["visibility"] = visibility
        r = self._http.put(f"/v1/voices/{self._segment(name)}/blend", json=body)
        return self._check(r).json()

    def clone(
        self,
        name: str,
        audio_bytes: bytes,
        filename: str = "clip.wav",
        ref_text: str | None = None,
        asr_model: str | None = None,
    ) -> dict:
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        data = {k: v for k, v in (("ref_text", ref_text), ("asr_model", asr_model)) if v}
        r = self._http.post(
            f"/v1/voices/{self._segment(name)}/clone", files=files, data=data or None
        )
        return self._check(r).json()

    def asr_models(self) -> dict:
        """The STT models this service accepts, and which one is its default."""
        return self._check(self._http.get("/v1/audio/models")).json()

    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        *,
        model: str | None = None,
        language: str | None = None,
        task: str = "transcribe",
        response_format: str = "json",
        prompt: str | None = None,
        word_timestamps: bool = False,
    ) -> str | dict:
        """Transcribe audio on the service. Returns a dict for json/verbose_json and a
        plain string for text/srt/vtt."""
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        data: dict[str, str] = {"task": task, "response_format": response_format}
        for key, value in (("model", model), ("language", language), ("prompt", prompt)):
            if value:
                data[key] = value
        if word_timestamps:
            data["timestamp_granularities[]"] = "word"
        r = self._check(
            self._http.post(
                "/v1/audio/transcriptions",
                files=files,
                data=data,
                timeout=_TRANSCRIBE_TIMEOUT_S,
            )
        )
        if response_format in ("json", "verbose_json"):
            return r.json()
        return r.text
