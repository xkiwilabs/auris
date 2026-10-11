from __future__ import annotations

import json
import types

import pytest

from typer.testing import CliRunner

from auris.cli.main import app

runner = CliRunner()


def test_voices_remote(monkeypatch):
    fake = types.SimpleNamespace(
        list_voices=lambda: [
            {
                "name": "narrator-uk-f",
                "kind": "preset",
                "clips": 0,
                "duration_s": 0.0,
                "default_model": "kokoro",
                "description": "UK English, female",
            },
            {
                "name": "Me",
                "kind": "cloned",
                "clips": 2,
                "duration_s": 14.0,
                "default_model": "f5_tts",
                "description": None,
            },
        ]
    )
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    out = runner.invoke(app, ["voices"]).output
    assert "narrator-uk-f" in out and "Me" in out


def test_tts_remote_writes_bytes(monkeypatch, tmp_path):
    fake = types.SimpleNamespace(
        tts=lambda voice, text, speed=1.0, fmt="mp3", owner=None: b"MP3DATA"
    )
    monkeypatch.setattr("auris.cli.commands.tts.remote_client", lambda: fake)
    out = tmp_path / "x.mp3"
    r = runner.invoke(app, ["tts", "narrator-uk-f", "--text", "hi", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.read_bytes() == b"MP3DATA"


def test_record_remote_uploads_wav(monkeypatch):
    import numpy as np

    captured = {}

    def _clone(name, audio_bytes, filename="clip.wav", ref_text=None, asr_model=None):
        captured["name"] = name
        captured["bytes"] = audio_bytes
        return {"voice": name, "clip_index": 0, "transcript": "hi", "duration_s": 4.0}

    fake = types.SimpleNamespace(clone=_clone)
    monkeypatch.setattr("auris.cli.commands.record.remote_client", lambda: fake)
    monkeypatch.setattr(
        "auris.cli.commands.record.record_audio",
        lambda seconds, rate, on_tick=None: np.zeros(int(rate * 4), dtype=np.float32),
    )
    r = runner.invoke(app, ["record", "Me", "--seconds", "4", "--free"])
    assert r.exit_code == 0, r.output
    assert captured["name"] == "Me"
    assert captured["bytes"][:4] == b"RIFF"  # uploaded a WAV; server transcribes/stores


def test_clone_remote_uploads_file(monkeypatch, tmp_path):
    captured = {}

    def _clone(name, audio_bytes, filename="clip.wav", ref_text=None, asr_model=None):
        captured["name"] = name
        captured["bytes"] = audio_bytes
        return {"voice": name, "clip_index": 1, "transcript": "hi", "duration_s": 4.0}

    monkeypatch.setattr(
        "auris.cli.commands.clone.remote_client", lambda: types.SimpleNamespace(clone=_clone)
    )
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFFfakewav")
    r = runner.invoke(app, ["clone", "Me", "--ref", str(ref)])
    assert r.exit_code == 0, r.output
    assert captured["name"] == "Me" and captured["bytes"] == b"RIFFfakewav"


def test_info_shows_remote_mode(monkeypatch):
    monkeypatch.setattr(
        "auris.cli.commands.info.load_client_config",
        lambda: {"base_url": "http://h:8011", "token": "t"},
    )
    out = runner.invoke(app, ["info"]).output
    assert "remote" in out and "http://h:8011" in out


def test_tts_local_when_not_configured(monkeypatch, tmp_path):
    # remote_client returns None => local path (monkeypatched build_model) runs.
    import numpy as np

    monkeypatch.setattr("auris.cli.commands.tts.remote_client", lambda: None)

    class _Fake:
        sample_rate = 24000

        def synthesise(self, text, conditioning, speed=1.0):
            return np.full(24000, 0.1, dtype=np.float32)

    monkeypatch.setattr("auris.cli.commands.tts.build_model", lambda name, settings: _Fake())

    async def _fake_resolve(settings, name, owner=None):
        return types.SimpleNamespace(model="kokoro", conditioning=object())

    monkeypatch.setattr("auris.cli.commands.tts.resolve_voice_standalone", _fake_resolve)
    out = tmp_path / "x.wav"
    r = runner.invoke(app, ["tts", "narrator-uk-f", "--text", "hi", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert out.exists()


def _multipart_fields(request) -> dict:
    """Parse a multipart/form-data request body into {field_name: value}, so tests can assert a
    value is attached to the *correct* field rather than merely present somewhere in the body.
    For the file part, the value is its filename (from Content-Disposition), not the raw bytes."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    fields = {}
    for part in request.content.split(b"--" + boundary):
        if b"Content-Disposition" not in part or b'name="' not in part:
            continue
        header, _, body = part.partition(b"\r\n\r\n")
        name = header.split(b'name="')[1].split(b'"')[0].decode()
        if b'filename="' in header:
            fields[name] = header.split(b'filename="')[1].split(b'"')[0].decode()
        else:
            fields[name] = body.rstrip(b"\r\n").decode(errors="replace")
    return fields


def test_client_transcribe_sends_the_expected_multipart(monkeypatch):
    import httpx

    from auris.client.api import ServiceClient

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["fields"] = _multipart_fields(request)
        return httpx.Response(200, json={"text": "hello"})

    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(
        base_url="http://svc",
        transport=httpx.MockTransport(_handler),
        headers={"Authorization": "Bearer tok"},
    )

    out = sc.transcribe(b"RIFFfake", "clip.wav", model="small.en", response_format="json")
    assert out == {"text": "hello"}
    assert seen["url"].endswith("/v1/audio/transcriptions")
    # values must land on the correct field, not merely appear somewhere in the body
    assert seen["fields"]["model"] == "small.en"
    assert seen["fields"]["file"] == "clip.wav"
    assert seen["fields"]["task"] == "transcribe"
    assert seen["fields"]["response_format"] == "json"
    # unset optional fields must be omitted from the wire, not sent as "" or "None"
    assert "language" not in seen["fields"]
    assert "prompt" not in seen["fields"]
    assert "timestamp_granularities[]" not in seen["fields"]


def test_client_transcribe_returns_text_for_srt(monkeypatch):
    import httpx

    from auris.client.api import ServiceClient

    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(
        base_url="http://svc",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="1\n...")),
        headers={"Authorization": "Bearer tok"},
    )
    assert sc.transcribe(b"x", "a.wav", response_format="srt").startswith("1\n")


def test_client_transcribe_word_timestamps_sets_granularity_field(monkeypatch):
    import httpx

    from auris.client.api import ServiceClient

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["fields"] = _multipart_fields(request)
        return httpx.Response(200, json={"text": "hello"})

    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(
        base_url="http://svc",
        transport=httpx.MockTransport(_handler),
        headers={"Authorization": "Bearer tok"},
    )
    sc.transcribe(b"x", "a.wav", word_timestamps=True)
    assert seen["fields"]["timestamp_granularities[]"] == "word"


def test_client_transcribe_uses_a_long_timeout(monkeypatch):
    import httpx

    from auris.client.api import ServiceClient

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"text": "hello"})

    sc = ServiceClient("http://svc", "tok")  # constructor default timeout is 120s
    sc._http = httpx.Client(
        base_url="http://svc",
        transport=httpx.MockTransport(_handler),
        headers={"Authorization": "Bearer tok"},
    )
    sc.transcribe(b"x", "a.wav")
    # comfortably above the server's 600s asr_timeout_s default, and above the client's own
    # 120s constructor default — a long recording must not be cut off client-side.
    assert seen["timeout"]["read"] > 600


# --- fix round 4: the remote share/unshare seam -------------------------------------------
#
# This is the seam where a signature change silently turns location-transparency off: nothing
# used to pin `update_voice`'s name, body, `?owner=` or that `--owner` reaches it at all. Tests
# here would have caught both of this round's remote-mode defects on their own.


def _fake_patcher(info=None, raises=None):
    """A ServiceClient stand-in recording the one call share/unshare make."""
    seen = {}

    def _update_voice(name, *, visibility=None, owner=None):
        seen["name"] = name
        seen["visibility"] = visibility
        seen["owner"] = owner
        if raises is not None:
            raise raises
        # An owner by default, so only the test that asks for `owner: None` exercises the
        # unowned-voice note.
        return info or {
            "name": name,
            "visibility": visibility,
            "owner": owner or "owner@example.com",
        }

    return types.SimpleNamespace(update_voice=_update_voice), seen


def test_remote_share_patches_visibility_shared(monkeypatch):
    fake, seen = _fake_patcher()
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, ["voices", "share", "narrator"])
    assert r.exit_code == 0, r.output
    assert seen == {"name": "narrator", "visibility": "shared", "owner": None}
    assert "narrator: shared" in r.output


def test_remote_unshare_patches_visibility_private(monkeypatch):
    fake, seen = _fake_patcher()
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, ["voices", "unshare", "narrator"])
    assert r.exit_code == 0, r.output
    assert seen == {"name": "narrator", "visibility": "private", "owner": None}


@pytest.mark.parametrize(
    "argv",
    [
        ["voices", "--owner", "bob@example.com", "share", "narrator"],
        ["voices", "share", "narrator", "--owner", "bob@example.com"],
    ],
    ids=["owner-before-the-subcommand", "owner-after-the-subcommand"],
)
def test_remote_share_forwards_owner_from_either_position(monkeypatch, argv):
    """`--owner` must reach the service from both positions, or remote mode disambiguates a
    different account's voice than the same command would on the box."""
    fake, seen = _fake_patcher()
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, argv)
    assert r.exit_code == 0, r.output
    assert seen["owner"] == "bob@example.com"


def test_remote_unshare_surfaces_the_servers_refusal(monkeypatch):
    """The server now refuses `private` on an unowned voice with 422 (voice_access.
    set_visibility). Before that, remote mode printed `legacy: private`, exit 0, for a change
    the server had not made — while the same command on the box refused."""
    from auris.client.api import ClientError

    fake, seen = _fake_patcher(
        raises=ClientError(
            "'legacy' has no owner, so it is visible to every account no matter what its "
            "visibility is set to. Run 'auris voices adopt <email>' ..."
        )
    )
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, ["voices", "unshare", "legacy"])
    assert r.exit_code == 1
    assert "no owner" in r.output and "adopt" in r.output
    assert seen["visibility"] == "private"


def test_client_update_voice_sends_the_expected_patch(monkeypatch):
    """Pins the wire shape itself: path, JSON body and `?owner=` on the query string (not in
    the body, where the server does not read it)."""
    import httpx

    from auris.client.api import ServiceClient

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"name": "narrator", "visibility": "shared"})

    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(
        base_url="http://svc",
        transport=httpx.MockTransport(_handler),
        headers={"Authorization": "Bearer tok"},
    )
    out = sc.update_voice("narrator", visibility="shared", owner="bob@example.com")
    assert out["visibility"] == "shared"
    assert seen["method"] == "PATCH"
    assert seen["url"] == "http://svc/v1/voices/narrator?owner=bob%40example.com"
    assert seen["body"] == {"visibility": "shared"}


def test_client_update_voice_omits_an_absent_owner(monkeypatch):
    import httpx

    from auris.client.api import ServiceClient

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"name": "n", "visibility": "private"})

    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(
        base_url="http://svc",
        transport=httpx.MockTransport(_handler),
        headers={"Authorization": "Bearer tok"},
    )
    sc.update_voice("n", visibility="private")
    assert seen["url"] == "http://svc/v1/voices/n"  # no empty ?owner=


# --- fix round 4: the remote listing filter ----------------------------------------------


def _fake_lister(owner_email):
    return types.SimpleNamespace(
        list_voices=lambda: [
            {"name": "narrator-uk-f", "kind": "preset", "clips": 0, "duration_s": 0.0},
            {
                "name": "alice-voice",
                "kind": "cloned",
                "clips": 1,
                "duration_s": 4.0,
                "owner": owner_email,
                "visibility": "private",
            },
        ]
    )


def test_remote_owner_filter_is_case_insensitive(monkeypatch):
    """Local mode matches `COLLATE NOCASE`, and `users.validate_email` only strips, so stored
    addresses can differ in case from what is typed. A plain `==` made this return nothing
    remotely while the same command worked on the box."""
    monkeypatch.setattr(
        "auris.cli.commands.voices.remote_client", lambda: _fake_lister("alice@example.com")
    )
    r = runner.invoke(app, ["voices", "--owner", "Alice@Example.COM"])
    assert r.exit_code == 0, r.output
    assert "alice-voice" in r.output
    assert "narrator-uk-f" in r.output  # presets still listed, as locally


def test_remote_owner_filter_excludes_other_owners(monkeypatch):
    monkeypatch.setattr(
        "auris.cli.commands.voices.remote_client", lambda: _fake_lister("bob@example.com")
    )
    r = runner.invoke(app, ["voices", "--owner", "alice@example.com"])
    assert r.exit_code == 0, r.output
    assert "alice-voice" not in r.output
    assert "no voices visible for 'alice@example.com'" in r.output


def test_remote_owner_filter_with_no_match_names_the_address_and_exits_0(monkeypatch):
    """It used to print `(none yet — run 'auris clone')`, which is wrong twice: voices do exist,
    and cloning would not help. Naming the address back is the fix — but the exit code stays 0,
    and the message makes no claim about whether the account exists. Over HTTP it cannot:
    `/v1/voices` exposes only the owners of *visible* voices, so "no such account" and "nothing
    of theirs is visible to you" must read identically, or the exit code would disclose which
    addresses are registered. A legitimate account with no voices is not an error either."""
    monkeypatch.setattr(
        "auris.cli.commands.voices.remote_client", lambda: _fake_lister("bob@example.com")
    )
    r = runner.invoke(app, ["voices", "--owner", "nobody@example.com"])
    assert r.exit_code == 0, r.output
    assert "no voices visible for 'nobody@example.com'" in r.output
    assert "none yet" not in r.output
    assert "users add" not in r.output  # that hint would be a guess about account existence
    assert "narrator-uk-f" in r.output  # presets still listed


def test_remote_owner_filter_cannot_distinguish_unknown_from_empty(monkeypatch):
    """The non-disclosure property, stated as a test: an address the service has never heard of
    and one whose owner simply has nothing visible produce byte-identical output."""
    monkeypatch.setattr(
        "auris.cli.commands.voices.remote_client", lambda: _fake_lister("bob@example.com")
    )
    unknown = runner.invoke(app, ["voices", "--owner", "nobody@example.com"])
    monkeypatch.setattr(
        "auris.cli.commands.voices.remote_client",
        lambda: types.SimpleNamespace(
            list_voices=lambda: [
                {"name": "narrator-uk-f", "kind": "preset", "clips": 0, "duration_s": 0.0}
            ]
        ),
    )
    empty = runner.invoke(app, ["voices", "--owner", "nobody@example.com"])
    assert unknown.exit_code == empty.exit_code == 0
    assert unknown.output == empty.output


def test_remote_share_of_an_unowned_voice_prints_the_same_note_as_local(monkeypatch):
    """`share` on an unowned voice is allowed but inert, and local mode says so. The PATCH
    response carries `owner`, so remote mode has no excuse to stay silent about it."""
    fake, _seen = _fake_patcher(
        info={"name": "legacy", "visibility": "shared", "owner": None},
    )
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, ["voices", "share", "legacy"])
    assert r.exit_code == 0, r.output
    assert "already visible to every account" in r.output
