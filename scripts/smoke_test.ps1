param(
  [string]$AudioPath
)
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if (-not $AudioPath) {
  $AudioPath = Read-Host "Enter full path to an audio file"
}

if (-not (Test-Path $AudioPath)) {
  throw "File not found: $AudioPath"
}

Write-Host "Uploading audio..."
$uploadJson = curl.exe -s -X POST "http://localhost:8000/api/upload" -F "file=@$AudioPath"
$upload = $uploadJson | ConvertFrom-Json
if (-not $upload.job_id) {
  throw "Upload failed: $uploadJson"
}

Write-Host "Job ID: $($upload.job_id)"
Write-Host "Running quantize with defaults..."
$targetBpm = if ($upload.estimated_bpm) { [double]$upload.estimated_bpm } else { 100 }
$body = @{ job_id=$upload.job_id; target_bpm=$targetBpm; resolution=8; groove_preserve=50; mode="hybrid" } | ConvertTo-Json
$quant = Invoke-RestMethod -Method POST -Uri "http://localhost:8000/api/quantize" -ContentType "application/json" -Body $body

$jobId = $quant.job_id
$outDir = Join-Path $RepoRoot "outputs/$jobId"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$downloadDir = Join-Path $outDir "downloads"
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null

Write-Host "Downloading outputs..."
Invoke-WebRequest "http://localhost:8000/api/download/$jobId/$($quant.output_files.browser_audio)" -OutFile (Join-Path $downloadDir $quant.output_files.browser_audio) | Out-Null
Invoke-WebRequest "http://localhost:8000/api/download/$jobId/$($quant.output_files.master_audio)" -OutFile (Join-Path $downloadDir $quant.output_files.master_audio) | Out-Null
if ($quant.output_files.source_match_audio) {
  Invoke-WebRequest "http://localhost:8000/api/download/$jobId/$($quant.output_files.source_match_audio)" -OutFile (Join-Path $downloadDir $quant.output_files.source_match_audio) | Out-Null
}
Invoke-WebRequest "http://localhost:8000/api/download/$jobId/report.json" -OutFile (Join-Path $downloadDir "report.json") | Out-Null
Invoke-WebRequest "http://localhost:8000/api/download/$jobId/tempo_map.mid" -OutFile (Join-Path $downloadDir "tempo_map.mid") | Out-Null

$venvPython = Join-Path $RepoRoot "backend/.venv/Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
  $venvPython = Join-Path $RepoRoot "venv/Scripts/python.exe"
}
$checkCmd = "import soundfile as sf; import sys; a,_=sf.read(r'$AudioPath', always_2d=True); b,_=sf.read(r'$(Join-Path $downloadDir $quant.output_files.browser_audio)', always_2d=True); print(a.shape[1], b.shape[1])"
$ch = & $venvPython -c $checkCmd
$parts = $ch.Trim().Split(' ')
$inCh = [int]$parts[0]
$outCh = [int]$parts[1]

if ($inCh -eq 2 -and $outCh -ne 2) {
  throw "Stereo verification failed: input=$inCh output=$outCh"
}

Write-Host "Smoke test passed." -ForegroundColor Green
Write-Host "Output folder: $outDir"
Write-Host "Downloaded copies: $downloadDir"
Write-Host "Stereo check: input channels=$inCh output channels=$outCh"
