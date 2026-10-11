from __future__ import annotations

import typer

import auris
from auris.audio.transcribe import _resolve_runtime
from auris.client.config import load_client_config
from auris.config import load_settings


def info() -> None:
    """Show the active mode/device, model, and where data is stored."""
    settings = load_settings()
    typer.echo(f"auris {auris.__version__}")

    remote = load_client_config()["base_url"]
    if remote:
        typer.echo(f"mode:           remote → {remote}")
    else:
        typer.echo("mode:           local (model runs on this machine)")
    typer.echo(f"default model:  {settings.default_model}")
    typer.echo(f"device setting: {settings.device}")
    # Show the resolved STT runtime, not the raw setting — "auto" tells you nothing.
    asr_device, asr_index, asr_compute = _resolve_runtime(
        settings.asr_device, settings.asr_compute_type
    )
    asr_where = f"{asr_device}:{asr_index}" if asr_device == "cuda" else asr_device
    typer.echo(f"stt model:      {settings.asr_model} on {asr_where} ({asr_compute})")
    typer.echo(f"data dir:       {settings.data_dir}")

    try:
        import torch
    except ImportError:
        typer.echo("torch:          not installed (run: scripts/install.sh --model)")
        return

    from auris.models.f5_tts import _resolve_device

    cuda = torch.cuda.is_available()
    mps = torch.backends.mps.is_available()
    typer.echo(f"torch:          {torch.__version__}")
    typer.echo(f"active device:  {_resolve_device(settings.device)}  (cuda={cuda}, mps={mps})")
