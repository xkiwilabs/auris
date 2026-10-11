from __future__ import annotations

import os

from auris.runtime import ensure_valid_hashseed


def test_empty_hashseed_is_reset(monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "")
    ensure_valid_hashseed()
    assert os.environ["PYTHONHASHSEED"] == "0"


def test_garbage_hashseed_is_reset(monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "not-a-number")
    ensure_valid_hashseed()
    assert os.environ["PYTHONHASHSEED"] == "0"


def test_valid_integer_hashseed_is_kept(monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "42")
    ensure_valid_hashseed()
    assert os.environ["PYTHONHASHSEED"] == "42"


def test_random_hashseed_is_kept(monkeypatch):
    monkeypatch.setenv("PYTHONHASHSEED", "random")
    ensure_valid_hashseed()
    assert os.environ["PYTHONHASHSEED"] == "random"


def test_unset_hashseed_is_left_unset(monkeypatch):
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    ensure_valid_hashseed()
    assert "PYTHONHASHSEED" not in os.environ
