from __future__ import annotations

import typer

from auris.cli.commands import asr as asr_cmd
from auris.cli.commands import clone as clone_cmd
from auris.cli.commands import info as info_cmd
from auris.cli.commands import keys as keys_cmd
from auris.cli.commands import play as play_cmd
from auris.cli.commands import record as record_cmd
from auris.cli.commands import speakers as speakers_cmd
from auris.cli.commands import say as say_cmd
from auris.cli.commands import config as config_cmd
from auris.cli.commands import dashboard as dashboard_cmd
from auris.cli.commands import transcribe as transcribe_cmd
from auris.cli.commands import tts as tts_cmd
from auris.cli.commands import users as users_cmd
from auris.cli.commands import voices as voices_cmd
from auris.runtime import ensure_valid_hashseed

app = typer.Typer(help="auris — local-first voice cloning and TTS studio.")


@app.callback()
def _bootstrap() -> None:
    # Runs before any subcommand: keep the environment sane for spawned workers.
    ensure_valid_hashseed()


app.add_typer(speakers_cmd.app, name="speakers")
app.add_typer(asr_cmd.app, name="asr")
app.add_typer(keys_cmd.app, name="keys")
app.add_typer(users_cmd.app, name="users")
app.command("clone")(clone_cmd.clone)
app.command("record")(record_cmd.record)
app.command("tts")(tts_cmd.tts)
app.command("transcribe")(transcribe_cmd.transcribe)
app.command("say")(say_cmd.say)
app.add_typer(voices_cmd.app, name="voices")
app.command("login")(config_cmd.login)
app.command("dashboard")(dashboard_cmd.dashboard)
app.add_typer(config_cmd.config_app, name="config")
app.command("play")(play_cmd.play)
app.command("info")(info_cmd.info)


if __name__ == "__main__":
    app()
