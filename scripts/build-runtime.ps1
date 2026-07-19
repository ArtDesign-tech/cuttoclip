# Build the CutToClip Beta runtime ZIP that the desktop app downloads on first
# launch. Freezes the worker (PyInstaller onedir), assembles the layout the
# supervisor expects, zips it, computes the SHA-256, and updates runtime-lock.json.
#
# You must place the third-party binaries under tools/ first (they are NOT
# committed): tools/ffmpeg/ffmpeg.exe, tools/ffmpeg/ffprobe.exe, tools/deno/deno.exe
#
# After this runs, upload the ZIP to a GitHub Release and paste the release
# download URL into apps/desktop/src-tauri/runtime-lock.json ("url"). The sha256
# is filled in automatically here.

param(
    [string]$Version = "0.2.0-beta.1"
)

$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$workerDir  = Join-Path $repoRoot "apps\worker"
$toolsDir   = Join-Path $repoRoot "tools"
$stageRoot  = Join-Path $repoRoot "release-artifacts\runtime-stage"
$artifactDir = Join-Path $repoRoot "release-artifacts"
$zipName    = "CutToClip-Runtime-v$Version-windows-x64.zip"
$zipPath    = Join-Path $artifactDir $zipName
$lockPath   = Join-Path $repoRoot "apps\desktop\src-tauri\runtime-lock.json"

function Require-Path([string]$path, [string]$hint) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing $path`n  $hint" }
}

# 1. Freeze the worker (onedir) -> apps/worker/dist/local-worker/
Push-Location $workerDir
try {
    python -m PyInstaller cuttoclip-worker.spec --distpath dist --workpath build --noconfirm
}
finally {
    Pop-Location
}
$frozenWorker = Join-Path $workerDir "dist\local-worker"
Require-Path (Join-Path $frozenWorker "local-worker.exe") "PyInstaller did not produce the worker exe."

# 2. Assemble the runtime layout the supervisor expects.
if (Test-Path -LiteralPath $stageRoot) { Remove-Item -Recurse -Force $stageRoot }
New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

Copy-Item -Recurse -LiteralPath $frozenWorker -Destination (Join-Path $stageRoot "worker")

Require-Path (Join-Path $toolsDir "ffmpeg\ffmpeg.exe")  "Put ffmpeg.exe in tools/ffmpeg/"
Require-Path (Join-Path $toolsDir "ffmpeg\ffprobe.exe") "Put ffprobe.exe in tools/ffmpeg/"
Require-Path (Join-Path $toolsDir "deno\deno.exe")      "Put deno.exe in tools/deno/"
Copy-Item -Recurse -LiteralPath (Join-Path $toolsDir "ffmpeg") -Destination (Join-Path $stageRoot "ffmpeg")
Copy-Item -Recurse -LiteralPath (Join-Path $toolsDir "deno")   -Destination (Join-Path $stageRoot "deno")

# 3. Zip it.
New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null
if (Test-Path -LiteralPath $zipPath) { Remove-Item -Force $zipPath }
Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal

# 4. SHA-256 + write it (and the version) into runtime-lock.json. The url is left
#    for you to paste after uploading to the GitHub Release.
$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$zipPath.sha256" -Value "$hash  $zipName" -Encoding ascii

$lock = Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json
$lock.version = $Version
$lock.sha256  = $hash
$lock.sizeBytes = (Get-Item -LiteralPath $zipPath).Length
$lock | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $lockPath -Encoding utf8

Write-Host ""
Write-Host "Runtime ZIP: $zipPath"
Write-Host "SHA256:      $hash"
Write-Host ""
Write-Host "NEXT: upload the ZIP to your GitHub Release, then paste its download URL"
Write-Host "      into the 'url' field of apps/desktop/src-tauri/runtime-lock.json."
