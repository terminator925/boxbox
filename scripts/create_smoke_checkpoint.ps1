param(
  [string]$Output = "models/boxbox_smoke.pt",
  [string]$Status = "smoke_test",
  [int]$FeatureDim = 82,
  [int]$TrainExamples = 4,
  [int]$ValExamples = 4,
  [double]$AvgMae = 0.5,
  [double]$BaselineAvgMae = 1.0,
  [double]$RealPct = 50.0,
  [double]$UnknownPct = 0.0,
  [string]$ManifestOutput = "",
  [int]$Seed = 7
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

$smokeArgs = @(
  "-m", "backend.ml.model_smoke_checkpoint",
  "--output", $Output,
  "--status", $Status,
  "--feature-dim", "$FeatureDim",
  "--train-examples", "$TrainExamples",
  "--val-examples", "$ValExamples",
  "--avg-mae", "$AvgMae",
  "--baseline-avg-mae", "$BaselineAvgMae",
  "--real-pct", "$RealPct",
  "--unknown-pct", "$UnknownPct",
  "--seed", "$Seed"
)
if ($ManifestOutput) { $smokeArgs += @("--manifest-output", $ManifestOutput) }

& $venvPython @smokeArgs
if ($LASTEXITCODE -ne 0) { throw "Smoke checkpoint creation failed." }
