# Build the CutToClip Beta desktop installer.
#
# Optionally embeds default Groq/Gemini API keys: if scripts/build-beta.local.ps1
# exists it is sourced first to set CUTTOCLIP_EMBEDDED_*_KEYS in this session.
# Without it, the build succeeds with no embedded keys (testers enter their own).
#
# `option_env!` in the Rust build reads these at COMPILE time, so they must be in
# the environment before `tauri build` runs — which is exactly what this does.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$localKeys = Join-Path $PSScriptRoot "build-beta.local.ps1"

if (Test-Path -LiteralPath $localKeys) {
    Write-Host "Loading embedded keys from build-beta.local.ps1"
    . $localKeys
} else {
    Write-Host "No build-beta.local.ps1 found - building with NO embedded keys."
    Write-Host "  (Copy build-beta.local.example.ps1 to build-beta.local.ps1 to embed keys.)"
}

# Report presence/count only - never echo the key values themselves.
function Show-KeyStatus([string]$label, [string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host ("  {0}: none" -f $label)
    } else {
        $count = ($value -split "," | Where-Object { $_.Trim() -ne "" }).Count
        Write-Host ("  {0}: {1} key(s) embedded" -f $label, $count)
    }
}
Show-KeyStatus "Groq"   $env:CUTTOCLIP_EMBEDDED_GROQ_KEYS
Show-KeyStatus "Gemini" $env:CUTTOCLIP_EMBEDDED_GEMINI_KEYS

Push-Location $repoRoot
try {
    npm.cmd run build:beta --workspace @cuttoclip/desktop

    $bundleDir = Join-Path $repoRoot "apps\desktop\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis"
    $installer = Get-ChildItem -LiteralPath $bundleDir -Filter "*.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $installer) {
        throw "NSIS installer was not produced in $bundleDir"
    }

    $artifactDir = Join-Path $repoRoot "release-artifacts"
    New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null
    $artifact = Join-Path $artifactDir "CutToClip-Beta-v0.2.0-beta.1-x64-setup.exe"
    Copy-Item -LiteralPath $installer.FullName -Destination $artifact -Force
    $hash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$artifact.sha256" -Value "$hash  CutToClip-Beta-v0.2.0-beta.1-x64-setup.exe" -Encoding ascii

    Write-Host ""
    Write-Host "Installer: $artifact"
    Write-Host "SHA256:    $hash"
}
finally {
    Pop-Location
    # Clear embedded keys from the session so they don't linger in the shell.
    Remove-Item Env:\CUTTOCLIP_EMBEDDED_GROQ_KEYS   -ErrorAction SilentlyContinue
    Remove-Item Env:\CUTTOCLIP_EMBEDDED_GEMINI_KEYS -ErrorAction SilentlyContinue
}
