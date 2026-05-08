param(
  [int]$Count = 64,
  [int]$SampleRate = 22050,
  [int]$Seed = 7,
  [switch]$Clean
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

$argsList = @("-m", "backend.ml.synthetic_data", "--output", "data/examples", "--count", "$Count", "--sr", "$SampleRate", "--seed", "$Seed")
if ($Clean) {
  $argsList += "--clean"
}

Write-Host "Generating synthetic training examples..." -ForegroundColor Cyan
& $venvPython @argsList
if ($LASTEXITCODE -ne 0) { throw "Synthetic example generation failed." }
