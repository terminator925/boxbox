param(
  [string]$Manifest = "data/benchmark_corpus_stash_likely_older_with_tags.json",
  [string]$Output = "data/sample_stash_gridness_report.json",
  [string]$DriftingOutput = "data/benchmark_corpus_stash_audio_drifting.json",
  [string]$GridOutput = "data/benchmark_corpus_stash_audio_grid_quantized.json",
  [int]$Limit = 0,
  [int]$AnalysisSr = 22050,
  [double]$ClipStart = 0.0,
  [double]$ClipDuration = 90.0,
  [int]$Windows = 1,
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

$gridnessArgs = @(
  "-m", "backend.ml.sample_gridness",
  "--manifest", $Manifest,
  "--output", $Output,
  "--drifting-output", $DriftingOutput,
  "--grid-output", $GridOutput,
  "--analysis-sr", "$AnalysisSr",
  "--clip-start", "$ClipStart",
  "--clip-duration", "$ClipDuration",
  "--windows", "$Windows"
)
if ($Limit -gt 0) { $gridnessArgs += @("--limit", "$Limit") }
if ($Json) { $gridnessArgs += "--json" }

& $venvPython @gridnessArgs
if ($LASTEXITCODE -ne 0) { throw "Sample gridness analysis failed." }
