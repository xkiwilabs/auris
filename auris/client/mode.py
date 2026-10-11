from __future__ import annotations

from auris.client.api import ServiceClient
from auris.client.config import load_client_config


def remote_client() -> ServiceClient | None:
    """Return a ServiceClient if a service URL is configured, else None (use the local model)."""
    c = load_client_config()
    if not c["base_url"]:
        return None
    return ServiceClient(c["base_url"], c["token"])
