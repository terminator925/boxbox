param(
  [string]$Manifest = "data/sample_stash_full_track_candidates.json",
  [string]$Output = "data/sample_stash_era_classification.json",
  [string]$OlderOutput = "data/benchmark_corpus_stash_likely_older.json",
  [string]$UncertainOutput = "data/benchmark_corpus_stash_uncertain_era.json",
  [int]$OlderCount = 250,
  [int]$UncertainCount = 100,
  [switch]$ProbeTags,
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

$classifyArgs = @(
  "-m", "backend.ml.sample_era_classifier",
  "--manifest", $Manifest,
  "--output", $Output,
  "--older-output", $OlderOutput,
  "--uncertain-output", $UncertainOutput,
  "--older-count", "$OlderCount",
  "--uncertain-count", "$UncertainCount"
)
if ($ProbeTags) { $classifyArgs += "--probe-tags" }
if ($Json) { $classifyArgs += "--json" }

& $venvPython @classifyArgs
if ($LASTEXITCODE -ne 0) { throw "Sample era classification failed." }
