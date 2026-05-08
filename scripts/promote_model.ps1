param(
  [string]$ModelsDir = "models",
  [string]$TargetName = "boxbox_latest.pt",
  [double]$MinScoreImprovementPct = 1.0,
  [int]$MinValExamples = 4,
  [double]$MinRealPct = 1.0,
  [switch]$Apply,
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

$promotionArgs = @(
  "-m", "backend.ml.model_promotion",
  "--models-dir", $ModelsDir,
  "--target-name", $TargetName,
  "--min-score-improvement-pct", "$MinScoreImprovementPct",
  "--min-val-examples", "$MinValExamples",
  "--min-real-pct", "$MinRealPct"
)
if ($Apply) { $promotionArgs += "--apply" }
if ($Json) { $promotionArgs += "--json" }

& $venvPython @promotionArgs
if ($LASTEXITCODE -ne 0) { throw "Model promotion failed." }
