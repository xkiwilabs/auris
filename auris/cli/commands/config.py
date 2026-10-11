from __future__ import annotations

import sys

import typer

from auris.client.config import load_client_config, mask, save_client_config

config_app = typer.Typer(help="Configure the remote service this client talks to.")


def _read_secret(prompt: str) -> str:
    """Read a token without exposing it on the command line: from stdin if piped (e.g.
    `auris config token < tokenfile`), otherwise a hidden interactive prompt."""
    if not sys.stdin.isatty():
        token = sys.stdin.readline().strip()
        if not token:
            typer.echo("no token provided on stdin", err=True)
            raise typer.Exit(code=2)
        return token
    return typer.prompt(prompt, hide_input=True)


@config_app.command("set")
def set_config(
    url: str = typer.Option(..., "--url", help="Service base URL, e.g. http://host:8011"),
) -> None:
    """Set the service URL. (Set the token with `auris config token`.)"""
    path = save_client_config(base_url=url)
    typer.echo(f"Saved {path}")


@config_app.command("token")
def set_token() -> None:
    """Set or update the auth token (hidden prompt, or piped via stdin). Can be done any time.

    For automation, set the AURIS_AUTH_TOKEN environment variable instead — it takes precedence
    and isn't written to disk."""
    save_client_config(token=_read_secret("Auth token"))
    typer.echo("Token saved.")


@config_app.command("show")
def show() -> None:
    """Show the saved service URL and a masked token."""
    c = load_client_config()
    typer.echo(f"service URL: {c['base_url'] or '(none)'}")
    typer.echo(f"token:       {mask(c['token'])}")


@config_app.command("clear")
def clear() -> None:
    """Forget the saved service URL and token."""
    save_client_config(base_url="", token="")
    typer.echo("Cleared.")


def _interactive() -> bool:
    return sys.stdin.isatty()


def login(
    url: str = typer.Option(None, "--url", help="Service base URL (prompts if omitted)"),
    no_token: bool = typer.Option(
        False, "--no-token", help="Only set the URL; add an API key later with `auris config token`"
    ),
) -> None:
    """Point this client at a remote auris service: its URL, plus an optional API key.

    `auris dashboard` needs only the URL — you sign in on the page. The API key is for the
    command-line tools (`auris voices`, `tts`, `record`, `transcribe`)."""
    current = load_client_config()
    if url is None:
        url = typer.prompt("Service URL", default=current["base_url"] or "http://127.0.0.1:8011")
    token = ""
    if not no_token:
        # Hidden prompt or stdin only — a token never goes on the command line.
        if _interactive():
            token = typer.prompt(
                "API key for the command-line tools (the dashboard doesn't need one; "
                "Enter to skip)",
                default="",
                show_default=False,
                hide_input=True,
            )
        else:
            token = sys.stdin.readline().strip()
    save_client_config(base_url=url, token=token or None)

    sc = None
    if token:
        from auris.client.api import ServiceClient

        sc = ServiceClient(url, token)
    if sc is not None and sc.ready():
        typer.echo(f"Connected to {url.rstrip('/')} ✓")
    elif token:
        typer.echo(f"Saved {url} — but couldn't reach it yet (check the URL / your VPN).")
    else:
        typer.echo(
            f"Saved {url}. That's all `auris dashboard` needs. For the command-line tools, add an "
            "API key (Account → My API keys in the dashboard) with: auris config token"
        )
