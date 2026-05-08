param(
  [string]$ModelsDir = "models",
  [string]$ActiveName = "boxbox_latest.pt",
  [string]$CandidateName = "",
  [double]$MinScoreImprovementPct = 1.0,
  [int]$MinValExamples = 4,
  [double]$MinRealPct = 1.0,
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

$compareArgs = @(
  "-m", "backend.ml.model_compare",
  "--models-dir", $ModelsDir,
  "--active-name", $ActiveName,
  "--min-score-improvement-pct", "$MinScoreImprovementPct",
  "--min-val-examples", "$MinValExamples",
  "--min-real-pct", "$MinRealPct"
)
if ($CandidateName) { $compareArgs += @("--candidate-name", $CandidateName) }
if ($Json) { $compareArgs += "--json" }

& $venvPython @compareArgs
if ($LASTEXITCODE -ne 0) { throw "Model comparison failed." }
