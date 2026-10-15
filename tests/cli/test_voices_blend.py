"""`auris voices blend`: local (the box's unowned namespace) and remote (PUT …/blend)."""

from __future__ import annotations

import json
import types

import pytest
from typer.testing import CliRunner

from auris.cli.main import app
from auris.client.api import ClientError
from auris.voice_library import blends, speakers
from tests.cli.test_voices_ownership import _with_db

runner = CliRunner()


@pytest.fixture
def local(cli_data_dir, monkeypatch):
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: None)
    return cli_data_dir


def test_blend_creates_then_re_mixes_locally(local):
    r = runner.invoke(app, ["voices", "blend", "warm", "narrator-uk-f=60", "bright-uk-f=40"])
    assert r.exit_code == 0, r.output
    assert "created warm: 60% narrator-uk-f + 40% bright-uk-f (shared)" in r.output
    r = runner.invoke(app, ["voices", "blend", "warm", "narrator-uk-f=1", "deep-uk-m=3"])
    assert r.exit_code == 0, r.output
    assert "re-mixed warm: 25% narrator-uk-f + 75% deep-uk-m" in r.output
    (sp,) = _with_db(speakers.list_all)
    assert sp.is_blend and sp.owner_id is None
    assert [c.preset for c in blends.from_json(sp.blend).components] == [
        "narrator-uk-f",
        "deep-uk-m",
    ]


def test_the_listing_shows_the_recipe(local):
    runner.invoke(app, ["voices", "blend", "warm", "calm-m=1", "low-f=1"])
    r = runner.invoke(app, ["voices"])
    assert r.exit_code == 0, r.output
    assert "warm" in r.output and "blend: 50% calm-m + 50% low-f" in r.output


@pytest.mark.parametrize(
    "args, needle",
    [
        (["warm", "calm-m=1"], "not 1"),
        (["warm", "calm-m=1", "my-clone=1"], "not a built-in voice"),
        (["warm", "calm-m", "low-f=1"], "NAME=WEIGHT"),
        (["calm-m", "low-f=1", "soft-f=1"], "built-in preset name"),
        (["warm", "calm-m=1", "low-f=1", "--private"], "has no owner"),
    ],
)
def test_bad_blends_exit_1_with_the_reason(local, args, needle):
    r = runner.invoke(app, ["voices", "blend", *args])
    assert r.exit_code == 1
    assert needle in r.output
    assert _with_db(speakers.list_all) == []


def test_a_cloned_voice_is_not_turned_into_a_blend(local):
    _with_db(lambda c: speakers.create_speaker(c, "cloned"))
    r = runner.invoke(app, ["voices", "blend", "cloned", "calm-m=1", "low-f=1"])
    assert r.exit_code == 1 and "cloned voice" in r.output
    (sp,) = _with_db(speakers.list_all)
    assert not sp.is_blend


def test_owner_does_not_apply(local):
    r = runner.invoke(
        app, ["voices", "--owner", "bob@example.com", "blend", "warm", "calm-m=1", "low-f=1"]
    )
    assert r.exit_code == 1 and "--owner does not apply" in r.output


def test_refit_skips_blends(local, monkeypatch):
    """A blend has no recording to test, so refit must not even load f5 for one."""
    runner.invoke(app, ["voices", "blend", "warm", "calm-m=1", "low-f=1"])

    def _boom(settings):
        raise AssertionError("refit loaded a model for a blend")

    monkeypatch.setattr("auris.cli.commands.voices._load_probe_model", _boom)
    r = runner.invoke(app, ["voices", "refit"])
    assert r.exit_code == 0, r.output


def test_blend_goes_to_the_service_in_remote_mode(cli_data_dir, monkeypatch):
    seen = {}

    def _put_blend(name, components, visibility=None):
        seen.update(name=name, components=components, visibility=visibility)
        return {
            "name": name,
            "description": "Blend: 60% calm-m + 40% low-f",
            "visibility": "shared",
        }

    fake = types.SimpleNamespace(put_blend=_put_blend)
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, ["voices", "blend", "warm", "calm-m=60", "low-f=40", "--share"])
    assert r.exit_code == 0, r.output
    assert seen == {
        "name": "warm",
        "components": [("calm-m", 60.0), ("low-f", 40.0)],
        "visibility": "shared",
    }
    assert "warm: Blend: 60% calm-m + 40% low-f (shared)" in r.output
    assert _with_db(speakers.list_all) == []  # nothing written to the client's own library


def test_a_service_refusal_exits_1(cli_data_dir, monkeypatch):
    def _put_blend(name, components, visibility=None):
        raise ClientError("voice 'warm' belongs to someone else")

    fake = types.SimpleNamespace(put_blend=_put_blend)
    monkeypatch.setattr("auris.cli.commands.voices.remote_client", lambda: fake)
    r = runner.invoke(app, ["voices", "blend", "warm", "calm-m=1", "low-f=1"])
    assert r.exit_code == 1 and "belongs to someone else" in r.output


def test_client_put_blend_sends_the_expected_request():
    import httpx

    from auris.client.api import ServiceClient

    seen = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.update(method=request.method, url=str(request.url), body=json.loads(request.content))
        return httpx.Response(201, json={"name": "a#b", "visibility": "private"})

    sc = ServiceClient("http://svc", "tok")
    sc._http = httpx.Client(base_url="http://svc", transport=httpx.MockTransport(_handler))
    sc.put_blend("a#b", [("calm-m", 60.0), ("low-f", 40.0)])
    assert seen["method"] == "PUT"
    assert seen["url"] == "http://svc/v1/voices/a%23b/blend"
    assert seen["body"] == {
        "components": [{"voice": "calm-m", "weight": 60.0}, {"voice": "low-f", "weight": 40.0}]
    }


def test_record_onto_a_blend_is_refused_before_recording(local, monkeypatch):
    import auris.cli.commands.record as record_cmd

    runner.invoke(app, ["voices", "blend", "warm", "calm-m=1", "low-f=1"])
    monkeypatch.setattr(record_cmd, "remote_client", lambda: None)

    def _no_mic(*a, **k):
        raise AssertionError("asked for a recording that could never be stored")

    monkeypatch.setattr(record_cmd, "record_audio", _no_mic)
    r = runner.invoke(app, ["record", "warm"])
    assert r.exit_code == 1 and "blended voice" in r.output
