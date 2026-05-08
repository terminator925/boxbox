param(
  [Parameter(Mandatory=$true)]
  [string]$Report,
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

$summaryArgs = @(
  "-m", "backend.ml.benchmark_report",
  "--report", $Report,
  "--top-n", "$TopN"
)
if ($Output) { $summaryArgs += @("--output", $Output) }
if ($Json) { $summaryArgs += "--json" }

& $venvPython @summaryArgs
if ($LASTEXITCODE -ne 0) { throw "Benchmark report summarization failed." }
