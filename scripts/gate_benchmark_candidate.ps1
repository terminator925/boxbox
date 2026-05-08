param(
  [Parameter(Mandatory = $true)]
  [string]$BaselineReport,
  [Parameter(Mandatory = $true)]
  [string]$CandidateReport,
  [string]$Output = "outputs/benchmark_candidate_gate.json",
  [double]$MaxAvgRegressionSec = 0.001,
  [double]$MaxPerFileRegressionSec = 0.003,
  [double]$MinSpeedupPct = 0.0
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

Write-Host "Evaluating benchmark candidate gate..." -ForegroundColor Cyan
$gateArgs = @(
  "-m", "backend.ml.benchmark",
  "--benchmark-gate-baseline-report", $BaselineReport,
  "--benchmark-gate-candidate-report", $CandidateReport,
  "--benchmark-gate-max-avg-regression-sec", "$MaxAvgRegressionSec",
  "--benchmark-gate-max-per-file-regression-sec", "$MaxPerFileRegressionSec",
  "--benchmark-gate-min-speedup-pct", "$MinSpeedupPct",
  "--output", $Output
)

& $venvPython @gateArgs
if ($LASTEXITCODE -ne 0) { throw "Benchmark candidate gate failed to run." }
