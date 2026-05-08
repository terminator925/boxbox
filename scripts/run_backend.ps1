param()
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

if (-not $env:BOXBOX_INFER_ACCELERATOR) {
  $env:BOXBOX_INFER_ACCELERATOR = "torch"
}
if (-not $env:BOXBOX_INFER_CANDIDATE_STRATEGY) {
  $env:BOXBOX_INFER_CANDIDATE_STRATEGY = "core4_adaptive_plus"
}
if (-not $env:BOXBOX_HYBRID_SEARCH_STRATEGY) {
  $env:BOXBOX_HYBRID_SEARCH_STRATEGY = "core4"
}

Write-Host "Starting BoxBox backend at http://localhost:8000" -ForegroundColor Cyan
Write-Host "Inference accelerator: $env:BOXBOX_INFER_ACCELERATOR" -ForegroundColor DarkCyan
Write-Host "Inference strategy: $env:BOXBOX_INFER_CANDIDATE_STRATEGY" -ForegroundColor DarkCyan
Write-Host "Hybrid search: $env:BOXBOX_HYBRID_SEARCH_STRATEGY" -ForegroundColor DarkCyan
& $venvPython -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
