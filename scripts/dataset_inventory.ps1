param(
  [string]$Examples = "data/examples",
  [string]$Datasets = "",
  [string]$MinImprovementPct = "",
  [string]$MaxAfterSec = "",
  [int]$MinEventCount = 0,
  [int]$MaxExamples = 0,
  [switch]$Json,
  [switch]$IncludeRecords
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

$inventoryArgs = @(
  "-m", "backend.ml.dataset_inventory",
  "--examples", $Examples
)
if ($Datasets) { $inventoryArgs += @("--datasets", $Datasets) }
if ($MinImprovementPct) { $inventoryArgs += @("--min-improvement-pct", $MinImprovementPct) }
if ($MaxAfterSec) { $inventoryArgs += @("--max-after-sec", $MaxAfterSec) }
if ($MinEventCount -gt 0) { $inventoryArgs += @("--min-event-count", "$MinEventCount") }
if ($MaxExamples -gt 0) { $inventoryArgs += @("--max-examples", "$MaxExamples") }
if ($Json) { $inventoryArgs += "--json" }
if ($IncludeRecords) { $inventoryArgs += "--include-records" }

& $venvPython @inventoryArgs
if ($LASTEXITCODE -ne 0) { throw "Dataset inventory failed." }
