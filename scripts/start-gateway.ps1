[CmdletBinding()]
param(
  [string]$LogDirectory
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($LogDirectory)) {
  $baseDirectory = if ($env:ProgramData) { $env:ProgramData } else { $env:LOCALAPPDATA }
  $LogDirectory = Join-Path $baseDirectory "CutToClip\Gateway\logs"
}
$envFile = Join-Path $root "gateway\.env"
$entry = Join-Path $root "gateway\dist\index.js"
if (-not (Test-Path -LiteralPath $envFile)) { throw "Missing gateway/.env. Copy gateway/.env.example and fill the production values first." }
if (-not (Test-Path -LiteralPath $entry)) { throw "Missing built gateway. Run npm.cmd run build:gateway first." }

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$log = Join-Path $LogDirectory "gateway.log"
if ((Test-Path -LiteralPath $log) -and (Get-Item -LiteralPath $log).Length -gt 10MB) {
  for ($index = 4; $index -ge 1; $index--) {
    $source = "$log.$index"
    $destination = "$log." + ($index + 1)
    if (Test-Path -LiteralPath $source) { Move-Item -LiteralPath $source -Destination $destination -Force }
  }
  Move-Item -LiteralPath $log -Destination "$log.1" -Force
}

Push-Location $root
try {
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & npm.cmd run start --workspace @cuttoclip/gateway 2>&1 | Tee-Object -FilePath $log -Append
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
} finally {
  Pop-Location
}
exit $exitCode
