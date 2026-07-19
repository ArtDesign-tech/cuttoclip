[CmdletBinding()]
param(
  [string]$CredentialsPath = $(Join-Path $env:LOCALAPPDATA "CutToClip\gateway-credentials.json"),
  [int]$Port = 4317
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $CredentialsPath)) { throw "Missing tester credentials. Run scripts/onboard-tester.ps1 first." }
$credentials = Get-Content -LiteralPath $CredentialsPath -Raw | ConvertFrom-Json
foreach ($property in "gatewayUrl", "installationToken", "cloudflareAccessClientId", "cloudflareAccessClientSecret") {
  if ([string]::IsNullOrWhiteSpace([string]$credentials.$property)) { throw "Tester credentials are incomplete." }
}

$env:CUTTOCLIP_GATEWAY_URL = [string]$credentials.gatewayUrl
$env:CUTTOCLIP_INSTALLATION_TOKEN = [string]$credentials.installationToken
$env:CUTTOCLIP_CF_ACCESS_CLIENT_ID = [string]$credentials.cloudflareAccessClientId
$env:CUTTOCLIP_CF_ACCESS_CLIENT_SECRET = [string]$credentials.cloudflareAccessClientSecret
$env:CUTTOCLIP_GATEWAY_REQUIRE_ACCESS = "true"

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
  $pythonExecutable = $venvPython
} else {
  $systemPython = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $systemPython) {
    throw "Python was not found. Install Python 3.11+ or create .venv using the setup commands in README.md."
  }
  $pythonExecutable = $systemPython.Source
  Write-Warning "Local .venv was not found; using system Python at $pythonExecutable."
}

Push-Location $root
try {
  & $pythonExecutable -m uvicorn apps.worker.app.main:app --port $Port
} finally {
  Pop-Location
}
