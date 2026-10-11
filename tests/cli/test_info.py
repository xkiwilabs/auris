from __future__ import annotations

from typer.testing import CliRunner

from auris.cli.main import app

runner = CliRunner()


def test_info_reports_core_settings(cli_data_dir):
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0, result.output
    assert "auris" in result.output
    assert "f5_tts" in result.output  # default model
    assert "device setting" in result.output
    # torch may or may not be installed in this environment — either branch is fine,
    # but one of these lines must appear.
    assert ("active device" in result.output) or ("torch:" in result.output)
