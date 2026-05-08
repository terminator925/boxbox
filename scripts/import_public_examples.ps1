param(
  [int]$MaestroCount = 24,
  [int]$GrooveCount = 24,
  [int]$EGMDCount = 24,
  [int]$ASAPCount = 24,
  [int]$POP909Count = 24,
  [int]$SampleRate = 22050,
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

Write-Host "Importing public MIDI datasets into paired audio examples..." -ForegroundColor Cyan
& $venvPython -m backend.ml.import_public_midi --downloads data/downloads --output data/examples --maestro-count $MaestroCount --groove-count $GrooveCount --egmd-count $EGMDCount --asap-count $ASAPCount --pop909-count $POP909Count --sr $SampleRate --seed $Seed
if ($LASTEXITCODE -ne 0) { throw "Public dataset import failed." }
