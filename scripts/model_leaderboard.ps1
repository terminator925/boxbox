param(
  [string]$ModelsDir = "models",
  [int]$Limit = 10,
  [int]$MinValExamples = 4,
  [double]$MinRealPct = 1.0,
  [switch]$Json,
  [switch]$IncludeMissingModels,
  [switch]$Validate,
  [switch]$AllowMissingModels,
  [switch]$SyncEmbedded,
  [switch]$Apply
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

$leaderboardArgs = @(
  "-m", "backend.ml.model_registry",
  "--models-dir", $ModelsDir,
  "--limit", "$Limit",
  "--min-val-examples", "$MinValExamples",
  "--min-real-pct", "$MinRealPct"
)
if ($Json) { $leaderboardArgs += "--json" }
if ($IncludeMissingModels) { $leaderboardArgs += "--include-missing-models" }
if ($Validate) { $leaderboardArgs += "--validate" }
if ($AllowMissingModels) { $leaderboardArgs += "--allow-missing-models" }
if ($SyncEmbedded) { $leaderboardArgs += "--sync-embedded" }
if ($Apply) { $leaderboardArgs += "--apply" }

& $venvPython @leaderboardArgs
if ($LASTEXITCODE -ne 0) { throw "Model leaderboard failed." }
