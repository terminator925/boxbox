param(
  [string]$AudioPath = "stayin-alive-serban-mix.wav",
  [double]$TargetBpm = 104,
  [int]$Resolution = 8,
  [int]$GroovePreserve = 50,
  [string]$Mode = "hybrid",
  [double]$MaxElapsedSec = 130,
  [string]$Output = "outputs/benchmark_metronome_canary_latest.json",
  [string]$GateOutput = "outputs/benchmark_metronome_canary_gate_latest.json",
  [string]$HistoryOutput = "outputs/canary_history_summary_latest.json"
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
if (-not (Test-Path $AudioPath)) {
  throw "Canary audio not found: $AudioPath"
}

$env:PYTHONIOENCODING = "utf-8"

Write-Host "Running Stayin' Alive metronome canary..." -ForegroundColor Cyan
& $venvPython -m backend.ml.benchmark `
  --metronome-canary `
  --audio $AudioPath `
  --target-bpm $TargetBpm `
  --resolution $Resolution `
  --groove-preserve $GroovePreserve `
  --mode $Mode `
  --skip-validation `
  --output $Output
if ($LASTEXITCODE -ne 0) { throw "Stayin' Alive canary run failed." }

Write-Host "Running 100% DAW-lock gate..." -ForegroundColor Cyan
& $venvPython -m backend.ml.benchmark `
  --gate-metronome-canary-report $Output `
  --max-canary-elapsed-sec $MaxElapsedSec `
  --output $GateOutput
if ($LASTEXITCODE -ne 0) { throw "Stayin' Alive canary gate command failed." }

function Update-CanaryHistory {
  if ([string]::IsNullOrWhiteSpace($HistoryOutput)) { return }
  Write-Host "Refreshing canary history summary..." -ForegroundColor Cyan
  & $venvPython -m backend.ml.canary_history `
    --output $HistoryOutput `
    --top-n 10
  if ($LASTEXITCODE -ne 0) { throw "Canary history summary failed." }
}

$gate = Get-Content $GateOutput -Raw | ConvertFrom-Json
$canaryGate = $gate.metronome_canary_gate
if (-not $gate.metronome_canary_gate.passed) {
  Update-CanaryHistory
  $failedChecks = @($canaryGate.failed_checks)
  if ($failedChecks.Count -eq 0) {
    $canaryGate.checks.PSObject.Properties | ForEach-Object {
      if ($_.Value -ne $true) {
        $failedChecks += $_.Name
      }
    }
  }
  $summary = [string]$canaryGate.failure_summary
  if ([string]::IsNullOrWhiteSpace($summary)) {
    $summary = "Stayin' Alive 100% DAW-lock gate failed. Failed checks: $($failedChecks -join ', ')."
  }
  $observed = $canaryGate.observed | ConvertTo-Json -Depth 4
  throw "$summary Observed: $observed"
}

Update-CanaryHistory

Write-Host ""
Write-Host "Gate observed:" -ForegroundColor Cyan
Write-Host ("  verdict={0}, segment_lock={1:P1}, fixed_lock={2:P1}, raw_fixed_lock={3:P1}" -f `
  $canaryGate.observed.verdict, `
  [double]$canaryGate.observed.segment_locked_ratio, `
  [double]$canaryGate.observed.fixed_locked_ratio, `
  [double]$canaryGate.observed.raw_fixed_locked_ratio)
Write-Host ("  avg_error={0:N1}ms, elapsed={1:N1}s, continuity={2}, max_jump={3:N1}ms" -f `
  ([double]$canaryGate.observed.avg_error_after_sec * 1000.0), `
  [double]$canaryGate.observed.elapsed_sec, `
  $canaryGate.observed.warp_continuity, `
  ([double]$canaryGate.observed.max_window_offset_jump_sec * 1000.0))
if ($null -ne $canaryGate.observed.max_window_offset_jump_from_sec -and $null -ne $canaryGate.observed.max_window_offset_jump_to_sec) {
  Write-Host ("  jump_window={0:N1}s->{1:N1}s, offsets={2:N1}ms->{3:N1}ms" -f `
    [double]$canaryGate.observed.max_window_offset_jump_from_sec, `
    [double]$canaryGate.observed.max_window_offset_jump_to_sec, `
    ([double]$canaryGate.observed.max_window_offset_before_sec * 1000.0), `
    ([double]$canaryGate.observed.max_window_offset_after_sec * 1000.0))
}
Write-Host ("  diagnostics={0} ({1}), metronome_check={2}@{3:N1}bpm" -f `
  [int]$canaryGate.observed.daw_lock_diagnostic_issue_count, `
  $canaryGate.observed.daw_lock_diagnostic_summary, `
  $canaryGate.observed.metronome_check_filename, `
  [double]$canaryGate.observed.metronome_check_bpm)
if ($null -ne $canaryGate.observed.runtime_config) {
  Write-Host ("  runtime={0} + {1} + {2}, verify_top_k={3}" -f `
    $canaryGate.observed.runtime_config.inference_accelerator, `
    $canaryGate.observed.runtime_config.inference_candidate_strategy, `
    $canaryGate.observed.runtime_config.hybrid_search_strategy, `
    $canaryGate.observed.runtime_config.hybrid_verify_top_k)
}
Write-Host "Stayin' Alive 100% DAW-lock gate passed." -ForegroundColor Green
