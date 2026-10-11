"""Tests for the e2e suite's own pure helpers.

These live in the *fast* suite on purpose: a broken WER function or a launcher that silently
inherits the ambient environment would make every e2e result untrustworthy, and neither needs
a GPU to check.
"""

from __future__ import annotations

import pytest

from tests.e2e.audio import normalise, wer
from tests.e2e.server import build_env, free_port


def test_normalise_strips_case_and_punctuation():
    assert normalise("  Hello, THERE!  ") == ["hello", "there"]
    assert normalise("It's fine — really.") == ["its", "fine", "really"]


@pytest.mark.parametrize(
    "ref, hyp, expected",
    [
        ("one two three four five", "one two three four five", 0.0),
        ("one two three four five", "one two THREE four five", 0.0),  # case-insensitive
        ("one two three four five", "one two ten four five", 0.2),  # one substitution
        ("one two three four five", "one two four five", 0.2),  # one deletion
        ("one two three four five", "one two three four five six", 0.2),  # one insertion
        ("one two three", "", 1.0),
    ],
)
def test_wer_counts_edits_over_reference_length(ref, hyp, expected):
    assert wer(ref, hyp) == pytest.approx(expected)


def test_wer_of_empty_reference_is_zero_only_when_hypothesis_is_empty():
    assert wer("", "") == 0.0
    assert wer("", "spurious words") == 1.0


def test_build_env_is_explicit_and_pins_the_hash_seed(tmp_path):
    env = build_env(port=9999, data_dir=tmp_path, token="tok", overrides={})
    assert env["AURIS_DATA_DIR"] == str(tmp_path)
    assert env["AURIS_BIND_HOST"] == "127.0.0.1"  # never 0.0.0.0
    assert env["AURIS_PORT"] == "9999"
    assert env["AURIS_AUTH_TOKEN"] == "tok"
    assert env["PYTHONHASHSEED"] == "0"  # an empty one crashes f5-TTS's spawned workers
    assert env["AURIS_GPU_WORKERS"] == "1" and env["AURIS_QUEUE_MAXSIZE"] == "1"


def test_build_env_does_not_inherit_the_ambient_environment(tmp_path, monkeypatch):
    """The whole point of building it explicitly: a stray exported AURIS_* or an unrelated
    variable in the developer's shell must not reach the server under test."""
    monkeypatch.setenv("AURIS_DEFAULT_FORMAT", "opus")
    monkeypatch.setenv("SOME_UNRELATED_VARIABLE", "leaked")
    env = build_env(port=1, data_dir=tmp_path, token="t", overrides={})
    assert "AURIS_DEFAULT_FORMAT" not in env
    assert "SOME_UNRELATED_VARIABLE" not in env


def test_build_env_overrides_win(tmp_path):
    env = build_env(port=1, data_dir=tmp_path, token="t", overrides={"AURIS_TTS_ENABLED": "false"})
    assert env["AURIS_TTS_ENABLED"] == "false"
    env2 = build_env(port=1, data_dir=tmp_path, token="t", overrides={"AURIS_ASR_MODEL": "tiny.en"})
    assert env2["AURIS_ASR_MODEL"] == "tiny.en"


def test_free_port_returns_a_bindable_port():
    import socket

    port = free_port()
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))  # still free: raises OSError if not
