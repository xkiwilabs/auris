from __future__ import annotations

import typer

from auris.client.config import load_client_config
from auris.client.proxy import serve_dashboard


def dashboard(
    port: int = typer.Option(8765, "--port", help="Local port to serve the dashboard on"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't open a browser automatically"),
) -> None:
    """Open the dashboard locally (served at http://localhost), proxied to the remote service.

    Because it's served from localhost — a secure context — the in-browser microphone works. You
    sign in on the page itself, with your email and password, just as on the server; the proxy
    holds no credential and asks for nothing here."""
    c = load_client_config()
    if not c["base_url"]:
        typer.echo("No service configured. Run `auris login` first.", err=True)
        raise typer.Exit(code=1)
    try:
        serve_dashboard(c["base_url"], port=port, open_browser=not no_open)
    except OSError as exc:
        typer.echo(f"Could not start the dashboard on port {port}: {exc}", err=True)
        raise typer.Exit(code=1)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
