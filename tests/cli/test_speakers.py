from __future__ import annotations

import asyncio

from typer.testing import CliRunner

from auris.cli.main import app
from auris.config import load_settings
from auris.voice_library import db, speakers

runner = CliRunner()


def test_speakers_add_creates_speaker(cli_data_dir):
    result = runner.invoke(app, ["speakers", "add", "Me"])
    assert result.exit_code == 0, result.output
    assert "Me" in result.output

    # the speaker was actually persisted to the db (catches a dropped commit)
    async def _fetch():
        conn = await db.connect(load_settings().db_path)
        try:
            return await speakers.get_speaker_by_name_and_owner(conn, "Me", None)
        finally:
            await conn.close()

    sp = asyncio.run(_fetch())
    assert sp is not None
    assert sp.name == "Me"
