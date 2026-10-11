from __future__ import annotations

from auris.log import get_logger


def test_get_logger_returns_a_logger():
    log = get_logger()
    assert log is not None
    # should be usable without raising
    log.info("smoke_test_event", value=1)
