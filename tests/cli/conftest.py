from __future__ import annotations

import pytest


@pytest.fixture
def cli_data_dir(tmp_path, monkeypatch):
    """Point auris at an isolated tmp data dir for CLI tests (sets AURIS_DATA_DIR)."""
    monkeypatch.setenv("AURIS_DATA_DIR", str(tmp_path))
    return tmp_path
