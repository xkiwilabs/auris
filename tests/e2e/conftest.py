"""Session-scoped fixtures for the e2e suite: one real server for the whole run.

Model loading costs ~40 s, so it is paid once. A test that needs a differently-configured
server takes its own `instance(...)` context manager instead of these fixtures.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from tests.e2e.server import Instance, instance


@pytest.fixture(scope="session")
def server() -> Iterator[Instance]:
    with instance() as inst:
        yield inst


@pytest.fixture(scope="session")
def admin(server: Instance) -> Iterator[httpx.Client]:
    """Authenticated as the master token (the `admin` principal)."""
    with server.client() as c:
        yield c


@pytest.fixture(scope="session")
def as_key(server: Instance) -> Iterator[httpx.Client]:
    """Authenticated as the seeded named API key (NOT admin)."""
    with server.client(server.key) as c:
        yield c
