from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from auris.client.api import ClientError, ServiceClient


def _client(handler):
    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://svc",
        headers={"Authorization": "Bearer tok"},
    )
    return sc


def test_voices_sends_auth_and_parses():
    def handler(req):
        assert req.headers["authorization"] == "Bearer tok"
        assert req.url.path == "/v1/voices"
        return httpx.Response(200, json=[{"name": "narrator-uk-f", "kind": "preset"}])

    assert _client(handler).list_voices()[0]["name"] == "narrator-uk-f"


def test_tts_returns_bytes():
    def handler(req):
        assert req.url.path == "/v1/tts"
        return httpx.Response(200, content=b"MP3DATA")

    assert _client(handler).tts("narrator-uk-f", "hi") == b"MP3DATA"


def test_error_envelope_raises_clienterror():
    def handler(req):
        return httpx.Response(404, json={"error": {"code": "voice_not_found", "message": "nope"}})

    with pytest.raises(ClientError) as e:
        _client(handler).tts("ghost", "hi")
    assert "nope" in str(e.value)


def test_401_gives_token_hint():
    def handler(req):
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "no"}})

    with pytest.raises(ClientError) as e:
        _client(handler).list_voices()
    assert "config token" in str(e.value)


def test_clone_posts_multipart():
    def handler(req):
        assert req.url.path == "/v1/voices/Me/clone"
        assert b"WAVDATA" in req.content
        return httpx.Response(
            201, json={"voice": "Me", "clip_index": 0, "transcript": "hi", "duration_s": 1.0}
        )

    out = _client(handler).clone("Me", b"WAVDATA", "rec.wav")
    assert out["voice"] == "Me"


@pytest.mark.parametrize("name", ["a#b", "a?b", "a b", "a%b"])
def test_a_voice_name_is_percent_encoded_in_the_path(name):
    """`validate_name` allows everything but slashes and control characters, and httpx escapes
    none of it: `a#b` used to PATCH `/v1/voices/a` with `#b` dropped as a fragment, so
    `auris voices share 'a#b'` flipped the visibility of a voice named `a` and printed
    `a#b: shared`. A wrong-row write reported as success.
    """
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, json={"name": name, "visibility": "shared"})

    _client(handler).update_voice(name, visibility="shared")
    assert seen[0].raw_path.split(b"?")[0] == b"/v1/voices/" + quote(name, safe="").encode()
    # The decoded path is the whole name again, and nothing leaked into the query string.
    assert seen[0].path == f"/v1/voices/{name}"
    assert seen[0].query == b""


def test_clone_percent_encodes_the_voice_name_too():
    """Leaving one of the two encoded and the other not is worse than either."""
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(
            201, json={"voice": "a#b", "clip_index": 0, "transcript": "hi", "duration_s": 1.0}
        )

    _client(handler).clone("a#b", b"WAVDATA")
    assert seen[0].raw_path == b"/v1/voices/a%23b/clone"
