# Open the auris dashboard in your browser (Windows). The first run asks for the service URL and
# your token and saves them to client.env so you never retype them.
#
# Usage (PowerShell, from the repo root):
#   .\scripts\dashboard.ps1
# If you hit an execution-policy error:
#   powershell -ExecutionPolicy Bypass -File .\scripts\dashboard.ps1

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$conf = "client.env"

if (-not (Test-Path $conf)) {
    Write-Host "First-time setup - enter the details your team gave you."
    $base = (Read-Host "  Service URL (e.g. http://your-server:8011)").TrimEnd("/")
    $token = Read-Host "  Auth token"
    "AURIS_BASE_URL=$base`r`nAURIS_AUTH_TOKEN=$token" | Set-Content -Path $conf -Encoding ascii
    Write-Host "  Saved $conf"
    Write-Host ""
}

$cfg = @{}
Get-Content $conf | ForEach-Object {
    if ($_ -match '^\s*([^=#]+)=(.*)$') { $cfg[$matches[1].Trim()] = $matches[2].Trim() }
}
$base = $cfg["AURIS_BASE_URL"]
if (-not $base) { throw "client.env is missing AURIS_BASE_URL" }

Write-Host "Checking $base ..."
try {
    Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "$base/ready" | Out-Null
    Write-Host "  Service is up."
} catch {
    Write-Host "  WARNING: couldn't reach $base/ready - opening anyway (check the URL / your VPN)."
}

Write-Host "Opening $base/"
Start-Process "$base/"
