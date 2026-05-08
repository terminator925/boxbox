# BoxBox

BoxBox is an audio quantization app that warps drifting songs to a fixed BPM so they line up cleanly in a DAW.

Copyright (c) 2026 Alexander Thaddeus Stepnowsky. Released under the MIT License. See [LICENSE](LICENSE).

BoxBox is intended to be run locally for reliable testing, development, and evaluation.

## Quick Start

BoxBox runs as:

- a Python/FastAPI backend
- a React frontend

Open the app at:

- `http://127.0.0.1:5173`

### Windows

```powershell
scripts/setup_windows.ps1
```

Then start the backend in one PowerShell window:

```powershell
scripts/run_backend.ps1
```

And the frontend in another:

```powershell
scripts/run_frontend.ps1
```

### macOS

```bash
./scripts/setup_macos.sh
```

Then start the backend in one Terminal window:

```bash
./scripts/run_backend_macos.sh
```

And the frontend in another:

```bash
./scripts/run_frontend_macos.sh
```

## Requirements

You need:

- `ffmpeg`
- `rubberband`
- `Python 3.11`
- `Node.js`

The setup scripts install or verify these for you.

## What it does

- Upload any audio file `ffmpeg` can decode
- Set target BPM + quantization resolution (quarter/eighth/sixteenth)
- Quantize with `dtw`, `ml`, or `hybrid` mode
- Preserve source sample rate and channel layout during processing
- Preserve stereo output when input is stereo (shared warp map on L/R)
- Export:
  - `quantized.wav` (browser-safe PCM WAV)
  - `quantized_master.flac` (lossless master when ffmpeg is available)
  - `quantized_source.<ext>` (source-matched container/codec when supported)
  - `metronome_check.wav` (quantized audio mixed with the fixed BPM click for quick DAW-lock listening checks)
  - `report.json` (segments + timing metrics)
  - `tempo_map.mid` (constant BPM + beat markers)
- Frontend includes progress tracking and A/B playback waveforms

Most users only need the quick start above. Everything below is optional deeper setup, verification, training, and development material.

## Detailed Windows Setup

1. Open PowerShell in this repo.
2. Run:

```powershell
scripts/setup_windows.ps1
```

3. In terminal 1, run backend:

```powershell
scripts/run_backend.ps1
```

4. In terminal 2, run frontend:

```powershell
scripts/run_frontend.ps1
```

If the local Vite build is blocked by native `esbuild` spawn permissions, regenerate the static frontend with the WASM fallback:

```powershell
cd frontend
$env:npm_config_cache="H:\BoxBox\.npm-cache"
npm run build:wasm
npm run verify:dist
```

Then serve the rebuilt static GUI without Vite:

```powershell
scripts/run_frontend_dist.ps1
```

This static frontend script also refuses stale `frontend/dist` output unless you rebuild with `-Build` or deliberately pass `-AllowStale`.

Or rebuild, verify, and serve it in one command:

```powershell
scripts/run_frontend_dist.ps1 -Build
```

To launch both backend and the static GUI together, rebuilding/verifying `frontend/dist` first:

```powershell
.\backend\.venv\Scripts\python.exe scripts/launch_gui.py
```

By default the app uses the promoted fast runtime path: `BOXBOX_INFER_ACCELERATOR=torch`, `BOXBOX_INFER_CANDIDATE_STRATEGY=core4_adaptive_plus`, and `BOXBOX_HYBRID_SEARCH_STRATEGY=core4`.

The GUI also shows an `Active Engine` panel from the live backend before you quantize, so you can confirm the running backend is actually using the promoted path instead of an older/stale setup.
The hero also shows a `Build Stamp` so you can tell whether the browser is showing the freshly rebuilt static GUI.

Use `--skip-frontend-build` only when you intentionally want to serve the existing static bundle.
When that flag is used, the launcher still blocks obviously stale static bundles if frontend source files are newer than `frontend/dist`.
For deliberate stale-bundle debugging only, add `--allow-stale-frontend`.

If you want to override the promoted runtime for debugging or oracle comparisons, either export env vars before launching or pass explicit launcher flags:

```powershell
.\backend\.venv\Scripts\python.exe scripts/launch_gui.py --infer-accelerator cuda --inference-candidate-strategy all --hybrid-search-strategy all
```

5. Open `http://127.0.0.1:5173` and test upload + quantize.

## Detailed macOS Setup

1. Open Terminal in this repo.
2. Run:

```bash
./scripts/setup_macos.sh
```

3. In terminal 1, run backend:

```bash
./scripts/run_backend_macos.sh
```

4. In terminal 2, run frontend:

```bash
./scripts/run_frontend_macos.sh
```

5. Open `http://127.0.0.1:5173` and test upload + quantize.

### Manual macOS setup

If you prefer to install dependencies yourself instead of using the helper scripts:

```bash
brew install ffmpeg rubberband node python@3.11
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r backend/requirements.txt
deactivate
cd frontend && npm install
```

Then launch the backend:

```bash
cd /path/to/boxbox
export BOXBOX_INFER_ACCELERATOR=torch
export BOXBOX_INFER_CANDIDATE_STRATEGY=core4_adaptive_plus
export BOXBOX_HYBRID_SEARCH_STRATEGY=core4
./backend/.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

And the frontend:

```bash
cd /path/to/boxbox/frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

## Quick Verification

With backend running:

```powershell
scripts/smoke_test.ps1 -AudioPath "C:\path\to\your\file.wav"
```

This uploads, quantizes with source-tempo defaults, downloads outputs, and verifies stereo preservation.

## Advanced Training And Evaluation

### Train ML model (optional)

Preview the training split, validation split, dataset-family mix, and generated-vs-real source mix before spending time on a run:

```powershell
scripts/train_model.ps1 -DryRun -Datasets "legacy_audio,groove_audio,maestro_audio" -MaxExamples 200
```

Inspect generated-vs-real training data percentages without decoding audio:

```powershell
scripts/dataset_inventory.ps1 -Examples "data/examples;data/examples_real_audio_musicnet"
```

Inventory a private sample/song stash as a read-only full-track test source. This excludes split stems with `(drums)`, `(bass)`, `(vocals)`, or `(other)` in the filename by default:

```powershell
scripts/inventory_sample_stash.ps1 -Root "H:\SAUCE (AUDIO)\SONG STASH\songs n samples"
```

For a stricter likely-full-track benchmark manifest, exclude obvious loops and require at least 60 seconds of probed duration:

```powershell
scripts/inventory_sample_stash.ps1 -Root "H:\SAUCE (AUDIO)\SONG STASH\songs n samples" -Output data/sample_stash_full_track_candidates.json -ExcludeLikelyLoops -MinDurationSec 60
```

Build tiered benchmark corpora from that manifest:

```powershell
scripts/build_sample_corpora.ps1 -IncludeFull
```

Classify the stash into likely older/unquantized versus likely modern/grid-produced tracks using filename/path/year/metadata clues:

```powershell
scripts/classify_sample_era.ps1 -ProbeTags
```

Run an audio gridness probe over the likely older corpus to confirm which tracks actually behave like drifting/non-grid recordings:

```powershell
scripts/analyze_sample_gridness.ps1 -Manifest data/benchmark_corpus_stash_likely_older_with_tags.json -ClipDuration 45
```

For stronger evidence, sample multiple windows across each track:

```powershell
scripts/analyze_sample_gridness.ps1 -Manifest data/benchmark_corpus_stash_likely_older_with_tags.json -ClipDuration 30 -Windows 3
```

Run a quick proxy benchmark against the stash smoke corpus:

```powershell
scripts/run_benchmarks.ps1 -AudioManifest data/benchmark_corpus_stash_smoke.json -ProxyOnly -ClipDuration 30
```

For longer stash corpora, write incremental results and resume after interruption:

```powershell
scripts/run_benchmarks.ps1 -AudioManifest data/benchmark_corpus_stash_audio_drifting_older79_windows3.json -ProxyOnly -SkipValidation -ClipDuration 10 -Output outputs/drifting_proxy_report.json -IncrementalOutput outputs/drifting_proxy_report.partial.json -Resume
```

If a broad private manifest contains mislabeled or corrupt audio, add `-SkipAudioErrors` to record failed files in the report instead of stopping the whole run.

Benchmark runs now default to the promoted fast path: `-InferenceAccelerator torch -InferenceCandidateStrategy core4_adaptive_plus -HybridSearchStrategy core4`.

For faster dev-only model-routing experiments, add `-MaxInferenceModels 2` to evaluate only the top two candidate checkpoints. Leave this unset for quality/default validation.

For oracle/full-search comparisons, override the promoted benchmark defaults explicitly:

```powershell
scripts/run_benchmarks.ps1 -AudioManifest data/benchmark_corpus_stash_smoke.json -ProxyOnly -ClipDuration 30 -InferenceCandidateStrategy all -HybridSearchStrategy all
```

Summarize a benchmark report into hardest tracks, regressions, and best improvements:

```powershell
scripts/summarize_benchmark_report.ps1 -Report outputs/benchmark_report.json -Output outputs/benchmark_summary.json
```

Summaries also surface slowest tracks and their slowest benchmark stage when reports include `processing_timing`, which helps target runtime optimization.

Gate a faster benchmark mode against the full quality baseline before promoting it as a product default:

```powershell
scripts/gate_benchmark_candidate.ps1 -BaselineReport outputs/drifting20_proxy_torch5_verified_hybrid_report.json -CandidateReport outputs/drifting20_proxy_torch5_m2_report.json -Output outputs/drifting20_m2_speed_gate.json -MaxAvgRegressionSec 0.001 -MaxPerFileRegressionSec 0.003 -MinSpeedupPct 20
```

The gate compares hybrid timing error and processing speed over shared tracks, then reports whether average regression, worst per-file regression, and required speedup all pass.

Run a compact readiness report across dataset inventory, manifest validation, leaderboard, comparison, and promotion dry-run:

```powershell
scripts/model_readiness.ps1 -Examples "data/examples;data/examples_real_audio_musicnet" -ModelsDir models
```

Create a guarded smoke checkpoint to test model registry/manifest plumbing without a full training run:

```powershell
scripts/create_smoke_checkpoint.ps1 -Output outputs/smoke/boxbox_smoke.pt
scripts/model_readiness.ps1 -ModelsDir outputs/smoke -MaxExamples 10
```

Smoke checkpoints default to `status=smoke_test`, so they appear in readiness/leaderboard reports but are blocked from promotion by `status_not_trained`.

Or run the whole lifecycle smoke check in one command:

```powershell
scripts/model_lifecycle_smoke.ps1
```

List trained model manifests by validation score:

```powershell
scripts/model_leaderboard.ps1 -Limit 10
```

Leaderboard rows include `warnings`; `low_val_examples` means the checkpoint is visible for inspection but does not have enough validation examples for safe promotion. Source-mix warnings such as `no_real_training_data` and `unknown_source_mix` are also shown so synthetic-only or poorly identified training runs do not look production-ready. The default evidence floor is four validation examples and at least `1%` real training data:

```powershell
scripts/model_leaderboard.ps1 -MinValExamples 8 -MinRealPct 5
```

Audit model manifests that are missing from ranking:

```powershell
scripts/model_leaderboard.ps1 -Validate
```

Restore missing sidecar manifests from checkpoints that embed `training_manifest` metadata:

```powershell
scripts/model_leaderboard.ps1 -SyncEmbedded
scripts/model_leaderboard.ps1 -SyncEmbedded -Apply
```

Compare the top candidate against the active model manifest:

```powershell
scripts/compare_models.ps1
```

Comparison blocks candidates below the validation-evidence floor or with blocking training-data warnings:

```powershell
scripts/compare_models.ps1 -MinValExamples 8 -MinRealPct 5
```

Preview promoting the best safe manifest-backed checkpoint to the active model:

```powershell
scripts/promote_model.ps1
```

Promotion scans ranked candidates in order and skips models that fail the comparison or data-quality gates, reporting `rejected_candidate_count` in the dry-run output.

Apply promotion only after reviewing the dry-run output:

```powershell
scripts/promote_model.ps1 -Apply
```

Promotion requires the manifest comparison gate to pass by default; tune the minimum score improvement if needed:

```powershell
scripts/promote_model.ps1 -MinScoreImprovementPct 2.5
```

Promotion also requires the candidate to meet the validation-evidence and real-data floors:

```powershell
scripts/promote_model.ps1 -MinValExamples 8 -MinRealPct 5
```

Train with the repo-local warp-target cache and balanced sampling:

```powershell
scripts/train_model.ps1 -Epochs 12 -BatchSize 2 -Device cpu -WarmCache -DatasetBalance inverse
```

To synthesize paired training data automatically first:

```powershell
scripts/generate_synthetic_examples.ps1 -Count 64 -Clean
```

Or generate and train in one step:

```powershell
scripts/train_model.ps1 -GenerateCount 64 -CleanSyntheticData -Epochs 12 -BatchSize 2 -Device cpu
```

Useful training controls:

- `-Examples`: one examples directory, or multiple directories separated by the OS path separator (`;` on Windows)
- `-DryRun`: print train/validation counts and dataset-family mix without loading audio or writing a model
- `-Datasets`: comma-separated dataset families such as `legacy_audio,groove_audio,maestro_audio,synthetic`
- `-DatasetBalance inverse`: upsample underrepresented dataset families during training
- `-WarmCache`: build/reuse disk-cached warp targets before training for more predictable epoch timing
- `-MinImprovementPct`, `-MaxAfterSec`, `-MinEventCount`, `-MaxExamples`: filter weak or oversized examples during experiments; capped examples are selected in a deterministic family-balanced order
- `-InitModel`: continue training from an existing checkpoint
- `-ManifestOutput`: write the training/dry-run manifest JSON to an explicit path; trained models also write `<model>.training.json` by default
- `-SkipEvaluate`: train only, useful for quick smoke runs

Dataset inventory groups examples as `real` when they come from real-audio families or audio source files, `generated` when they are synthetic or MIDI-import derived, and `unknown` when metadata is insufficient.

To import paired examples from downloaded public MIDI datasets:

```powershell
scripts/import_public_examples.ps1 -MaestroCount 24 -GrooveCount 24 -EGMDCount 24
```

Model output path: `models/boxbox_latest.pt`

If no model exists, app still works with onset-grid fallback.

## Training Data Format (`data/examples`)

Each example must be a folder containing:

- `original.wav`
- `warped.wav`
- optional `meta.json`

Example structure:

```text
data/examples/
  song_001/
    original.wav
    warped.wav
  song_002/
    original.wav
    warped.wav
```

## Key Scripts

- `scripts/setup_windows.ps1`: install backend and frontend dependencies on Windows
- `scripts/setup_macos.sh`: install backend and frontend dependencies on macOS
- `scripts/run_backend.ps1`: start the backend on Windows
- `scripts/run_frontend.ps1`: start the frontend on Windows
- `scripts/run_backend_macos.sh`: start the backend on macOS
- `scripts/run_frontend_macos.sh`: start the frontend on macOS
- `scripts/smoke_test.ps1`: run a quick end-to-end API smoke test
- `scripts/train_model.ps1`: train or dry-run the ML model pipeline

## Advanced Validation

These workflows are mainly for deeper regression testing, benchmark runs, and model validation.

Run the main fixed-tempo DAW-lock canary:

```powershell
scripts/run_stayin_alive_canary.ps1
```

Summarize recent canary history:

```powershell
scripts/summarize_canary_history.ps1 -Output outputs/canary_history_summary_latest.json
```

Run a broader manifest-based canary suite:

```powershell
scripts/run_metronome_canary_suite.ps1 -AudioManifest data/benchmark_corpus_stash_smoke.json -MaxCanaryFiles 3 -SkipAudioErrors
```

Summarize an existing suite report:

```powershell
scripts/summarize_metronome_canary_suite.ps1 -Report outputs/metronome_canary_suite_latest.json
```

## Current Limitations

- Fallback warp path is interpolation-based time mapping (Rubber Band used if available).
- Tempo-map MIDI is simple marker track at constant BPM.
- ML confidence is a lightweight proxy, not calibrated uncertainty.
- No deployment/auth/billing layer yet.

## Authorship

Creator and copyright holder:

- Alexander Thaddeus Stepnowsky

## Product Roadmap

- Production deploy target (container + managed object storage)
- Improved phase-vocoder/rubberband integration controls
- Better segment-aware confidence blending
- Plugin/VST-facing API client
- Dataset tooling + evaluation dashboards
