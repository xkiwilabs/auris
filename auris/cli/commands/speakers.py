from __future__ import annotations

import asyncio

import typer

from auris.config import load_settings
from auris.voice_library import db, speakers

app = typer.Typer(help="Manage speaker profiles.")


async def _add(name: str) -> speakers.Speaker:
    settings = load_settings()
    conn = await db.connect(settings.db_path)
    try:
        await db.create_tables(conn)
        return await speakers.create_speaker(conn, name=name)
    finally:
        await conn.close()


@app.command("add")
def add(name: str = typer.Argument(..., help="Display name for the speaker")) -> None:
    """Create a new speaker profile."""
    try:
        speaker = asyncio.run(_add(name))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Created speaker {speaker.name} ({speaker.id})")
