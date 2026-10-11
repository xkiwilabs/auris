from __future__ import annotations

import uvicorn

from auris.config import load_settings
from auris.server.app import create_app


def main() -> None:
    """Console-script entrypoint (`auris-server`). Single worker process: the GPU models
    load once and stay resident; concurrency comes from the in-process SynthesisPool."""
    settings = load_settings()
    uvicorn.run(create_app(), host=settings.bind_host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
