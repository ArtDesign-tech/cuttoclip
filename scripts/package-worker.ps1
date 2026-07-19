param(
  [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$workerDir = Join-Path $root "apps\worker"
$outputDir = Join-Path $root "apps\desktop\src-tauri\binaries"

python -m PyInstaller --onefile --name local-worker (Join-Path $workerDir "app\main.py") `
  --collect-all onnxruntime `
  --collect-submodules numpy `
  --hidden-import onnxruntime `
  --distpath $outputDir --workpath (Join-Path $workerDir "build") --specpath (Join-Path $workerDir "build")
$built = Join-Path $outputDir "local-worker.exe"
$target = Join-Path $outputDir ("local-worker-{0}.exe" -f $TargetTriple)
if (-not (Test-Path $built)) { throw "PyInstaller did not produce $built" }
Move-Item -Force $built $target
Write-Host "Worker packaged at $target"

$configPath = Join-Path $root "apps\desktop\src-tauri\tauri.conf.json"
$config = Get-Content $configPath -Raw | ConvertFrom-Json
if (-not $config.bundle.externalBin) {
  $config.bundle | Add-Member -NotePropertyName externalBin -NotePropertyValue @("binaries/local-worker")
}
$config | ConvertTo-Json -Depth 20 | Set-Content $configPath -Encoding utf8

$capabilityPath = Join-Path $root "apps\desktop\src-tauri\capabilities\default.json"
$capability = Get-Content $capabilityPath -Raw | ConvertFrom-Json
$spawnPermission = [pscustomobject]@{
  identifier = "shell:allow-spawn"
  allow = @([pscustomobject]@{ name = "binaries/local-worker"; sidecar = $true })
}
$capability.permissions = @($capability.permissions) + $spawnPermission
$capability | ConvertTo-Json -Depth 20 | Set-Content $capabilityPath -Encoding utf8
Write-Host "Tauri sidecar configuration enabled."
