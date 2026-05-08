param(
  [string]$Examples = "data/examples",
  [string]$Output = "models/boxbox_latest.pt",
  [int]$Epochs = 12,
  [int]$BatchSize = 2,
  [string]$Device = "cpu",
  [double]$LearningRate = 0.001,
  [double]$ValRatio = 0.15,
  [string]$InitModel = "",
  [string]$CacheDir = "data/cache/warp_targets",
  [switch]$WarmCache,
  [int]$NumWorkers = -1,
  [string]$Datasets = "",
  [ValidateSet("uniform", "inverse")]
  [string]$DatasetBalance = "uniform",
  [string]$DatasetWeightOverrides = "",
  [string]$MinImprovementPct = "",
  [string]$MaxAfterSec = "",
  [int]$MinEventCount = 0,
  [int]$MaxExamples = 0,
  [string]$ManifestOutput = "",
  [int]$GenerateCount = 0,
  [switch]$CleanSyntheticData,
  [switch]$DryRun,
  [switch]$SkipEvaluate
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

Write-Host "Preparing BoxBox ML training..." -ForegroundColor Cyan
if ($GenerateCount -gt 0) {
  $genArgs = @("-m", "backend.ml.synthetic_data", "--output", $Examples, "--count", "$GenerateCount", "--sr", "22050", "--seed", "7")
  if ($CleanSyntheticData) {
    $genArgs += "--clean"
  }
  Write-Host "Generating synthetic dataset..." -ForegroundColor Cyan
  & $venvPython @genArgs
  if ($LASTEXITCODE -ne 0) { throw "Synthetic dataset generation failed." }
}

Write-Host "Training BoxBox ML model..." -ForegroundColor Cyan
$trainArgs = @(
  "-m", "backend.ml.train",
  "--examples", $Examples,
  "--output", $Output,
  "--epochs", "$Epochs",
  "--batch-size", "$BatchSize",
  "--device", $Device,
  "--lr", "$LearningRate",
  "--val-ratio", "$ValRatio",
  "--cache-dir", $CacheDir,
  "--dataset-balance", $DatasetBalance
)
if ($InitModel) { $trainArgs += @("--init-model", $InitModel) }
if ($WarmCache) { $trainArgs += "--warm-cache" }
if ($NumWorkers -ge 0) { $trainArgs += @("--num-workers", "$NumWorkers") }
if ($Datasets) { $trainArgs += @("--datasets", $Datasets) }
if ($DatasetWeightOverrides) { $trainArgs += @("--dataset-weight-overrides", $DatasetWeightOverrides) }
if ($MinImprovementPct) { $trainArgs += @("--min-improvement-pct", $MinImprovementPct) }
if ($MaxAfterSec) { $trainArgs += @("--max-after-sec", $MaxAfterSec) }
if ($MinEventCount -gt 0) { $trainArgs += @("--min-event-count", "$MinEventCount") }
if ($MaxExamples -gt 0) { $trainArgs += @("--max-examples", "$MaxExamples") }
if ($ManifestOutput) { $trainArgs += @("--manifest-output", $ManifestOutput) }
if ($DryRun) { $trainArgs += "--dry-run" }

Write-Host "  examples=$Examples"
Write-Host "  output=$Output"
Write-Host "  cache=$CacheDir warm_cache=$($WarmCache.IsPresent)"
Write-Host "  datasets=$Datasets balance=$DatasetBalance max_examples=$MaxExamples"
Write-Host "  manifest=$ManifestOutput"
Write-Host "  filters=min_improvement_pct:$MinImprovementPct max_after_sec:$MaxAfterSec min_event_count:$MinEventCount"

& $venvPython @trainArgs
if ($LASTEXITCODE -ne 0) { throw "Training failed." }

if ($DryRun) {
  Write-Host "Dry run complete. No model was written." -ForegroundColor Green
  exit 0
}

if ($SkipEvaluate) {
  Write-Host "Training complete. Evaluation skipped. Model: $Output" -ForegroundColor Green
  exit 0
}

Write-Host "Evaluating trained model..." -ForegroundColor Cyan
$evalArgs = @(
  "-m", "backend.ml.evaluate",
  "--examples", $Examples,
  "--model", $Output,
  "--device", $Device,
  "--val-ratio", "$ValRatio"
)
if ($Datasets) { $evalArgs += @("--datasets", $Datasets) }
if ($MinImprovementPct) { $evalArgs += @("--min-improvement-pct", $MinImprovementPct) }
if ($MaxAfterSec) { $evalArgs += @("--max-after-sec", $MaxAfterSec) }
if ($MinEventCount -gt 0) { $evalArgs += @("--min-event-count", "$MinEventCount") }
if ($MaxExamples -gt 0) { $evalArgs += @("--max-examples", "$MaxExamples") }

& $venvPython @evalArgs
if ($LASTEXITCODE -ne 0) { throw "Evaluation failed." }

Write-Host "Training complete. Model: $Output" -ForegroundColor Green
