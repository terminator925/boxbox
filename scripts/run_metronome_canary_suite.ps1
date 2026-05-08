param(
  [Parameter(Mandatory=$true)]
  [string]$AudioManifest,
  [int]$MaxCanaryFiles = 3,
  [double]$TargetBpm = 0,
  [int]$Resolution = 8,
  [int]$GroovePreserve = 50,
  [string]$Mode = "hybrid",
  [double]$MaxElapsedSec = 130,
  [string]$Output = "outputs/metronome_canary_suite_latest.json",
  [string]$IncrementalOutput = "outputs/metronome_canary_suite_latest.partial.json",
  [string]$SummaryOutput = "outputs/metronome_canary_suite_summary_latest.json",
  [switch]$Resume,
  [switch]$SkipAudioErrors
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
if (-not (Test-Path $AudioManifest)) {
  throw "Audio manifest not found: $AudioManifest"
}

$env:PYTHONIOENCODING = "utf-8"

Write-Host "Running metronome canary suite. This is a full quantize path and may take a while." -ForegroundColor Cyan
$benchmarkArgs = @(
  "-m", "backend.ml.benchmark",
  "--metronome-canary",
  "--audio-manifest", $AudioManifest,
  "--resolution", "$Resolution",
  "--groove-preserve", "$GroovePreserve",
  "--mode", $Mode,
  "--max-canary-elapsed-sec", "$MaxElapsedSec",
  "--max-canary-files", "$MaxCanaryFiles",
  "--skip-validation",
  "--output", $Output
)
if ($IncrementalOutput) { $benchmarkArgs += @("--incremental-output", $IncrementalOutput) }
if ($Resume) { $benchmarkArgs += "--resume" }
if ($TargetBpm -gt 0) { $benchmarkArgs += @("--target-bpm", "$TargetBpm") }
if ($SkipAudioErrors) { $benchmarkArgs += "--skip-audio-errors" }

& $venvPython @benchmarkArgs
if ($LASTEXITCODE -ne 0) { throw "Metronome canary suite failed." }

$report = Get-Content $Output -Raw | ConvertFrom-Json
$suite = $report.metronome_canary_suite
Write-Host ("suite_files={0}, passes={1}, fails={2}, audio_skipped={3}, non_song_skipped={4}, all_passed={5}" -f `
  [int]$suite.summary.file_count, `
  [int]$suite.summary.pass_count, `
  [int]$suite.summary.fail_count, `
  [int]$suite.summary.failed_file_count, `
  [int]$suite.summary.skipped_non_song_count, `
  [bool]$suite.summary.all_passed)

if ($SummaryOutput) {
  Write-Host "Summarizing metronome canary suite..." -ForegroundColor Cyan
  & $venvPython -m backend.ml.canary_suite_report `
    --report $Output `
    --output $SummaryOutput `
    --top-n 10
  if ($LASTEXITCODE -ne 0) { throw "Metronome canary suite summary failed." }
}
