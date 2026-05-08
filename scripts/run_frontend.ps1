param()
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location (Join-Path $RepoRoot "frontend")

Write-Host "Starting BoxBox frontend at http://localhost:5173" -ForegroundColor Cyan
npm run dev
