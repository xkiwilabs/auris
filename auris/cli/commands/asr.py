from __future__ import annotations

import typer

from auris.audio.asr_models import CATALOGUE, CATALOGUE_BY_ID
from auris.audio.transcribe import _resolve_runtime
from auris.client.api import ClientError
from auris.client.mode import remote_client
from auris.config import load_settings

app = typer.Typer(help="Speech-to-text (faster-whisper) models used for reference transcripts.")


def _cached(model_id: str) -> bool:
    """True if the weights are already in the local HuggingFace cache (no network needed)."""
    try:
        from faster_whisper import download_model
    except ImportError:
        return False
    try:
        download_model(model_id, local_files_only=True)
    except Exception:  # noqa: BLE001 — any miss (not cached, bad revision) means "needs pulling"
        return False
    return True


@app.command("list")
def list_models() -> None:
    """List the STT models you can choose from, and which are already downloaded."""
    sc = remote_client()
    if sc is not None:  # remote mode: the service does the transcribing, so ask it
        try:
            info = sc.asr_models()
        except ClientError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"service default: {info['default']}")
        for entry in info["models"]:
            mark = "*" if entry["id"] == info["default"] else " "
            typer.echo(
                f" {mark} {entry['id']:<17} {entry['params']:>5}  "
                f"{entry['lang']:<5} {entry['desc']}"
            )
        typer.echo("\nChoose one per command with --asr-model.")
        return

    settings = load_settings()
    device, index, compute = _resolve_runtime(settings.asr_device, settings.asr_compute_type)
    where = f"{device}:{index}" if device == "cuda" else device
    typer.echo(f"active: {settings.asr_model} on {where} ({compute})")
    typer.echo(f"allowed for remote callers: {', '.join(settings.asr_allowed)}\n")

    for model in CATALOGUE:
        marks = "*" if model.id == settings.asr_model else " "
        state = "downloaded" if _cached(model.id) else "not downloaded"
        typer.echo(
            f" {marks} {model.id:<17} {model.params:>5}  {model.lang:<5} {state:<14} {model.desc}"
        )
    typer.echo("\nSet the default with AURIS_ASR_MODEL, the device with AURIS_ASR_DEVICE.")


@app.command("pull")
def pull(
    models: list[str] = typer.Argument(
        None, help="Model ids to download (default: the configured AURIS_ASR_MODEL)"
    ),
    all_models: bool = typer.Option(False, "--all", help="Download the whole catalogue"),
) -> None:
    """Pre-download STT weights so the first transcription doesn't stall on a fetch."""
    settings = load_settings()
    if all_models:
        wanted = [m.id for m in CATALOGUE]
    elif models:
        wanted = list(models)
    else:
        wanted = [settings.asr_model]

    unknown = [m for m in wanted if m not in CATALOGUE_BY_ID]
    if unknown:
        typer.echo(f"unknown model(s): {', '.join(unknown)} — run 'auris asr list'", err=True)
        raise typer.Exit(code=1)

    try:
        from faster_whisper import download_model
    except ImportError:
        typer.echo(
            "downloading STT weights needs faster-whisper (the 'model' extra). "
            "Install it with: pip install -e '.[model]'",
            err=True,
        )
        raise typer.Exit(code=1) from None

    for model_id in wanted:
        typer.echo(f"pulling {model_id} …")
        try:
            path = download_model(model_id)
        except (OSError, ValueError) as exc:  # network / bad id / disk
            typer.echo(f"failed to download {model_id}: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(f"  → {path}")
    typer.echo("done.")
