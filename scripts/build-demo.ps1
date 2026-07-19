$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$artifactDirectory = Join-Path $repoRoot "release-artifacts"
$artifactPath = Join-Path $artifactDirectory "CutToClip-Demo-v0.1.0-x64-setup.exe"
$checksumPath = "$artifactPath.sha256"

Push-Location $repoRoot
try {
    npm.cmd run build:demo:desktop

    $bundleDirectory = Join-Path $repoRoot "apps\desktop\src-tauri\target\x86_64-pc-windows-msvc\release\bundle\nsis"
    $installer = Get-ChildItem -LiteralPath $bundleDirectory -Filter "*.exe" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $installer) {
        throw "NSIS installer was not produced in $bundleDirectory"
    }

    New-Item -ItemType Directory -Path $artifactDirectory -Force | Out-Null
    Copy-Item -LiteralPath $installer.FullName -Destination $artifactPath -Force
    $hash = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksumPath -Value "$hash  CutToClip-Demo-v0.1.0-x64-setup.exe" -Encoding ascii

    Get-Item -LiteralPath $artifactPath, $checksumPath | Select-Object FullName, Length, LastWriteTime
    Write-Output "SHA256 $hash"
}
finally {
    Pop-Location
}
