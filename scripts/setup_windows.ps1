param()
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

Write-Host "=== BoxBox Setup (Windows) ===" -ForegroundColor Cyan

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  throw "ffmpeg not found in PATH. Install ffmpeg and reopen PowerShell."
}
Write-Host "ffmpeg found." -ForegroundColor Green

$venvPath = Join-Path $RepoRoot "backend/.venv"
if (-not (Test-Path $venvPath)) {
  Write-Host "Creating backend virtual environment..."
  python -m venv $venvPath
}

Write-Host "Installing backend dependencies..."
try {
  & "$venvPath/Scripts/python.exe" -m pip install --upgrade pip
  & "$venvPath/Scripts/python.exe" -m pip install -r "backend/requirements.txt"
} catch {
  Write-Warning "Backend dependency install failed. This usually means offline/restricted network."
  if (Test-Path (Join-Path $RepoRoot "venv/Scripts/python.exe")) {
    Write-Host "Fallback environment detected at .\\venv. You can still run locally using that." -ForegroundColor Yellow
  } else {
    throw
  }
}

Write-Host "Installing frontend dependencies..."
Set-Location (Join-Path $RepoRoot "frontend")
try {
  npm install
} catch {
  Write-Warning "Frontend dependency install failed (likely offline)."
}

Set-Location $RepoRoot
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next: run scripts/run_backend.ps1 and scripts/run_frontend.ps1"
