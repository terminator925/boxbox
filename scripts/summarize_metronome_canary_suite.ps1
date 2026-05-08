param(
  [string]$Report = "outputs/metronome_canary_suite_latest.json",
  [string]$Output = "",
  [int]$TopN = 10,
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
if (-not (Test-Path $Report)) {
  throw "Metronome canary suite report not found: $Report"
}

$summaryArgs = @(
  "-m", "backend.ml.canary_suite_report",
  "--report", $Report,
  "--top-n", "$TopN"
)
if ($Output) { $summaryArgs += @("--output", $Output) }
if ($Json) { $summaryArgs += "--json" }

& $venvPython @summaryArgs
if ($LASTEXITCODE -ne 0) { throw "Metronome canary suite summarization failed." }
