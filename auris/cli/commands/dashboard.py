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
    sign in with your email and password; the proxy holds the resulting session cookie and injects
    it into every forwarded request, so neither ever reaches the page. The password is held in
    memory for the life of this process only — it is never written to disk, logged, or cached.

    Never pass a password on the command line; this command always prompts for it."""
    c = load_client_config()
    if not c["base_url"]:
        typer.echo("No service configured. Run `auris login` first.", err=True)
        raise typer.Exit(code=1)
    email = c.get("email") or typer.prompt("Email")
    password = typer.prompt("Password", hide_input=True)
    try:
        serve_dashboard(c["base_url"], email, password, port=port, open_browser=not no_open)
    except OSError as exc:
        typer.echo(f"Could not start the dashboard on port {port}: {exc}", err=True)
        raise typer.Exit(code=1)
    except RuntimeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
