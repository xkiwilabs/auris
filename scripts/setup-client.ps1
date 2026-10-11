# Set up the auris REMOTE-CLIENT profile on Windows — no GPU, no model. You get the `auris` CLI in
# remote mode (record / tts / say / voices against a running service) and `auris dashboard` (a
# localhost dashboard where the mic works).
#
# Usage (PowerShell, from the repo root):
#   .\scripts\setup-client.ps1
# If you hit an execution-policy error:
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup-client.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Error "Python not found. Install Python 3.11+ from https://www.python.org/downloads/ (tick 'Add to PATH')."
    exit 1
}

Write-Host "==> creating .venv-client"
& $py.Source -m venv .venv-client
$pyexe = ".\.venv-client\Scripts\python.exe"
$auris = ".\.venv-client\Scripts\auris.exe"
& $pyexe -m pip install --quiet --upgrade pip
Write-Host "==> installing auris client (no model) + example deps"
& $pyexe -m pip install --quiet -e ".[client,audio]" requests openai

Write-Host ""
Write-Host "==> where is the auris server? (ask whoever runs it for the address)"
& $auris login --no-token

Write-Host ""
Write-Host "Client ready. In this terminal, and in every new one, first run:"
Write-Host ""
Write-Host "  .\.venv-client\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Then open the dashboard and sign in there with your email and password (the mic works here):"
Write-Host ""
Write-Host "  auris dashboard"
Write-Host ""
Write-Host "The command-line tools need an API key as well. Make one in the dashboard (Account -> My API"
Write-Host "keys -> Issue), then save it once with:  auris config token"
Write-Host ""
Write-Host "  auris voices                    # list the service's voices"
Write-Host "  auris record `"Me`"               # record from your mic and upload (clone your voice)"
Write-Host "  auris tts narrator-uk-f --text `"Hello.`" --out hello.mp3"
