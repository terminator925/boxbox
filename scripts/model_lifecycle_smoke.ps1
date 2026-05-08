param(
  [string]$Output = "outputs/smoke_lifecycle/boxbox_smoke.pt",
  [string]$Examples = "data/examples",
  [string]$Status = "smoke_test",
  [int]$FeatureDim = 82,
  [int]$MaxExamples = 10,
  [double]$RealPct = 50.0,
  [double]$AvgMae = 0.5,
  [double]$BaselineAvgMae = 1.0,
  [switch]$Json
)
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$outputPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Output))
$modelsDir = Split-Path -Parent $outputPath

Write-Host "Creating guarded model lifecycle smoke checkpoint..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "create_smoke_checkpoint.ps1") `
  -Output $Output `
  -Status $Status `
  -FeatureDim $FeatureDim `
  -RealPct $RealPct `
  -AvgMae $AvgMae `
  -BaselineAvgMae $BaselineAvgMae
if ($LASTEXITCODE -ne 0) { throw "Smoke checkpoint creation failed." }

Write-Host "Running model readiness against smoke checkpoint..." -ForegroundColor Cyan
$readinessArgs = @{
  Examples = $Examples
  ModelsDir = $modelsDir
  MaxExamples = $MaxExamples
}
if ($Json) { $readinessArgs.Json = $true }

& (Join-Path $PSScriptRoot "model_readiness.ps1") @readinessArgs
if ($LASTEXITCODE -ne 0) { throw "Model lifecycle smoke readiness failed." }
