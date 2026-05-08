param(
  [string]$Manifest = "data/sample_stash_full_track_candidates.json",
  [int]$Seed = 7,
  [int]$SmokeCount = 12,
  [int]$DevCount = 75,
  [int]$BroadCount = 250,
  [switch]$IncludeFull
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

function New-Corpus($Name, $Output, $Count) {
  & $venvPython -m backend.ml.sample_corpus --manifest $Manifest --output $Output --count "$Count" --seed "$Seed" --name $Name
  if ($LASTEXITCODE -ne 0) { throw "Failed building corpus $Name." }
}

New-Corpus "stash_smoke" "data/benchmark_corpus_stash_smoke.json" $SmokeCount
New-Corpus "stash_dev" "data/benchmark_corpus_stash_dev.json" $DevCount
New-Corpus "stash_broad" "data/benchmark_corpus_stash_broad.json" $BroadCount
if ($IncludeFull) {
  New-Corpus "stash_full" "data/benchmark_corpus_stash_full.json" 0
}
