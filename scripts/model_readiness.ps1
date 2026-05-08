param(
  [string]$Examples = "data/examples",
  [string]$ModelsDir = "models",
  [string]$Datasets = "",
  [string]$TargetName = "boxbox_latest.pt",
  [int]$Limit = 5,
  [double]$MinScoreImprovementPct = 1.0,
  [int]$MinValExamples = 4,
  [double]$MinRealPct = 1.0,
  [int]$MaxExamples = 0,
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

$readinessArgs = @(
  "-m", "backend.ml.model_readiness",
  "--examples", $Examples,
  "--models-dir", $ModelsDir,
  "--target-name", $TargetName,
  "--limit", "$Limit",
  "--min-score-improvement-pct", "$MinScoreImprovementPct",
  "--min-val-examples", "$MinValExamples",
  "--min-real-pct", "$MinRealPct"
)
if ($Datasets) { $readinessArgs += @("--datasets", $Datasets) }
if ($MaxExamples -gt 0) { $readinessArgs += @("--max-examples", "$MaxExamples") }
if ($Json) { $readinessArgs += "--json" }

& $venvPython @readinessArgs
if ($LASTEXITCODE -ne 0) { throw "Model readiness report failed." }
