from __future__ import annotations

import structlog


def _configure() -> None:
    """Pin structlog's exception rendering so tracebacks never print local variables.

    structlog's *default* console renderer formats exceptions with
    `RichTracebackFormatter(show_locals=True)`. That is a credential leak here: an unhandled
    error anywhere under `auris.server.auth` puts `AURIS_AUTH_TOKEN` — which is both the admin
    credential and the session-cookie HMAC key — into the log in plaintext, and an error in the
    DB layer would do the same for an API key. Locals are off for every entry point, not just
    the server, because the CLI handles the same secrets.
    """
    if structlog.is_configured():  # an embedder's explicit configuration wins
        return
    processors = list(structlog.get_config()["processors"])
    if processors and isinstance(processors[-1], structlog.dev.ConsoleRenderer):
        try:
            formatter = structlog.dev.RichTracebackFormatter(show_locals=False)
        except Exception:  # noqa: BLE001 — rich missing or changed; plain never shows locals
            formatter = structlog.dev.plain_traceback
        processors[-1] = structlog.dev.ConsoleRenderer(exception_formatter=formatter)
    structlog.configure(processors=processors)


_configure()


def get_logger(name: str = "auris") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
