param(
  [int]$Resolution = 8,
  [string]$AudioManifest = "",
  [string]$Output = "outputs/benchmark_report.json",
  [string]$IncrementalOutput = "",
  [ValidateSet("auto", "npu", "gpu", "cpu", "torch", "cuda")]
  [string]$InferenceAccelerator = "torch",
  [ValidateSet("", "all", "core4", "core4_adaptive", "core4_adaptive_plus", "core4_adaptive_plus_qf", "core5")]
  [string]$InferenceCandidateStrategy = "core4_adaptive_plus",
  [ValidateSet("", "all", "routed", "routed_top1", "routed_top2", "core4", "core5")]
  [string]$HybridSearchStrategy = "core4",
  [int]$HybridVerifyTopK = 0,
  [int]$MaxInferenceModels = 0,
  [switch]$ProxyOnly,
  [switch]$SkipAudioErrors,
  [switch]$FastProxyHybridScoring,
  [switch]$Resume,
  [switch]$SkipValidation,
  [double]$ClipDuration = 0.0
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

if (-not (Test-Path "benchmarks")) {
  New-Item -ItemType Directory -Force -Path "benchmarks" | Out-Null
}

Write-Host "Running validation and real-audio benchmarks..." -ForegroundColor Cyan
$benchmarkArgs = @(
  "-m", "backend.ml.benchmark",
  "--examples", "data/examples",
  "--audio", "stayin-alive-serban-mix.wav",
  "--resolution", "$Resolution",
  "--output", $Output
)
if (-not $AudioManifest) { $benchmarkArgs += @("--audio-dir", "benchmarks") }
if ($AudioManifest) { $benchmarkArgs += @("--audio-manifest", $AudioManifest) }
if ($IncrementalOutput) { $benchmarkArgs += @("--incremental-output", $IncrementalOutput) }
if ($InferenceAccelerator) { $benchmarkArgs += @("--inference-accelerator", $InferenceAccelerator) }
if ($InferenceCandidateStrategy) { $benchmarkArgs += @("--inference-candidate-strategy", $InferenceCandidateStrategy) }
if ($HybridSearchStrategy) { $benchmarkArgs += @("--hybrid-search-strategy", $HybridSearchStrategy) }
if ($HybridVerifyTopK -gt 0) { $benchmarkArgs += @("--hybrid-verify-topk", "$HybridVerifyTopK") }
if ($MaxInferenceModels -gt 0) { $benchmarkArgs += @("--max-inference-models", "$MaxInferenceModels") }
if ($ProxyOnly) { $benchmarkArgs += "--proxy-only" }
if ($SkipAudioErrors) { $benchmarkArgs += "--skip-audio-errors" }
if ($FastProxyHybridScoring) { $benchmarkArgs += "--fast-proxy-hybrid-scoring" }
if ($Resume) { $benchmarkArgs += "--resume" }
if ($SkipValidation) { $benchmarkArgs += "--skip-validation" }
if ($ClipDuration -gt 0) { $benchmarkArgs += @("--clip-duration", "$ClipDuration") }

& $venvPython @benchmarkArgs
if ($LASTEXITCODE -ne 0) { throw "Benchmark run failed." }
