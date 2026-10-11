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
Write-Host "==> configuring the service (you can skip the token and add it later)"
& $auris login

Write-Host ""
Write-Host "Client ready. Activate it in each new shell with:  .\.venv-client\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Then:"
Write-Host "  auris dashboard                 # open the dashboard locally (mic works here)"
Write-Host "  auris voices                    # list the service's voices"
Write-Host "  auris record `"Me`"               # record from your mic and upload (clone your voice)"
Write-Host "  auris tts narrator-uk-f --text `"Hello.`" --out hello.mp3"
Write-Host ""
Write-Host "Add or change your token any time:  auris config token"
