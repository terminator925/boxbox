param(
  [string]$Root = "H:\SAUCE (AUDIO)\SONG STASH\songs n samples",
  [string]$Output = "data/sample_stash_manifest.json",
  [int]$Limit = 0,
  [int]$MinBytes = 0,
  [double]$MinDurationSec = 0.0,
  [switch]$IncludeStems,
  [switch]$ExcludeLikelyLoops,
  [switch]$ProbeDuration,
  [switch]$Json
)
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot "backend/.venv/Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
  $venvPython = Join-Path $RepoRoot "venv/Scripts/python.exe"
}
if (-not (Test-Path $venvPython)) {
  throw "No Python environment found. Run scripts/setup_windows.ps1 first."
}

$inventoryArgs = @(
  "-m", "backend.ml.sample_stash",
  "--root", $Root,
  "--output", $Output
)
if ($Limit -gt 0) { $inventoryArgs += @("--limit", "$Limit") }
if ($MinBytes -gt 0) { $inventoryArgs += @("--min-bytes", "$MinBytes") }
if ($MinDurationSec -gt 0) { $inventoryArgs += @("--min-duration-sec", "$MinDurationSec") }
if ($IncludeStems) { $inventoryArgs += "--include-stems" }
if ($ExcludeLikelyLoops) { $inventoryArgs += "--exclude-likely-loops" }
if ($ProbeDuration) { $inventoryArgs += "--probe-duration" }
if ($Json) { $inventoryArgs += "--json" }

& $venvPython @inventoryArgs
if ($LASTEXITCODE -ne 0) { throw "Sample stash inventory failed." }
