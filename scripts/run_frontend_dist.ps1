param(
  [int]$Port = 5173,
  [switch]$Build,
  [switch]$AllowStale
)
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "frontend"
$DistRoot = Join-Path $FrontendRoot "dist"

function Get-LatestFileWriteTimeUtc {
  param([string[]]$Roots)
  $latest = [datetime]::MinValue
  foreach ($root in $Roots) {
    if (-not (Test-Path $root)) { continue }
    $items = @()
    if (Test-Path $root -PathType Leaf) {
      $items = @(Get-Item $root)
    } else {
      $items = @(Get-ChildItem -Path $root -Recurse -File)
    }
    foreach ($item in $items) {
      if ($item.LastWriteTimeUtc -gt $latest) {
        $latest = $item.LastWriteTimeUtc
      }
    }
  }
  return $latest
}

function Test-FrontendDistFresh {
  $sourceRoots = @(
    (Join-Path $FrontendRoot "src"),
    (Join-Path $FrontendRoot "scripts"),
    (Join-Path $FrontendRoot "index.html"),
    (Join-Path $FrontendRoot "package.json"),
    (Join-Path $FrontendRoot "package-lock.json")
  )
  $distFiles = @()
  if (Test-Path $DistRoot) {
    $distFiles = @(Get-ChildItem -Path $DistRoot -Recurse -File)
  }
  if ($distFiles.Count -eq 0) {
    return $false
  }
  $sourceLatest = Get-LatestFileWriteTimeUtc -Roots $sourceRoots
  $distLatest = Get-LatestFileWriteTimeUtc -Roots @($DistRoot)
  return $distLatest -ge $sourceLatest
}

$venvPython = Join-Path $RepoRoot "backend/.venv/Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
  $venvPython = Join-Path $RepoRoot "venv/Scripts/python.exe"
}
if (-not (Test-Path $venvPython)) {
  $venvPython = "python"
}

if ($Build) {
  Set-Location $FrontendRoot
  $env:npm_config_cache = Join-Path $RepoRoot ".npm-cache"
  Write-Host "Building BoxBox static frontend with WASM fallback..." -ForegroundColor Cyan
  npm run build:wasm
  npm run verify:dist
} elseif (-not (Test-FrontendDistFresh) -and -not $AllowStale) {
  throw "Static frontend appears stale. Run scripts/run_frontend_dist.ps1 -Build, or pass -AllowStale only for deliberate stale-bundle debugging."
}

$indexPath = Join-Path $DistRoot "index.html"
if (-not (Test-Path $indexPath)) {
  throw "Static frontend is missing. Run: cd frontend; `$env:npm_config_cache='$RepoRoot\.npm-cache'; npm run build:wasm; npm run verify:dist"
}

Write-Host "Starting BoxBox static frontend at http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Serving: $DistRoot" -ForegroundColor DarkCyan
& $venvPython -m http.server $Port --bind 127.0.0.1 --directory $DistRoot
