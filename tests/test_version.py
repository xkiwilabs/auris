from __future__ import annotations

import auris


def test_version_is_exposed():
    assert auris.__version__ == "0.1.0"
