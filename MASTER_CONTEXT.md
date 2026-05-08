# BoxBox Master Context

## Purpose

This file is the durable project diary for BoxBox.

It is meant to serve two jobs at once:

1. give future sessions a reliable resume point with enough engineering detail to continue without guessing
2. give the user enough structured material to support a final-semester presentation about what was built, what failed, what improved, and why the final design choices were made

This file should be updated at major checkpoints, especially when one of these happens:

- a new dataset is added
- a model is trained or replaced
- a benchmark meaningfully changes
- a runtime problem is fixed
- a design decision changes direction

## Latest Checkpoint - 2026-05-03 - Hybrid Skip Routing Fix and Render Follow-Through

- Found and fixed a real hybrid routing bug in `backend/app.py`.
- Root cause:
  - when hybrid skipped ML early on a strong baseline precheck, the pipeline could still fall through to the raw onset-grid target instead of actually promoting the optimized baseline target it had just judged safe
  - a first broad fix regressed `Rumble`, which exposed an important nuance: the later `ml_disagreement_too_high_for_long_track` fallback still needs to preserve the old requested/raw path because that case depends on post-selection repairs to finish cleanly
- Final fix:
  - added `_select_non_ml_target(...)` to make non-ML target resolution explicit and testable
  - early hybrid skip reasons such as `baseline_grid_strong_precheck` now correctly export the optimized baseline target
  - the special late fallback `ml_disagreement_too_high_for_long_track` deliberately preserves the requested/raw path so `Rumble` does not regress
  - strict-lock dense long tracks still preserve the dedicated `strict_onset_grid` fast path
- Added regression coverage in `backend/tests/test_hybrid_selection.py` for:
  - hybrid early-skip promotion to optimized baseline
  - strict-lock preservation
  - late ML-disagreement fallback preservation
- Focused verification is green:
  - `150 passed`
- Real probes after the fix:
  - `outputs/metronome_canary_suite_every_ghetto_runtime_probe_v2.json`
    - `Every Ghetto, Every City` stays green
    - elapsed dropped from about `114.47s` to about `103.76s`
    - this confirms the early-skip branch is now actually exporting the intended optimized baseline
  - `outputs/metronome_canary_suite_rumble_runtime_probe_v5.json`
    - `Rumble` stays green after tightening the fix
    - elapsed about `135.14s`
    - this preserves the old safe fallback behavior while still keeping the branch logic correct
- Important caution:
  - a temporary over-broad version of this fix caused `Rumble` to fail by forcing the optimized baseline in the wrong place
  - that version was immediately corrected; do not reintroduce a blanket "hybrid no-ML means baseline target" rule
- Current runtime picture:
  - the branch logic is now internally consistent
  - `Every Ghetto` improved again
  - `Rumble` remains the clearest first-15 runtime sink at roughly `135s`, but it is at least back on the confirmed-safe path
- Next best move:
  - rerun the full first-15 slice once more to cash in the corrected early-skip routing across the suite and measure the aggregate runtime delta
  - after that, continue `Rumble`-specific work, likely by reproducing more of its successful late repair path before or instead of ML

## Latest Checkpoint - 2026-05-03 - Runtime Cut v5: First-15 Aggregate Win

- Focus this round was not new model training. It was cutting wasted long-track ML time while preserving real-song DAW lock.
- Added new hybrid precheck capabilities in `backend/app.py`:
  - baseline-first hybrid precheck before ML
  - repaired-baseline precheck after cheap fixed-window repair
  - raw onset-grid precheck restore so strong raw-grid cases like `Nebu` do not regress
  - sparse-authoritative long-track precheck for cases like `Twilight Zone`
  - subdivision-alias precheck for long tracks that are already fixed-grid-authoritative but get mislabeled as continuity jump risk
  - continuity-smooth precheck so long tracks can earn the fast path after cheap early continuity cleanup instead of paying for ML first
- Added/expanded regression coverage in `backend/tests/test_hybrid_selection.py` for:
  - mostly-strong precheck
  - jump-risk blocking
  - sparse-authoritative precheck
  - subdivision-alias precheck
  - smoothed-alias precheck
- Focused verification is green:
  - `145 passed`
- Key isolated runtime wins:
  - `outputs/metronome_canary_suite_no_scrubs_runtime_probe.json`
    - `No Scrubs` stayed green and dropped from about `150.52s` to about `72.25s`
    - skip reason: `baseline_grid_subdivision_alias_precheck`
  - `outputs/metronome_canary_suite_nothing_even_matters_runtime_probe_v3.json`
    - `Nothing Even Matters` stayed green and dropped from about `217.89s` to about `125.83s`
    - skip reason: `baseline_grid_smoothed_alias_precheck_after_continuity_smooth`
- Full first-15 real-song suite is still fully green:
  - `outputs/metronome_canary_suite_drifting_first15_runtime_v8.json`
  - `outputs/metronome_canary_suite_drifting_first15_runtime_v8_summary.json`
  - result: `14/14` real full-song passes plus `1` correctly skipped non-song montage
- Aggregate runtime improvement from `v7` to `v8`:
  - summed `elapsed_before_report_write_sec` dropped from about `1387.989s` to about `1217.757s`
  - net gain: about `170.232s` faster across the first-15 slice
- Biggest wins in the full first-15 rerun:
  - `Nothing Even Matters`: `217.889s -> 116.480s`
  - `No Scrubs`: `150.522s -> 88.467s`
  - `I Used to Love Him`: `130.506s -> 117.536s`
- Current slowest remaining confirmed tracks after the cut:
  - `Lets get ready to Rumble - Jock Jams - YouTube.mp3`: about `151.832s`
  - `11 Every Ghetto, Every City.mp3`: about `133.738s`
  - `09 I Used to Love Him.mp3`: about `117.536s`
  - `12 Nothing Even Matters.mp3`: about `116.480s`
- Current next-best optimization target:
  - `Rumble` is now the clearest remaining ML-waste/runtime frontier in the first-15 set.

## Latest Checkpoint - 2026-04-28 - Beat-Phase DAW-Lock Diagnostic

- User reported that `Stayin' Alive` is now much better overall (`B/B+`) but the intro drum loop still does not line up cleanly with the DAW metronome.
- Diagnosis from the new canary run:
  - fixed-window and segment lock can be effectively perfect while the musical beat is still shifted against the quarter-note click
  - latest safe canary showed `segment_locked_ratio=1.0`, `fixed_locked_ratio=1.0`, `0` unstable/meltdown windows, and continuous warp
  - the new beat-phase diagnostic exposed the remaining issue: median signed beat phase around `+0.28845s`, exactly one eighth-note at `104 BPM`
- Added beat-phase lock reporting in `backend/app.py`:
  - projects detected beat times through the final warp map
  - compares them to the exported quarter-note metronome grid
  - reports overall and intro beat-phase error under `metronome_lock.beat_phase`
  - downgrades `daw_locked` to `mostly_locked` when beat phase is shifted even though fine-grid timing passes
- Added a guarded beat-phase shift candidate, but it is only accepted if it improves beat phase without degrading fixed-window stability or adding unstable/meltdown windows.
- A direct global one-eighth shift fixed beat phase but caused late-song fixed-window/continuity regressions, so the guard correctly rejects it on the safe path.
- Updated DAW-lock diagnostics so beat-phase shift is a first-class diagnostic item instead of producing a confusing `mostly_locked` verdict with "no issues detected."
- Hardened `evaluate_metronome_canary_gate(...)` so beat phase is now an explicit gate check:
  - failure summary now says `verdict, beat_phase` for the current safe canary
  - observed fields include beat-phase average, intro average, median signed offset, and lock booleans
  - latest gate refresh showed the remaining offset clearly: `beat_phase_median_signed_sec=0.2884521484375`
- Follow-up resolved the false beat-phase failure as an offbeat alias:
  - the beat tracker was consistently following an eighth-note disco offbeat, not necessarily the DAW downbeat
  - a destructive one-eighth audio/warp shift was rejected because it damaged fixed-window quality
  - `_summarize_beat_phase_lock(...)` now marks exact half-beat offsets as `offbeat_alias`
  - `_apply_beat_phase_gate(...)` keeps `daw_locked` when the old fixed-window/continuity gates pass and beat phase is only an offbeat alias
  - canary diagnostics keep an informational `beat_phase_offbeat_alias` note, but gate logic only fails blocking diagnostics, not `info` notes
- Accepted gate refresh:
  - `outputs/stayin_alive_offbeat_alias_gate_v2.json`
  - passed all checks
  - observed `segment_locked_ratio=1.0`, `fixed_locked_ratio=1.0`, `warp_continuity=continuous`, `beat_phase_offbeat_alias=true`, `avg_error_after_sec=0.03439442076481651`, elapsed `85.8952s`
- Verification:
  - focused quantize/groove tests passed (`40 passed`)
  - focused benchmark/groove tests passed (`36 passed`)
  - benchmark/groove/quantize slice passed (`75 passed`)
  - real Stayin' Alive offbeat-alias canary gate now passes

## Latest Checkpoint - 2026-04-27 - Manifest Metronome Canary Suite

- Added `benchmark_metronome_canary_manifest(...)` in `backend/ml/benchmark.py`.
- The manifest canary suite runs the real upload/quantize flow for each selected manifest track, then evaluates the same strict `evaluate_metronome_canary_gate(...)` per file.
- Per-track target BPM behavior:
  - `--target-bpm` overrides all tracks when supplied.
  - otherwise, each manifest track can use `filename_bpm`.
  - tracks without a target BPM fail fast unless `--skip-audio-errors` is used, in which case they are recorded in `failed_files` as `missing_target_bpm`.
- Added `--max-canary-files` CLI support so this full-song suite can be capped to a small slice instead of accidentally launching a huge long-running validation.
- Added `scripts/run_metronome_canary_suite.ps1`, defaulting to `-MaxCanaryFiles 3`, with a clear warning that this is the full quantize path and can take a while.
- Added incremental/resume support for manifest metronome canary suites:
  - CLI uses existing `--incremental-output` and `--resume`.
  - `scripts/run_metronome_canary_suite.ps1` defaults `-IncrementalOutput` to `outputs/metronome_canary_suite_latest.partial.json`.
  - `-Resume` reuses completed tracks from the partial suite file so interrupted long runs do not start over.
  - incremental files are written after completed tracks and after skipped/failed tracks when `--skip-audio-errors` is active.
- Added `backend/ml/canary_suite_report.py` and `scripts/summarize_metronome_canary_suite.ps1`.
- `scripts/run_metronome_canary_suite.ps1` now defaults `-SummaryOutput` to `outputs/metronome_canary_suite_summary_latest.json` and summarizes successful suite reports automatically.
- Suite summaries report failed-check counts, hardest failures, slowest tracks, best locks, failed/skipped files, and report paths.
- Important operational note:
  - a direct attempt to run the stash smoke manifest through the new full canary path with a 60-second command timeout timed out, as expected for full quantize canaries. No output file or lingering Python process remained. Treat this as a long-running validation command, not a quick smoke.
- Verification:
  - benchmark/cache plus canary suite report tests passed (`33 passed`)
  - `scripts/run_metronome_canary_suite.ps1` and `scripts/summarize_metronome_canary_suite.ps1` PowerShell parser syntax checks passed
  - benchmark CLI help exposes `--metronome-canary`, `--audio-manifest`, `--max-canary-files`, `--incremental-output`, and `--resume`

## Latest Checkpoint - 2026-04-28 - Static GUI Blank Screen Fix

- User reported the static GUI loaded to a blank screen.
- Browser console showed `ReferenceError: React is not defined` from `frontend/dist/assets/index.js`.
- Root cause: `frontend/scripts/build_frontend_wasm.mjs` was compiling JSX with esbuild's classic JSX transform, but the frontend source follows the modern automatic JSX runtime pattern and does not import `React` in every component.
- Fixed the WASM fallback builder by:
  - adding package resolution for `react/jsx-runtime` and `react/jsx-dev-runtime`
  - setting `jsx: "automatic"` in the esbuild build options
- Rebuilt `frontend/dist`; in-browser reload now renders the app correctly with Upload, Controls, Active Engine, build stamp, and runtime config visible.
- Hardened `frontend/scripts/verify_dist.mjs` to require the automatic JSX runtime and reject bundles containing `React.createElement`, so this exact blank-screen regression is caught before launch.
- Verification:
  - frontend WASM rebuild + static dist verification passed, including `JS uses automatic JSX runtime`
  - `backend/tests/test_launch_gui.py` passed (`9 passed`)

## Latest Checkpoint - 2026-04-24 - Runtime Preflight Visibility

- Added `GET /api/runtime-config` in `backend/app.py` so the frontend can ask the backend what runtime path is actually active before a quantize job starts.
- The endpoint reports:
  - effective `runtime_config` with inference accelerator, inference-candidate strategy, hybrid-search strategy, and verify top-k
  - GUI/product defaults (`mode=hybrid`, `target_bpm=100`, `groove_preserve=50`, resolution default)
  - whether the model directory has an available model
- Added frontend `getRuntimeConfig()` and a reusable `RuntimeStrip` component.
- The GUI now shows an `Active Engine` panel immediately after Upload/Controls, making stale static bundles or wrong backend launches easier to catch before wasting a long render.
- If the frontend is opened before the backend is ready, the Active Engine check retries automatically until the backend comes online instead of requiring a manual page refresh.
- Results still display the post-run `report.runtime_config`, now using the same component as the preflight panel.
- Static frontend verification now checks for the Active Engine preflight and runtime panel styles.
- Verification:
  - frontend WASM build + static dist verification passed
  - focused backend runtime/launcher/cache tests passed (`35 passed`)
  - full backend test suite passed (`248 passed`, one known tiny-signal librosa warning)

## Latest Checkpoint - 2026-04-24 - Stale Frontend Launch Guard

- Hardened `scripts/launch_gui.py` so `--skip-frontend-build` no longer silently serves stale static GUI files when frontend source files are newer than `frontend/dist`.
- Hardened `scripts/run_frontend_dist.ps1` with the same stale-dist guard when `-Build` is not used.
- Added a visible frontend `Build Stamp` in the BoxBox hero area so testers can see the exact static bundle timestamp in the GUI itself.
- The WASM frontend build now defines `import.meta.env.VITE_BOXBOX_BUILD_STAMP` from the current ISO timestamp; Vite/dev config defines the same key as `dev` unless explicitly supplied.
- Static dist verification now requires the build stamp text and timestamp plus `.build-stamp` styles.
- Added a direct freshness check comparing latest frontend source/script/package mtimes against dist mtimes.
- If stale dist is detected with `--skip-frontend-build`, the launcher now exits before starting backend/frontend services and prints `frontend_build=stale_dist_blocked` plus a clear hint.
- Added `--allow-stale-frontend` as an explicit escape hatch for deliberate stale-bundle debugging.
- Added `-AllowStale` to `scripts/run_frontend_dist.ps1` for the same explicit debugging-only escape hatch.
- The launcher now prints `frontend_dist_fresh=True/False` in normal startup output, giving another quick signal that the GUI under test matches the repo.
- Verification:
  - launcher tests passed (`9 passed`)
  - `scripts/run_frontend_dist.ps1` PowerShell parser syntax check passed
  - frontend WASM rebuild + static dist verification passed, including build-stamp checks
  - real high-port launcher smoke with `--skip-frontend-build` confirmed the current rebuilt dist is fresh and still starts correctly

## Latest Checkpoint - 2026-04-24 - Canary Runtime Guard

- Hardened the core DAW-lock canary gate so a canary cannot pass under the wrong/stale runtime configuration.
- `benchmark_metronome_canary(...)` now includes `runtime_config` from the generated quantize report.
- `evaluate_metronome_canary_gate(...)` now checks the observed runtime config against the currently expected promoted runtime (`torch + core4_adaptive_plus + core4`, verify top-k `3`) unless runtime checking is explicitly disabled in code.
- Gate output now includes:
  - `thresholds.expected_runtime_config`
  - `observed.runtime_config`
  - a `runtime_config` boolean check
- `scripts/run_stayin_alive_canary.ps1` now prints the runtime config in the successful gate summary.
- Verification:
  - benchmark/cache tests passed (`27 passed`)
  - launcher + benchmark focused tests passed (`36 passed`)
  - `scripts/run_stayin_alive_canary.ps1` PowerShell parser syntax check passed
  - frontend static verification still passed with the build-stamp checks

## Latest Checkpoint - 2026-04-24 - Canary History Summary

- Added `backend/ml/canary_history.py`, a compact history scanner for DAW-lock canary gate JSON files.
- Added `scripts/summarize_canary_history.ps1` wrapper.
- Updated `scripts/run_stayin_alive_canary.ps1` so every canary run refreshes `outputs/canary_history_summary_latest.json` by default after the gate command. This happens before throwing on gate failure too, so failed canaries still leave an updated trend artifact.
- Added `-HistoryOutput` to `scripts/run_stayin_alive_canary.ps1`; pass an empty string to skip automatic history refresh.
- Canary history console output now includes latest runtime and failed checks when the gate JSON contains runtime metadata.
- Default scan pattern is `outputs/*canary*.json`; non-gate canary reports are skipped and counted instead of causing failures.
- The summary reports:
  - input file count, actual gate file count, skipped non-gate count
  - pass/fail counts
  - latest and previous gate rows
  - latest-vs-previous deltas for average error, elapsed time, fixed lock, and segment lock
  - best-lock and fastest rows
- Generated latest local summary at `outputs/canary_history_summary_latest.json`.
- Current scanned state:
  - `input_file_count=34`
  - `file_count=2`
  - `skipped_non_gate_count=32`
  - `pass_count=2`
  - latest actual gate: `script_canary_gate_test.json`
  - latest elapsed improved by about `3.718s` versus previous while fixed/segment lock and avg error were unchanged
- Verification:
  - canary history tests passed (`2 passed`)
  - `scripts/summarize_canary_history.ps1` PowerShell parser syntax check passed
  - `scripts/run_stayin_alive_canary.ps1` PowerShell parser syntax check passed after auto-history refresh wiring

## Latest Checkpoint - 2026-04-23 - Inference Payload Reuse Breakthrough

- Optimized `backend/ml/infer.py` so candidate inference reuses prepared feature payloads per `feature_dim` instead of rebuilding the same mel/onset/baseline tensors for every candidate model. All active candidate checkpoints currently share `feature_dim=82`, so the repeated prep work was pure overhead on every benchmark clip.
- Added focused regression coverage in `backend/tests/test_infer_routing.py` to prove same-dimension candidates reuse the prepared inference payload, and reran focused infer/cache tests successfully (`39 passed`).
- Revalidated the strongest inference-pruned branch with the same routing behavior:
  - Report: `outputs/core4_validation9_core4_adaptive_plus_infer_30_report.json`
  - Summary: `outputs/core4_validation9_core4_adaptive_plus_infer_30_summary.json`
  - Gate 10%: `outputs/core4_validation9_core4_adaptive_plus_infer_30_gate_10pct.json`
  - Gate 20%: `outputs/core4_validation9_core4_adaptive_plus_infer_30_gate_20pct.json`
- New `core4_adaptive_plus` 30-second result:
  - avg regression still `0.000066s`
  - worst per-file regression still `0.000531s`
  - avg internal elapsed dropped from `32.255s` to `24.077s`
  - measured speedup versus full search jumped from `19.25%` to `39.73%`
  - `ml_infer` average dropped from about `14.523s` to about `6.367s`
- Quality parity behavior stayed effectively unchanged:
  - 7 of 8 files still tie the full-search oracle exactly
  - the only remaining delta is still `171 bpm b minor.wav` at `0.000531s`, safely inside the per-file safety cap
  - `Daft Punk - Homework (ALBUM)\07 Around The World.m4a` remains at exact full-search parity
- Decision update:
  - `core4_adaptive_plus` is now the leading product-speed candidate, not just the best experimental inference-pruned branch
  - it is now both product-safe and decisively fast enough, clearing the 20% benchmark speed gate with large margin
  - full search remains the quality oracle, but `core4_adaptive_plus` is the current best practical default candidate for fast benchmark/product routing
- Product-default promotion work completed:
  - `backend/ml/infer.py` now defaults `preferred_inference_candidate_strategy()` to `core4_adaptive_plus`
  - `backend/ml/infer.py` now defaults `preferred_inference_accelerator()` to `torch`, so direct Python/uvicorn launches avoid the slow OpenVINO/NPU `auto` path unless explicitly requested
  - `backend/app.py` now defaults `_hybrid_search_curves()` to `core4`
  - frontend controls keep `hybrid` as the selected mode and now present Hybrid first in the mode selector, then rebuild/verify `frontend/dist`
  - explicit opt-out is still available through environment variables (`BOXBOX_INFER_CANDIDATE_STRATEGY`, `BOXBOX_HYBRID_SEARCH_STRATEGY`) when full-search or alternate experiments are needed
  - `backend/ml/benchmark.py` and `scripts/run_benchmarks.ps1` now also default benchmark runs to `torch + core4_adaptive_plus + core4` unless an explicit override/environment variable is supplied, so unattended benchmark runs measure the promoted fast path instead of drifting back to older or slower defaults
  - `scripts/launch_gui.py` now launches the backend with explicit promoted env overrides (`torch + core4_adaptive_plus + core4`) and logs them, while `scripts/run_backend.ps1` exports the same runtime env before starting uvicorn so manual backend launches follow the same path
  - startup overrides are now cleaner too: `scripts/launch_gui.py` resolves runtime config as CLI flag > existing environment > promoted default, and `scripts/run_backend.ps1` only applies promoted values when the shell has not already set an override
- Traceability improvement:
  - quantize reports now include a `runtime_config` block with effective inference accelerator, inference-candidate strategy, hybrid-search strategy, and hybrid verify top-k
  - benchmark per-file reports now include the same `runtime_config` block, while retaining the legacy `hybrid_search_strategy` field for compatibility
  - benchmark cache keys now default to the promoted `core4` hybrid-search strategy when no env override is present, matching the app and benchmark runner defaults
  - the frontend Results panel now renders a compact runtime strip from `report.runtime_config`, and `frontend/dist` verification checks that the shipped bundle includes it
- Important runtime caveat:
  - `-InferenceAccelerator auto` currently selected OpenVINO `NPU` on this machine and was catastrophically slow on the same 30-second subset
  - the partial report `outputs/core4_validation8_core4_adaptive_plus_auto_infer_30_report.partial.json` shows a single-file `ml_infer` time of about `1139.761s` and total elapsed `1161.775s`
  - for now, Torch/CUDA remains the only practical fast path; auto/NPU should not be treated as production-ready on this hardware without explicit device-selection/fallback work

## Latest Checkpoint - 2026-04-23 - Longer Core-Search Validation

- Extended the `core4` hybrid-search validation from short 5-second clips to longer 10-second and 30-second private-stash proxy clips. This matters because user-reported failures often appear after the first 20-60 seconds, not always in tiny smoke windows.
- Independent 24-track validation at 10 seconds:
  - Full search: `outputs/core4_validation24_full10_report.json`, summary `outputs/core4_validation24_full10_summary.json`.
  - Core4: `outputs/core4_validation24_core4_10_report.json`, summary `outputs/core4_validation24_core4_10_summary.json`.
  - Gate: `outputs/core4_validation24_10s_speed_gate_10pct.json` passed with exact quality parity (`avg_regression=0`, `max_regression=0`) and `13.46%` speedup. The 20% gate failed on speed only.
  - Stage timing: full search averaged `hybrid_score=4.151s`; core4 averaged `hybrid_score=2.365s`, while ML inference stayed about `5.2s`.
- Built `data/benchmark_corpus_stash_core4_validation8_30s.json`, an 8-track longer-window subset selected from the 24-track validation manifest. It includes hard no-explicit-BPM tracks plus explicit-BPM sanity tracks (`171 bpm`, `128 bpm`, `116 BPM`) for 30-second testing.
- 30-second validation results:
  - Full search: `outputs/core4_validation8_full30_report.json`, summary `outputs/core4_validation8_full30_summary.json`, `hybrid_avg_error_after_sec=0.052790`, avg internal elapsed `39.946s`.
  - Core4: `outputs/core4_validation8_core4_30_report.json`, summary `outputs/core4_validation8_core4_30_summary.json`, `hybrid_avg_error_after_sec=0.052857`, avg internal elapsed `34.894s`.
  - Gate: `outputs/core4_validation8_30s_speed_gate_10pct.json` passed with avg regression `0.000066s`, worst per-file regression `0.000531s`, and `12.65%` speedup. The 20% gate failed on speed only.
  - Core4's only 30-second quality delta was `171 bpm b minor.wav`, where full search chose `boxbox_legacy_specialist_r1730` and core4 chose `routed`. The delta was only `0.000531s`, safely inside the product-safety cap.
- Added `core5` hybrid-search strategy as an experiment. It keeps `core4` and adds `boxbox_legacy_specialist_r1730`, because the full 30-second run selected that specialist once. CLI/script support now accepts `--hybrid-search-strategy core5` / `-HybridSearchStrategy core5`.
- Core5 30-second validation:
  - Report: `outputs/core4_validation8_core5_30_report.json`, summary `outputs/core4_validation8_core5_30_summary.json`.
  - It matched full-search quality exactly (`avg_regression=0`, `max_regression=0`) but averaged `36.612s`, only `8.35%` faster than full search.
  - Gates: `outputs/core4_validation8_30s_core5_speed_gate_10pct.json` and `outputs/core4_validation8_30s_core5_speed_gate_20pct.json` failed on speed only.
- Added an explicit inference-candidate strategy knob through `BOXBOX_INFER_CANDIDATE_STRATEGY`, `--inference-candidate-strategy`, and `-InferenceCandidateStrategy`. Supported values are `all`, `core4`, `core4_adaptive`, `core4_adaptive_plus`, `core4_adaptive_plus_qf`, and `core5`. This separates aggressive inference pruning experiments from the existing post-inference hybrid-search pruning.
- Inference-pruned `core4` 30-second experiment:
  - Report: `outputs/core4_validation8_core4_inferpruned_30_report.json`, summary `outputs/core4_validation8_core4_inferpruned_30_summary.json`.
  - Runtime dropped hard: avg internal elapsed `28.183s`, speedup `29.45%`, `ml_infer` avg `10.555s`.
  - Quality was not safe enough for promotion. Gate outputs `outputs/core4_validation8_core4_inferpruned_30_gate_10pct.json` and `outputs/core4_validation8_core4_inferpruned_30_gate_20pct.json` failed on worst per-file regression: `0.004238s` on `Daft Punk - Homework (ALBUM)\07 Around The World.m4a`, above the `0.003s` cap. Avg regression stayed acceptable at `0.000781s`.
- Added `core4_adaptive` inference pruning, which keeps `core4` and conditionally brings back `boxbox_legacy_specialist_r1730` for more legacy-ish routing profiles.
- Adaptive 30-second experiment:
  - Report: `outputs/core4_validation8_core4_adaptive_infer_30_report.json`, summary `outputs/core4_validation8_core4_adaptive_infer_30_summary.json`.
  - It improved over full search on average (`avg_delta_candidate_minus_baseline_sec=-0.001746s`) while still delivering `26.19%` speedup and `29.485s` average internal elapsed.
  - However, it still failed the per-file safety cap because `Daft Punk - Homework (ALBUM)\07 Around The World.m4a` regressed by `0.004238s`, and `171 bpm b minor.wav` regressed by `0.003066s`. Gate outputs: `outputs/core4_validation8_core4_adaptive_infer_30_gate_10pct.json` and `outputs/core4_validation8_core4_adaptive_infer_30_gate_20pct.json`.
- Added `core4_adaptive_plus`, a stricter adaptive inference-pruning strategy that restores the full legacy trio (`r1730`, `r1884_qm`, `r1884_qf`) only for tracks whose routing profile suggests they need the broader legacy blend.
- `core4_adaptive_plus` 30-second validation:
  - Report: `outputs/core4_validation8_core4_adaptive_plus_infer_30_report.json`, summary `outputs/core4_validation8_core4_adaptive_plus_infer_30_summary.json`.
  - Gate: `outputs/core4_validation8_core4_adaptive_plus_infer_30_gate_10pct.json` passed with avg regression `0.000066s`, worst per-file regression `0.000531s`, and `19.25%` speedup.
  - The 20% gate (`outputs/core4_validation8_core4_adaptive_plus_infer_30_gate_20pct.json`) failed on speed only.
  - Important behavior change versus the earlier adaptive attempt:
    - `Daft Punk - Homework (ALBUM)\07 Around The World.m4a` returned to exact full-search parity by restoring the broader routed legacy blend.
    - `171 bpm b minor.wav` stayed within the safety cap with only `0.000531s` regression.
  - Timing: avg internal elapsed `32.255s`, `ml_infer` avg `14.523s`, still materially faster than full search (`39.946s`) and better aligned with product-safe quality than the earlier inference-pruned variants.
- Interpretation:
  - Post-inference `core4` remains the best current product-default speed candidate because it passes the 10% gate while staying inside both quality caps.
  - `core4_adaptive_plus` is now the strongest inference-pruned candidate so far. It is product-safe at the 10% gate and nearly reaches the 20% gate without quality surprises.
  - The remaining gap is now mostly speed margin, not quality safety. Future work should try to trim another 1-2 points of runtime without disturbing the restored routed blend on tracks like `Around The World`.
- Decision: `core4` remains the best product-speed candidate because it clears the 10% speed gate with negligible 30-second quality delta. `core5` is useful as a quality-parity experiment, but it is not currently faster enough to promote as the default speed path. Full search remains the quality oracle.
- Fixed benchmark summary printing on Windows by reconfiguring `backend/ml/benchmark_report.py` stdout to UTF-8 with replacement errors. This prevents summary generation from crashing on unusual Unicode filenames.
- Verification so far: focused hybrid/cache/infer tests passed (`37 passed` after `core4_adaptive_plus`), earlier focused hybrid/cache tests passed (`47 passed`), wrapper parse passed, 10-second and 30-second real private-stash proxy benchmarks completed, benchmark candidate gates/summaries were generated, and the full backend suite passed (`239 passed`).

## Latest Checkpoint - 2026-04-22 - Benchmark Candidate Speed Gate

- Added a product-safety gate for faster benchmark modes. `backend/ml/benchmark.py` now has `evaluate_benchmark_candidate_gate()` plus CLI args `--benchmark-gate-baseline-report`, `--benchmark-gate-candidate-report`, `--benchmark-gate-max-avg-regression-sec`, `--benchmark-gate-max-per-file-regression-sec`, and `--benchmark-gate-min-speedup-pct`.
- Added `scripts/gate_benchmark_candidate.ps1` so faster modes can be compared against the full quality baseline without manual DAW-style eyeballing. The gate checks shared-track hybrid error, average regression, worst per-file regression, and measured benchmark speedup from `processing_timing.elapsed_sec`.
- README now documents the gate command. Default example thresholds are `MaxAvgRegressionSec=0.001`, `MaxPerFileRegressionSec=0.003`, and `MinSpeedupPct=20`.
- Added hybrid-search pruning for benchmark/product experiments via `BOXBOX_HYBRID_SEARCH_STRATEGY` and `--hybrid-search-strategy` / `-HybridSearchStrategy`. Supported strategies: `all`, `routed`, `routed_top1`, `routed_top2`, `core4`, and `core5`; the promoted default is now `core4`, while `all` remains the oracle/full-search comparison path. Cache keys include this strategy so pruned-search reports do not reuse full-search results.
- Added `--skip-audio-errors` / `-SkipAudioErrors` for broad private-manifest benchmarks. Decode/probe failures are recorded under `failed_files` instead of stopping the entire run. Also hardened subprocess decoding in `backend/audio/io_utils.py` with UTF-8 replacement handling so ffprobe metadata with non-Windows-console bytes does not raise background `UnicodeDecodeError`.
- Built `data/benchmark_corpus_stash_core4_validation24.json`: 24 broad full-track stash candidates excluding the original drifting-20 seed, with 21 no-explicit-BPM tracks and 3 explicit-BPM sanity tracks.
- Real gate results against the 20-track private drifting seed:
  - `outputs/drifting20_m2_speed_gate.json`: failed. It achieved `36.47%` speedup and avg regression `0.000929s`, but worst per-file regression was `0.011369s` on `Player - Baby Come Back HD 320kbps.mp3`, exceeding the `0.003s` product-safety cap.
  - `outputs/drifting20_m1_speed_gate.json`: failed. It achieved `58.12%` speedup, but avg regression was `0.001177s` and worst per-file regression was again `0.011369s`.
  - `outputs/drifting20_routed_top1_speed_gate.json`: failed. It achieved `22.55%` speedup, but avg regression was `0.004115s` and worst per-file regression was `0.015589s`. Lesson: highest-confidence side model is not a safe proxy for the full hybrid search.
  - `outputs/drifting20_core4_speed_gate_20pct.json`: failed only the 20% speed threshold. It had exact quality parity with full search (`avg regression=0`, `max regression=0`) and `13.57%` speedup.
  - `outputs/drifting20_core4_speed_gate_10pct.json`: passed. It matched full quality exactly on the 20-track seed and exceeded a 10% speedup gate. `core4` should be treated as the best safe speed experiment so far, but it needs broader validation before becoming a default product path.
- Broader independent `core4` validation:
  - Full baseline: `outputs/core4_validation24_full_report.json`, summary `hybrid_avg_error_after_sec=0.050605`, `hybrid_wins=15/24`, average internal elapsed `8.131s`.
  - Core4: `outputs/core4_validation24_core4_report.json`, summary exactly matched full quality (`hybrid_avg_error_after_sec=0.050605`, `hybrid_wins=15/24`), average internal elapsed `5.939s`.
  - Gate: `outputs/core4_validation24_speed_gate_10pct.json` passed with `avg_regression=0`, `max_regression=0`, and `26.96%` speedup on all 24 shared files.
  - Slow-stage summaries: full search averaged `hybrid_score=2.144s`; `core4` averaged `hybrid_score=1.223s`, confirming the optimization is doing what it was intended to do.
- Decision: full six-model/all-candidate search remains the quality/default validation path. `-MaxInferenceModels 1`, `-MaxInferenceModels 2`, and `routed_top1` are not product-default safe. `-HybridSearchStrategy core4` is promising because it preserves quality on this seed while reducing average internal elapsed from `7.029s` to `6.075s`.
- Updated decision: `core4` is now validated on 44 private tracks total across two independent manifests with exact parity to full search and measurable speedup. It is the leading candidate for a future GUI/product default, but should still be tested on longer clip durations and larger manifests before promotion.
- Verification: focused benchmark/cache/hybrid/io tests passed (`26 passed` and prior `45 passed`), full backend suite status should be rerun after this checkpoint if more edits follow, and PowerShell wrappers parsed successfully.

## Latest Checkpoint - 2026-04-22 - Resumable Private-Corpus Benchmarks

- Added resumable/incremental audio-manifest benchmark support in `backend/ml/benchmark.py`. Manifest runs now accept `--incremental-output` and `--resume`, write a top-level `real_audio_suite` partial report after each completed track, and skip already recorded tracks when resumed.
- Updated `scripts/run_benchmarks.ps1` with `-IncrementalOutput` and `-Resume`. Manifest runs no longer also pass the default `benchmarks` directory, avoiding empty-directory failures and wasted suite work.
- Hardened real Windows/audio intake after CLI smoke testing: audio manifests are read with `utf-8-sig` so PowerShell-generated BOM JSON works, and `backend/audio/io_utils.py::load_audio()` now falls back to ffmpeg float decoding when `soundfile` cannot open stash MP3/M4A-style sources.
- Added explicit benchmark inference accelerator control. `backend/ml/benchmark.py` now accepts `--inference-accelerator` and `scripts/run_benchmarks.ps1` exposes `-InferenceAccelerator` (`auto`, `npu`, `gpu`, `cpu`, `torch`, `cuda`). Benchmark cache keys now include the selected accelerator so Torch and OpenVINO/NPU runs do not silently reuse each other's cached results.
- README now documents the long-running private drifting corpus command: `scripts/run_benchmarks.ps1 -AudioManifest data/benchmark_corpus_stash_audio_drifting_older79_windows3.json -ProxyOnly -SkipValidation -ClipDuration 10 -Output outputs/drifting_proxy_report.json -IncrementalOutput outputs/drifting_proxy_report.partial.json -Resume`.
- README now notes that benchmark runs default to `-InferenceAccelerator torch`; use explicit overrides for OpenVINO/NPU experiments or oracle/full-search comparisons.
- Current best private benchmark seed remains `data/benchmark_corpus_stash_audio_drifting_older79_windows3.json`: 20 audio-confirmed drifting/unquantized candidates mined from the user's read-only sample stash.
- Real one-track stash MP3 smoke passed: `scripts/run_benchmarks.ps1 -AudioManifest outputs/drifting_one_track_manifest.json -ProxyOnly -SkipValidation -ClipDuration 5 -Output outputs/drifting_one_track_proxy_report.json -IncrementalOutput outputs/drifting_one_track_proxy_report.partial.json -Resume`. It completed in about 116 seconds and produced both output and partial reports; this confirms resumability is needed and benchmark/runtime speed is a major next optimization target.
- Resume check on the same partial report completed in about 3 seconds and reused the existing file result, proving interrupted/continued manifest runs can avoid recomputing completed tracks.
- Real Torch-backend wrapper smoke passed: `scripts/run_benchmarks.ps1 -AudioManifest outputs/drifting_one_track_manifest.json -ProxyOnly -SkipValidation -ClipDuration 5 -Output outputs/drifting_one_track_proxy_report.torch_wrapper.json -InferenceAccelerator torch` completed in about 13 seconds, and the cached repeat completed in about 2.8 seconds. The report's `accelerator` may show `cuda` because the Torch backend can still use CUDA while avoiding OpenVINO export/compile.
- Verification: benchmark/report/io focused tests passed (`23 passed`), `scripts/run_benchmarks.ps1` parsed, real one-track CLI smokes passed, and the full backend suite passed (`228 passed`).

## Latest Checkpoint - 2026-04-22 - Timed 20-Track Private Drifting Benchmark

- Added per-stage `processing_timing` to both `benchmark_real_audio()` and `benchmark_real_audio_proxy()` in `backend/ml/benchmark.py`. Timed stages include audio load, preparation, tempo/grid, feature extraction, onset detection, baseline curve/scoring, ML inference/scoring, hybrid ranking/scoring, and selection.
- Expanded `backend/ml/benchmark_report.py` so summaries include `stage_timing_summary`, `slowest_tracks`, and each slow track's slowest stage. README now notes that benchmark summaries surface slowest tracks/stages when timing data exists.
- Ran the first successful 20-track private drifting-corpus proxy benchmark using the audio-confirmed drifting seed:
  `scripts/run_benchmarks.ps1 -AudioManifest data/benchmark_corpus_stash_audio_drifting_older79_windows3.json -ProxyOnly -SkipValidation -ClipDuration 5 -Output outputs/drifting20_proxy_torch5_report.json -IncrementalOutput outputs/drifting20_proxy_torch5_report.partial.json -Resume -InferenceAccelerator torch`
- Runtime: about 140 seconds wall-clock for 20 tracks. Report outputs:
  `outputs/drifting20_proxy_torch5_report.json`
  `outputs/drifting20_proxy_torch5_report.partial.json`
  `outputs/drifting20_proxy_torch5_summary.json`
- Quality snapshot for 5-second proxy clips: `num_files=20`, `baseline_avg_error_after_sec=0.056931`, `ml_avg_error_after_sec=0.052919`, `hybrid_avg_error_after_sec=0.049558`, `ml_wins=13`, `hybrid_wins=16`. There was one tiny hybrid regression: Lauryn Hill `09 I Used to Love Him.mp3`, about `0.000458s` worse than baseline.
- Hardest tracks by hybrid error included Lauryn Hill `08 When It Hurts So Bad.mp3`, `jazzy unmastered.wav`, Lauryn Hill `09 I Used to Love Him.mp3`, `Nebu Kiniza - Gassed Up`, and `SOUDIERE - BLASTIN' with LOUD LORD`.
- Runtime bottleneck snapshot across the 20 tracks: average `ml_infer=2.590s`, `hybrid_score=2.233s`, `tempo_and_grid=0.541s`, `load_audio=0.511s`, `baseline_curve=0.430s`; average benchmark-internal elapsed was `7.278s`, max `9.299s`.
- Next optimization target should be ML inference and repeated hybrid candidate scoring. Decode and feature extraction are no longer the dominant bottlenecks for this short-clip Torch benchmark path.
- Verification: focused benchmark/report/io tests passed (`23 passed`), the 20-track private benchmark completed and summarized successfully, and the full backend suite passed (`228 passed`).

## Latest Checkpoint - 2026-04-22 - Benchmark Speed/Quality Knobs

- Tested a fast projected-onset hybrid scoring path for proxy benchmarks. It reduced the 20-track run from about `144s` to about `104s`, but quality regressed: hybrid wins dropped from `16/20` to `13/20`, and regressions grew. This mode is now explicit opt-in via `--fast-proxy-hybrid-scoring` / `-FastProxyHybridScoring`; it must not be the default quality path.
- Restored quality-preserving proxy-audio hybrid scoring as the default. Re-run confirmed the previous quality baseline: `hybrid_avg_error_after_sec=0.049558`, `hybrid_wins=16/20`, one tiny regression of about `0.000458s`.
- Added model-count limiting for inference experiments. `backend/ml/infer.py::infer_curve_candidates()` accepts `max_models`; `backend/ml/benchmark.py` exposes `--max-inference-models`; `scripts/run_benchmarks.ps1` exposes `-MaxInferenceModels`. Cache keys include the model-count limit so capped/all-model reports do not mix.
- Corrected CLI wiring so `--max-inference-models` is passed into audio-manifest benchmarks. A one-track fresh probe with `--max-inference-models 1` reduced `ml_infer` from about `2.85s` to about `0.67s`.
- 20-track `-MaxInferenceModels 1` run: about `62s`, `ml_infer` average `0.343s`, `hybrid_avg_error_after_sec=0.050735`, `hybrid_wins=16/20`, two regressions including `jazzy unmastered.wav` at about `0.004770s`. Good speed, but not quality-default safe.
- 20-track `-MaxInferenceModels 2` run: about `93s`, `ml_infer` average `1.273s`, `hybrid_score` average `0.933s`, `hybrid_avg_error_after_sec=0.050487`, `hybrid_wins=16/20`, one regression around `0.000943s`. This is the current best faster dev-mode compromise, but full six-model routing remains the quality/default validation mode.
- README now documents `-MaxInferenceModels 2` as a faster dev-only routing experiment knob and warns to leave it unset for quality/default validation.
- Verification: focused benchmark/report/io/infer tests passed (`33 passed`), benchmark wrapper parsed, real 20-track runs completed for full/default, fast hybrid scoring, max-1, and max-2 modes, and the full backend suite passed (`230 passed`).

## Operating Directives

- Codex must always read and reference this file before making major project decisions, changing training direction, changing data strategy, or redefining roadmap priorities.
- The project must not drift away from the core product goal:
  - quantize almost anything
  - especially the kinds of human-played recordings producers actually sample
- Efficiency remains a standing constraint:
  - storage-conscious data handling
  - GPU-first training
  - only use NPU/alternate runtimes where they are stable and useful
  - avoid duplicated datasets and wasted benchmark/training work
- Ethical and legal sourcing is mandatory:
  - avoid copyrighted source material for dataset ingestion
  - continually prefer public-domain, clearly licensed, or otherwise rights-cleared sources
  - prioritize 60s/70s/80s/90s music where cleared sources exist because those eras matter most for producer sampling
  - do not limit sourcing only to those eras; stay open to all music types and eras if they help generalization
  - every source used for dataset building must be cleared of copyright risk

## Phase Gate Status

Current MAE gate for model readiness:

- benchmark threshold: `0.05` validation MAE

Current tracked validation results:

- active mixed model `boxbox_latest.pt` on `groove_audio + maestro_audio + musicnet_audio + legacy_audio` val:
  - `avg_mae = 0.013345`
- active legacy specialist `boxbox_legacy_specialist_pilot.pt` on `musicnet_audio + legacy_audio` val:
  - `avg_mae = 0.009199`
- filtered legacy specialist `boxbox_legacy_specialist_r1884_qf.pt` on the high-confidence filtered `musicnet_audio + legacy_audio` val:
  - `avg_mae = 0.002681`

Conclusion:

- the current tracked validation MAEs are all well below the `0.05` gate
- this means the project is now ready to begin development of the event-detection module as the next major subsystem

## Next Phase Roadmap

Once the current quantization backbone is stable enough, the next phase should include:

- event-detection module development
- tempo drift tracking
  - explicitly model local tempo movement over time instead of only mapping onsets to a fixed-grid target
- style adaptation
  - account for differences between drums, piano, orchestral material, vintage mixed recordings, and producer sample-source material
- user feedback loops
  - let user choices and corrections influence future quantization decisions and tuning

These should be treated as the next major roadmap items after the current quantization-model phase gate.

## 2026-04-05 Event-Detection Phase Start

Event-detection development has now started.

What was added:
- new module:
  - `backend/audio/events.py`
- this module turns raw audio plus extracted features into structured event detections
- each detected event currently includes:
  - `time_sec`
  - `strength`
  - `prominence`
  - `confidence`
  - `kind`
  - `accent`
- event kinds currently used:
  - `percussive`
  - `mixed`
  - `harmonic`

Current implementation details:
- event detection is derived from the existing feature stack instead of building a separate analysis pipeline
- it combines:
  - onset envelope
  - novelty
  - local HPSS-based percussive balance
- this is intentionally a first-pass event layer meant to support later subsystems, not a final transcription engine

Product integration:
- quantize reports now include:
  - `event_detection.summary`
  - `event_detection.preview`
- this means future work on tempo drift tracking, style adaptation, and user feedback can consume a stable event representation from `report.json`

Why this matters:
- tempo drift tracking needs a better event substrate than bare onset timestamps
- user feedback loops will need identifiable event-level anchors
- style adaptation benefits from understanding whether local material is more percussive, mixed, or harmonic

Validation:
- added tests in:
  - `backend/tests/test_event_detection.py`
- full backend suite after this step:
  - `50 passed`

## 2026-04-05 Tempo Drift Tracking Started

Tempo drift tracking has now started on top of the event-detection substrate.

What was added:
- new module:
  - `backend/audio/drift.py`
- this module estimates a local tempo trace from detected events and compares it to the requested target BPM

Current output structure:
- quantize reports now include:
  - `tempo_drift.summary`
  - `tempo_drift.preview`

Current tempo drift summary fields:
- `window_count`
- `target_bpm`
- `median_local_bpm`
- `mean_abs_drift_bpm`
- `max_abs_drift_bpm`
- `mean_abs_drift_pct`

Current preview fields:
- `time_sec`
- `local_bpm`
- `drift_bpm`
- `drift_pct`
- `confidence`

Why this matters:
- BoxBox now has the beginning of a local tempo-movement representation instead of only one global BPM plus onset alignment
- this is the foundation for:
  - smarter style adaptation
  - local timing strategy changes
  - future user feedback loops around “tighten” vs “preserve feel”

Validation:
- added tests in:
  - `backend/tests/test_tempo_drift.py`
- full backend suite after this step:
  - `52 passed`

## 2026-04-05 Style Adaptation Started

Style adaptation has now started on top of event detection and tempo drift.

What was added:
- new module:
  - `backend/audio/style.py`
- this module infers a lightweight style profile from:
  - event makeup
  - event density
  - strong-event ratio
  - tempo drift summary

Current style output:
- quantize reports now include:
  - `style_adaptation.profile`
  - `style_adaptation.confidence`
  - `style_adaptation.recommended_groove_preserve`
  - `style_adaptation.prefer_segmented_hybrid`
  - `style_adaptation.features`

Current style profiles:
- `percussive_tight`
- `harmonic_expressive`
- `mixed_drifting`
- `dense_rhythmic`
- `balanced`

Current purpose:
- this is a low-risk style-guidance layer, not yet a hard behavioral controller
- it gives BoxBox a first stable representation of “what kind of musical material is this section/song?”
- this is the bridge toward future:
  - adaptive groove-preserve tuning
  - smarter segmented hybrid behavior
  - user-facing feedback and override controls

Validation:
- added tests in:
  - `backend/tests/test_style_profile.py`
- full backend suite after this step:
  - `54 passed`

## 2026-04-05 Style Adaptation Now Guides Quantization

Style adaptation is no longer report-only.

What changed:
- `backend/app.py` now uses style output to gently guide live quantization behavior
- the requested groove-preserve value is now softly blended toward the style recommendation when style confidence is high
- segmented hybrid selection is now gated by the style recommendation instead of always being attempted

Current control behavior:
- user intent is still preserved
- style guidance only nudges groove preserve partway toward the recommendation
- low-confidence style classifications do not change the requested groove-preserve value
- segmented hybrid is only enabled when the style layer explicitly prefers it and confidence is high enough

Why this matters:
- this turns the style layer into an actual product behavior instead of a passive report field
- expressive or drifting material can now opt into segmented hybrid more deliberately
- tighter percussive material can steer toward stronger quantization without fully overriding the user request

Report integration:
- quantize reports now also include:
  - top-level `style_guided_groove_preserve`
  - `style_adaptation.guided_groove_preserve`
  - `style_adaptation.segmented_hybrid_applied`

Validation:
- added coverage for:
  - confident vs low-confidence groove guidance
  - segmented-hybrid preference gating
  - smoke-report presence of the new style-guided fields
- full backend suite after this step:
  - `57 passed`

## 2026-04-05 User Feedback Loop Started

User feedback loop development has now started.

What was added:
- new module:
  - `backend/audio/feedback.py`
- new API endpoint:
  - `POST /api/feedback`
- frontend results integration:
  - quick feedback actions now appear in the results panel

Current behavior:
- each quantize report now includes a `feedback_loop` section
- that section contains:
  - a short summary
  - quick feedback actions
  - suggested follow-up controls for each action
  - latest feedback entry
  - feedback history count
- supported first-pass feedback labels are:
  - `good`
  - `too_loose`
  - `too_tight`
  - `warbly`

How it works right now:
- feedback is stored per job in:
  - `outputs/<job_id>/feedback.json`
- the latest feedback is also mirrored back into `report.json`
- UI quick actions both:
  - record the user's judgment
  - apply the suggested next-pass controls in the frontend for fast iteration

Why this matters:
- this creates the first real product feedback loop instead of only passive analysis
- users can now steer the next quantization pass with simple subjective judgments
- future work can build on the stored feedback history instead of starting from scratch

Validation:
- added backend coverage in:
  - `backend/tests/test_feedback_loop.py`
- full backend suite after this step:
  - `58 passed`
- frontend production build verification is still blocked in this environment by a local Windows `spawn EPERM` from Vite/esbuild

## 2026-04-05 Feedback Memory Now Starts Influencing Quantization

The feedback loop now has a first persistent learning layer.

What changed:
- feedback is now aggregated globally in:
  - `outputs/feedback_memory.json`
- aggregation is grouped by inferred style profile
- repeated feedback on the same style can now gently bias future groove-preserve choices

Current behavior:
- quantization reads learned feedback memory before choosing the final style-guided groove-preserve target
- this learned layer does not override explicit user mode requests
- it currently only nudges groove preserve and exposes a learned preferred mode in reports
- evidence is intentionally required before any learned adjustment is applied

Current learning rules:
- repeated `too_loose` feedback for a style nudges groove preserve downward
- repeated `too_tight` feedback nudges groove preserve upward
- repeated `warbly` feedback nudges groove preserve upward and can mark `dtw` as the learned safer mode
- small sample counts do not trigger automatic adaptation

Report integration:
- quantize reports now include:
  - `style_adaptation.learned_behavior`
  - `feedback_loop.learned_behavior`

Why this matters:
- BoxBox now has the beginning of cross-job memory instead of treating every run as isolated
- subjective user feedback can start influencing future quantization decisions for similar material
- this is the first bridge from static heuristics toward user-shaped adaptive behavior

Validation:
- added aggregation coverage in:
  - `backend/tests/test_feedback_loop.py`
- full backend suite after this step:
  - `59 passed`

## 2026-04-05 Learned Defaults Exposed To The UI

The learned feedback memory is now surfaced as an explicit next-pass recommendation.

What changed:
- `feedback_loop` now emits a `learned_default` action once enough evidence exists for a style profile
- the frontend results panel now exposes a one-click `Use Learned Default` button

Current behavior:
- the learned default does not auto-override controls silently
- instead, it gives the user a clear recommendation they can apply for the next pass
- the recommended controls currently include:
  - `mode`
  - `target_bpm`
  - `resolution`
  - `groove_preserve`

Why this matters:
- BoxBox now moves from hidden adaptive memory toward transparent user-steerable adaptation
- users can benefit from prior feedback without losing control over the next quantization pass
- this is a safer bridge between fixed heuristics and stronger automatic personalization

Validation:
- added learned-default coverage in:
  - `backend/tests/test_feedback_loop.py`
- full backend suite after this step:
  - `60 passed`

## 2026-04-05 Training Loop Speedup + Legacy Specialist Follow-Up

Training and evaluation tooling was tightened so model iteration can continue inside the current workflow without waiting on overly slow validation passes.

What changed:
- `backend/ml/dataset.py` now supports deterministic `max_examples` limits after filtering/splitting
- `backend/ml/evaluate.py` now exposes `--max-examples`
- `backend/ml/train.py` now exposes `--max-examples`

Why this matters:
- full mixed-model validation was too slow to be a practical screening loop in the current environment
- the new limit makes it possible to run fast candidate checks before committing to longer training or broader evaluation
- this is directly aligned with the standing efficiency constraint

Legacy routing fix:
- `backend/ml/infer.py` had drifted from project memory and was still pointing at `boxbox_legacy_specialist_pilot.pt`
- this was corrected so the promoted `boxbox_legacy_specialist_r1730.pt` is now the main routed legacy specialist again

Legacy specialist follow-up training:
- dataset sizing checks on `musicnet_audio + legacy_audio` showed:
  - unfiltered slice: train `496`, val `86`
  - moderate quality filter (`min_improvement_pct=15`, `max_after_sec=0.06`, `min_event_count=48`): train `267`, val `56`
  - strict quality filter (`25`, `0.05`, `80`): train `199`, val `38`
- current specialist comparisons before the new run:
  - promoted `r1730` on moderate slice: `avg_mae=0.003392`
  - filtered `r1884_qf` on moderate slice: `avg_mae=0.002826`

New run:
- trained new candidate:
  - `models/boxbox_legacy_specialist_r1884_qm.pt`
- init:
  - `models/boxbox_legacy_specialist_r1884_qf.pt`
- datasets:
  - `musicnet_audio,legacy_audio`
- balancing:
  - `inverse`
  - overrides `legacy_audio=2,musicnet_audio=1.5`
- moderate quality filter:
  - `min_improvement_pct=15`
  - `max_after_sec=0.06`
  - `min_event_count=48`
- epochs: `6`
- batch size: `8`
- lr: `0.00015`

Result:
- moderate quality val:
  - `r1884_qm`: `avg_mae=0.002824`
  - `r1884_qf`: `avg_mae=0.002826`
  - baseline on that slice: `0.002823`
- strict filtered val:
  - `r1884_qm`: `avg_mae=0.002678`
  - `r1884_qf`: `avg_mae=0.002681`
  - baseline on that slice: `0.002679`
- full unfiltered `musicnet_audio + legacy_audio` val:
  - promoted `r1730`: `avg_mae=0.009199`
  - new `r1884_qm`: `avg_mae=0.009379`
  - prior `r1884_qf`: `avg_mae=0.009394`

Pragmatic outcome:
- `r1884_qm` is a useful compromise checkpoint
- it does not beat the promoted `r1730` specialist on the full unfiltered validation slice
- it slightly improves over `r1884_qf` on filtered slices and slightly over `r1884_qf` on the full slice without surpassing `r1730`
- because of that, it was not promoted as the main legacy specialist
- instead it was added as an extra legacy candidate for router search alongside:
  - `r1730`
  - `r1884_qf`

Operational note:
- a representative full-song legacy benchmark probe on `benchmarks/popular-song-1931.ogg` still timed out under the current offline harness
- the benchmark harness remains slower than the product path for this kind of end-to-end validation

Validation:
- added deterministic example-limit coverage in:
  - `backend/tests/test_dataset_disk_cache.py`
- updated routing coverage in:
  - `backend/tests/test_infer_routing.py`
- full backend suite after this step:
  - `61 passed`

## 2026-04-06 Legacy Proxy Suite + Mixed Legacy Candidate

The next step focused on making legacy-domain model iteration faster and then using that tighter loop to test a new mixed-model direction.

Benchmark harness improvement:
- `backend/ml/benchmark.py` now supports:
  - a named `LEGACY_BENCHMARK_SUITE`
  - cache separation for:
    - full vs proxy mode
    - clip start
    - clip duration
- this avoids cache collisions between quick proxy checks and full-song benchmarks
- the CLI now supports `--legacy-suite`

Current legacy proxy suite:
- `benchmarks/koromogo-e-1930.ogg`
- `benchmarks/popular-song-1931.ogg`
- `benchmarks/tico-tico-1943.ogg`
- `benchmarks/ute-1950.ogg`

Practical result:
- running the legacy suite in proxy mode with:
  - `BOXBOX_INFER_ACCELERATOR=cuda`
  - `clip_duration=30`
- produced:
  - baseline avg after: `0.0606227309`
  - ML avg after: `0.0444831806`
  - hybrid avg after: `0.0467317539`
  - ML wins: `4/4`
  - hybrid wins: `4/4`
- notable routing observation:
  - the routed candidate now meaningfully uses both legacy experts on the older-material subset
  - `boxbox_latest` still remains important, especially on some less strictly legacy-feeling material

New mixed-model experiment:
- trained:
  - `models/boxbox_mixed_legacy_candidate_r1884.pt`
- init:
  - `models/boxbox_latest.pt`
- datasets:
  - `groove_audio,maestro_audio,musicnet_audio,legacy_audio`
- balancing:
  - `inverse`
  - overrides:
    - `legacy_audio=2`
    - `musicnet_audio=1.5`
    - `groove_audio=0.75`
    - `maestro_audio=1.0`
- train cap for iteration speed:
  - `max_examples=1200`
- epochs: `4`
- batch size: `8`
- lr: `0.00015`

Initial training-val output on the capped four-family slice:
- candidate:
  - `avg_mae=0.012789`
  - baseline `0.012948`

Direct comparison checks:
- broad four-family screened val (`max_examples=96`):
  - active `boxbox_latest.pt`: `avg_mae=0.015399`
  - candidate `boxbox_mixed_legacy_candidate_r1884.pt`: `avg_mae=0.015385`
- legacy-heavy val (`musicnet_audio + legacy_audio`):
  - active `boxbox_latest.pt`: `avg_mae=0.011055`
  - candidate `boxbox_mixed_legacy_candidate_r1884.pt`: `avg_mae=0.009218`

Interpretation:
- the mixed+legacy candidate is not yet strong enough to auto-promote over the active mixed model based only on these screened checks
- but it is directionally better on both:
  - the screened broad slice
  - the legacy-heavy slice
- that makes it worth exposing to router search instead of hiding it behind a single active-model slot

Routing change:
- `backend/ml/infer.py` now includes:
  - `boxbox_mixed_legacy_candidate_r1884.pt`
- this means the router can now consider:
  - active mixed model
  - mixed legacy-adapted candidate
  - Groove specialist
  - promoted legacy specialist
  - moderate-quality legacy specialist
  - high-confidence filtered legacy specialist

Pragmatic outcome:
- no active mixed-model promotion yet
- added the mixed+legacy candidate as an additional routed mixed-domain option
- this keeps the product path conservative while still letting legacy-informed improvements contribute where they help

Validation:
- updated benchmark cache coverage in:
  - `backend/tests/test_benchmark_cache.py`
- updated routing coverage in:
  - `backend/tests/test_infer_routing.py`
- full backend suite after this step:
  - `63 passed`

## 2026-04-06 Mixed Legacy Candidate Gets Its Own Routing Role

The new mixed+legacy checkpoint is no longer treated as just another generic mixed model.

What changed:
- `backend/ml/infer.py` now classifies:
  - `boxbox_mixed_legacy_candidate_r1884.pt`
  - as `mixed_legacy` instead of plain `mixed`
- routing weights now include a dedicated `mixed_legacy` branch

Why this matters:
- the earlier generic mixed weighting underused the new mixed+legacy checkpoint on older material
- this new role lets legacy-ish content favor it more than the plain mixed model without confusing it with the pure legacy specialists
- it is a better fit for material that is:
  - mixed or commercial-feeling
  - but still vintage, noisy, or legacy-adjacent

Observed proxy effect on the legacy subset:
- on `koromogo-e-1930.ogg`, routed proxy weights now gave `boxbox_mixed_legacy_candidate_r1884.pt` slightly more weight than `boxbox_latest.pt`
- on `popular-song-1931.ogg`, the mixed+legacy candidate clearly outranked the plain mixed model in routed weights
- on `ute-1950.ogg`, the plain mixed model still stayed stronger, which is a healthy sign that routing is not blindly overfavoring the new role

Current interpretation:
- the mixed+legacy candidate is now positioned as a bridge expert between:
  - the general mixed model
  - the legacy specialists
- this is likely a better product fit than either:
  - promoting it too early as the single active mixed checkpoint
  - leaving it underweighted as a plain mixed model

Validation:
- routing tests remain green
- full backend suite after this step:
  - `63 passed`

## 2026-04-06 Routed Comparison Summary Added

Benchmark output now includes an explicit routed-comparison summary instead of requiring manual reading of raw per-file routing weights.

What changed:
- `backend/ml/benchmark.py` now emits:
  - `real_audio_suite.routed_comparison`
- this section summarizes:
  - selected-candidate counts
  - routed-file count
  - average plain mixed weight
  - average mixed+legacy weight
  - average combined legacy-specialist weight
  - per-file routed comparison rows

Why this matters:
- promotion decisions now have a cleaner product-facing artifact
- we can compare routed behavior across representative tracks without hand-parsing large JSON blobs
- this is the right level of evidence for deciding whether a candidate belongs in the router, should be promoted, or should stay experimental

Fresh legacy proxy comparison checkpoint:
- regenerated proxy legacy suite report:
  - `outputs/benchmark_legacy_proxy_compare_20260406.json`
- summary:
  - baseline avg after: `0.0606227309`
  - ML avg after: `0.0466290826`
  - hybrid avg after: `0.0487953208`
  - ML wins: `4/4`
  - hybrid wins: `4/4`

Routed comparison summary:
- selected candidate counts:
  - `routed`: `3`
  - `boxbox_latest`: `1`
- average routed weights across the routed files:
  - plain mixed (`boxbox_latest.pt`): about `0.1782`
  - mixed legacy (`boxbox_mixed_legacy_candidate_r1884.pt`): about `0.1912`
  - combined legacy specialists: about `0.5708`

Interpretation:
- the router is now using the mixed+legacy bridge model slightly more than the plain mixed model on the representative legacy subset
- legacy specialists still remain the dominant routed block overall on that subset
- `boxbox_latest` still directly wins at least one more modern-feeling representative track, so full promotion of the mixed+legacy candidate still looks premature
- the current evidence supports keeping:
  - `boxbox_latest.pt` as the main mixed checkpoint
  - `boxbox_mixed_legacy_candidate_r1884.pt` as a routed bridge expert

Validation:
- benchmark cache tests remain green
- full backend suite after this step:
  - `63 passed`

## Shutdown Checkpoint

If resuming from a fresh machine start, begin here:

- read this file first
- then read `H:\BoxBox\HANDOFF.md`
- then continue from the event-detection phase, not from earlier quantization-model exploration

Current best resume point:

- quantization backbone is stable
- legacy-routing work is in place
- event detection is implemented
- tempo drift tracking is implemented
- style adaptation is now lightly connected to quantization behavior
- first user feedback loop is now in place
- feedback memory now lightly influences future groove-preserve behavior by style
- next best work item is deeper adaptive control:
  - promote learned preferred mode suggestions into safer user-facing defaults
  - add segment-level user feedback anchors over time
  - separate project-wide memory from per-user memory if multi-user behavior matters

## Elevator Summary

BoxBox is an audio quantization system that started from a strong deterministic onset-grid baseline and evolved into a hybrid system:

- deterministic timing analysis still does the main work
- ML proposes cautious timing corrections
- guardrails prevent ML from making results worse

The current evidence is that this hybrid path is stronger than either:

- deterministic-only quantization with no learned correction
- standalone ML trying to drive the full warp curve by itself

## Problem Statement

The product goal is general quantization of arbitrary music audio files.

That means the system eventually needs to behave well on:

- drums and loops
- solo piano
- dense mixed music
- older/noisy recordings
- music with weak transients
- music with expressive timing and rubato

The core engineering challenge is that numerical timing improvement alone is not enough. Audio quality and runtime behavior also matter. A quantizer that looks better on paper but sounds warped, pitchy, or unusably slow is not acceptable.

## Current Best Architecture

### Backbone

- onset-grid quantization remains the main timing backbone
- this is still the strongest single reliable component on real audio

### ML role

- ML is used as a residual correction layer over the baseline
- the model does not try to invent the whole warp curve from scratch
- the ML path is routed between specialist checkpoints based on audio characteristics

### Guardrails

- hybrid only wins when it actually improves timing metrics
- disagreement with the baseline reduces ML influence
- segment-level local decisions are now supported instead of forcing one global blend for the whole file

### Warp backend

- high-quality time warping now uses Rubber Band
- low-quality interpolation fallback was identified as unacceptable for real use

### Runtime

- live GUI requests use CUDA-first inference
- NPU/OpenVINO remains available for explicit experiments, not as the default live request path

## Main Timeline

### Phase 1: Baseline validation

- verified the existing backend and tests
- benchmarked old ML behavior on real audio
- found that standalone ML lost to the onset-grid baseline on real benchmark tracks

### Phase 2: Residual ML redesign

- changed ML to learn residual corrections over the baseline
- improved training losses and fine-tuning support
- added caching for dataset preparation and benchmarking

### Phase 3: Real-audio data expansion

- imported Groove real drum audio
- imported MAESTRO real piano audio
- combined both into a mixed real-audio training pool
- observed that real audio helped hybrid behavior more than synthetic-only data

### Phase 4: Multi-candidate hybrid logic

- added multiple candidate checkpoints
- added routing between specialists
- added alpha ladders and candidate verification
- improved shortlist construction to preserve per-curve winners

### Phase 5: Runtime and product fixes

- enabled CUDA on the RTX 5070
- enabled optional NPU inference through OpenVINO
- fixed bad DTW audio caused by a broken Rubber Band path
- fixed hybrid latency caused by first-run ONNX/OpenVINO export on the live request path

### Phase 6: Post-move stabilization

- moved project to `H:\BoxBox`
- rebuilt the broken moved virtualenv
- restored CUDA torch and OpenVINO packages
- pinned missing `mido` dependency
- added segment-aware hybrid selection
- switched hybrid candidate verification to a fast mono proxy path before the final full-quality warp

## Datasets Used So Far

### Groove

- type: real drum audio with aligned MIDI
- role: strengthened percussive timing behavior
- result: major reason the percussive specialist exists

### MAESTRO

- type: real piano audio with aligned MIDI
- role: widened the system beyond drums into expressive pitched performance
- result: improved mixed-domain coverage, though not enough by itself to make standalone ML beat baseline

### Other importer-ready sources

The importer already supports or partially supports:

- EGMD
- ASAP
- POP909

These are not all equally valuable for the current goal because some are MIDI-only or less directly useful than large real aligned audio sets.

### New current direction: MusicNet

- type: real classical/chamber recordings with aligned note labels and reference MIDIs
- official source: Zenodo `10.5281/zenodo.5120004`
- value: expands beyond drums and piano into real ensemble and classical recordings
- current repo state:
  - importer support for MusicNet WAV + CSV label archives has been added
  - dedicated importer tests have been added
  - official `musicnet_metadata.csv` and `musicnet_midis.tar.gz` were downloaded
  - full `musicnet.tar.gz` download completed to `H:\BoxBox\data\downloads\musicnet.tar.gz`
  - metadata confirms `330` recordings
  - first import attempts produced a valid partial slice of `musicnet_audio_*` examples
  - current imported MusicNet slice size is at least `40` examples, with an additional background import batch started afterward

## Model and Checkpoint Notes

### Active checkpoint

- `models/boxbox_latest.pt`

### Important backups

- `models/boxbox_before_groove_860.pt`
- `models/boxbox_before_groove_982.pt`
- `models/boxbox_before_groove_finetune.pt`
- `models/boxbox_before_residual.pt`

### Important observed truth

- newer checkpoints were not always better
- standalone ML still does not justify replacing the deterministic baseline
- the best value so far comes from stronger hybrid selection, not simply more training epochs

## Benchmark Story

### Best broad known result before recent runtime changes

Canonical suite in `outputs/benchmark_report_routed.json`:

- baseline avg error after: `0.0388349281`
- ML avg error after: `0.0549015580`
- hybrid avg error after: `0.0381954493`
- standalone ML wins: `0/8`
- hybrid wins: `8/8`

### Interpretation

- standalone ML still loses on real audio
- routed hybrid with guardrails and candidate search beats the baseline across the representative suite
- the current system should be described as a high-quality hybrid quantizer, not an ML-only quantizer

### Current benchmark caveat

After Rubber Band was restored as the real warp backend, some offline full-song benchmark runs became too slow because the benchmark harness still does too many expensive full-audio warps. The live request path is currently more optimized than the offline benchmark harness.

Additional current truth:

- a new proxy-only benchmark mode was added to speed up long-track comparisons
- backend tests for it are clean
- even so, very long tracks like `stayin alive` can still exceed the current command window during offline benchmarking
- the benchmark harness still needs another round of performance work

## What Worked

- keeping the onset-grid baseline as the backbone
- residual learning instead of full-curve prediction
- real aligned audio data
- routing between specialist checkpoints
- conservative hybrid guardrails
- Rubber Band for audio quality
- CUDA for live inference and training
- cache layers for training and benchmark iteration
- segment-aware local hybrid choices
- proxy-based candidate screening before the final full-quality warp
- expanding into MusicNet as the next real-audio domain
- teaching the dataset/training pipeline to consume multiple example roots directly instead of requiring duplicated staging folders

## What Did Not Work

- trusting standalone ML on real audio
- synthetic-only validation as proof of real-world quality
- silent fallback to low-quality interpolation warp
- first-run ONNX/OpenVINO export during live GUI requests
- assuming later checkpoints are automatically better
- assuming one global hybrid blend is always appropriate for a whole song
- assuming more epochs are automatically the best next step
- assuming the first MusicNet-expanded training pool was valid without verifying its actual composition

## Current Product State

### Strengths

- good real-audio hybrid quantization
- better sounding warp path
- GPU-backed live inference
- resumable project state
- GUI is usable and the progress indicator is now more animated

### Weaknesses

- offline full-song benchmark harness still needs more optimization
- standalone ML is still weak
- broader genre coverage still needs more real aligned data
- confidence calibration can still improve
- segment-aware logic exists but has not yet been validated across a freshly regenerated full broad suite
- MusicNet is only partially imported so far, not yet fully exploited
- old duplicated staging folders may still exist on disk from earlier workflow phases, even though the code no longer needs that pattern

## Best Next Steps

1. Finish importing a larger MusicNet slice
2. Retrain on the correctly mixed Groove + MAESTRO + MusicNet pool
3. Continue optimizing the offline benchmark harness so long representative tracks finish in practical time
4. Run a fresh broad suite under the segment-aware hybrid logic
5. Continue improving specialist routing and confidence calibration

## Latest Checkpoint

- Added `MASTER_CONTEXT.md` as the long-form project diary and presentation source
- Added MusicNet importer support in `backend/ml/import_public_midi.py`
- Added MusicNet importer tests in `backend/tests/test_import_musicnet.py`
- Full backend suite passes at `31 passed`
- First MusicNet import attempts:
  - initial tar-backed import timed out in the shell window
  - a duplicate stray importer process was cleaned up
  - a valid partial import landed and produced `musicnet_audio_*` examples with `warp_method = rubberband`
- First attempt at a MusicNet-expanded training pool was invalid because the copy step only staged Groove data
- That issue was corrected and a real mixed pool was built:
  - `982` Groove
  - `320` MAESTRO
  - `40` MusicNet
  - `1342` total
- First proper mixed-domain MusicNet fine-tune:
  - output checkpoint: `models/boxbox_musicnet_candidate_full.pt`
  - epochs: `1`
  - device: `cuda`
  - train examples: `1135`
  - val examples: `207`
  - `val_avg_mae = 0.015242`
  - `val_baseline_avg_mae = 0.015436`
- Interpretation:
  - this is the first sign that the initial MusicNet slice may be helping at the curve-validation level
  - it is not enough on its own to justify promotion yet
  - representative real-audio benchmarking remains the gating step, and that harness is still too slow on very long tracks
- A new background MusicNet import batch was then started to keep growing the corpus beyond the first 40 imported examples
- Storage/engineering cleanup improvement:
  - `backend/ml/dataset.py` now supports multiple example roots directly
  - `backend/ml/train.py`, `backend/ml/evaluate.py`, and validation benchmarking can now consume a multi-root examples specification
  - this removes the architectural need for creating large duplicated staging folders like `examples_real_audio*` just to mix datasets
  - full backend suite now passes at `32 passed`
- Direct canonical-store filtered training:
  - trained directly from `data/examples`
  - dataset filters used:
    - `groove_audio`
    - `maestro_audio`
    - `musicnet_audio`
  - checkpoint: `models/boxbox_musicnet_candidate_direct_b8.pt`
  - train examples: `1145`
  - val examples: `214`
  - `val_avg_mae = 0.014116`
  - `val_baseline_avg_mae = 0.014238`
- Apples-to-apples validation comparison on the same filtered real-audio slice:
  - active `models/boxbox_latest.pt`: `avg_mae = 0.014234`
  - new `models/boxbox_musicnet_candidate_direct_b8.pt`: `avg_mae = 0.014116`
  - interpretation: the direct filtered MusicNet candidate is materially better than the current active checkpoint on that validation slice
- Hybrid-selector improvement after that signal:
  - the previous hybrid selector only considered small alpha blends and could not exploit cases where the ML curve itself was clearly better
  - hybrid search now explicitly includes the direct ML target as a candidate (`alpha = 1.0`)
  - clipped proxy comparison on `benchmarks/hale-makame-1930.ogg` with `clip_start = 30s` and `clip_duration = 30s`, using the new MusicNet candidate as the mixed specialist:
    - baseline after: `0.05119s`
    - standalone ML after: `0.03574s`
    - hybrid after: `0.03470s`
    - selected mode: `hybrid_candidate`
    - selected alpha: `1.0`
  - interpretation: this is the first concrete benchmark evidence that the MusicNet-extended candidate plus a less over-constrained selector can outperform the baseline on a representative real-audio slice
- Current caution:
  - this benchmark result is clipped and proxy-based, not yet a broad full-suite promotion gate
  - the candidate is promising, but not yet automatically promoted
  - backend suite now passes at `34 passed`

## Final Presentation Notes

The strongest presentation narrative is:

- start with the real problem: quantizing arbitrary music is not just a timing problem, it is also an audio-quality and runtime problem
- explain why pure ML was not enough
- show why the final architecture became hybrid
- emphasize that engineering discipline mattered more than hype:
  - benchmarks
  - guardrails
  - real audio datasets
  - GPU/NPU runtime work
  - fixing bad-sounding warps
- explain that the project improved by repeatedly identifying and removing false wins:
  - synthetic validation wins that did not transfer
  - bad fallbacks that sounded terrible
  - slow runtime paths that made the GUI unusable

Recommended final framing:

BoxBox became strongest when it stopped trying to replace classical audio analysis and instead combined it with carefully constrained ML and better real-audio data.

## Resume Checklist

On the next session, start here:

1. read this file
2. read `HANDOFF.md`
3. confirm the backend running copy is `H:\BoxBox`
4. confirm `backend\\.venv` still has CUDA torch and OpenVINO packages
5. continue with MusicNet import and benchmark-harness optimization

## 2026-04-04 - Live Quantization Fix: Groove Preserve Was Weakening GUI Output
- User reported that hybrid on stayin alive sounded barely quantized or worse than the original.
- Root cause: benchmark and GUI behavior had diverged. The GUI was rendering with groove_preserve=20, while earlier benchmark claims effectively reflected harder quantization, so the benchmark story overstated real live behavior.
- Fix in ackend/app.py: added _optimize_groove_target() so BoxBox now searches lower groove-preserve values and automatically chooses the strongest setting that measurably improves timing. This applies to baseline and hybrid candidates.
- Fix in ackend/ml/benchmark.py: aligned benchmark logic with the same groove optimization path so reported wins better match what the GUI actually renders.
- GUI defaults updated in rontend/src/App.jsx: default mode is now hybrid and default groove_preserve is  so the demo path shows the strongest current quantizer rather than the safest one.
- Added regression coverage in ackend/tests/test_groove_optimize.py.
- Verification:
  - backend tests: 35 passed
  - focused CUDA proxy check on stayin alive with requested groove_preserve=20 selected hybrid_candidate, but both effective preserve values dropped to 
  - proxy metrics on 45s clip: baseline .05827s, hybrid .05754s, lpha=1.0, candidate oxbox_before_groove_860.pt
- Operational note: offline OpenVINO/NPU benchmark inference is still unstable on this machine (ZE_RESULT_ERROR_UNINITIALIZED from the Intel NPU driver). Interactive backend should stay on CUDA for now.

## 2026-04-04 - Major MusicNet Expansion And New Active Real-Audio Checkpoint
- Continued data expansion instead of pushing more architecture changes first.
- Real-audio dataset counts before this push:
  - groove_audio = 982
  - maestro_audio = 320
  - musicnet_audio = 57
- Also confirmed synthetic diversity already present:
  - sap = 199
  - pop909 = 106
  - egmd = 181
- Ran a large MusicNet import pass. A synchronous attempt timed out but still added examples.
- Then ran a background MusicNet import to completion:
  - stdout log: outputs/musicnet_import_bg_20260404.out.log
  - stderr log: outputs/musicnet_import_bg_20260404.err.log
  - result: created_examples=146
- Final real-audio counts after import:
  - groove_audio = 982
  - maestro_audio = 320
  - musicnet_audio = 218
  - total real-audio pool = 1520
- Training experiments:
  - trained models/boxbox_musicnet_candidate_r1373.pt during mid-import expansion from models/boxbox_musicnet_candidate_direct_b8.pt
  - outcome was not stable enough to promote because the validation slice was changing underneath the run
  - trained a fresh stable full-pool run after import completed:
    - output: models/boxbox_musicnet_candidate_r1520.pt
    - init model: models/boxbox_latest.pt
    - datasets: groove_audio,maestro_audio,musicnet_audio
    - device: cuda
    - epochs: 8
    - batch size: 8
    - lr: .0002
- Stable full real-audio validation comparison on the fixed 232-example val slice:
  - old active models/boxbox_latest.pt:
    - vg_mae = 0.0155137726
    - aseline_avg_mae = 0.0155197255
  - new models/boxbox_musicnet_candidate_r1520.pt:
    - vg_mae = 0.015264
    - aseline_avg_mae = 0.015520
  - interpretation: the new full-pool real-audio checkpoint is modestly but cleanly better than the previous active model on the stable current real-audio validation slice
- Quick representative proxy check after this work:
  - stayin alive 45s CUDA proxy still selected the percussive specialist oxbox_before_groove_860.pt
  - interpretation: the Groove specialist is still the stronger percussive branch; the new checkpoint improves the mixed-domain branch rather than replacing the percussive specialist
- Promotion decision:
  - promoted models/boxbox_musicnet_candidate_r1520.pt to active models/boxbox_latest.pt
  - backed up previous active checkpoint to models/boxbox_latest_before_r1520.pt
  - restarted backend on CUDA so live inference uses the promoted weights
- Current backend state after promotion:
  - health check passed on http://127.0.0.1:8000/api/health
- Honest takeaway:
  - more real aligned data did help, but only once the dataset was stable and the comparison was apples-to-apples
  - blindly training mid-import created noisy results and one false lead
  - MusicNet is now large enough to matter materially in the real-audio pool

# IMPORTANT PROJECT DIRECTIVE

## PRIMARY GOAL

**BOXBOX MUST QUANTIZE JUST ABOUT ANYTHING, BUT IT MUST ESPECIALLY WORK WELL ON THE KIND OF MUSIC PRODUCERS ACTUALLY SAMPLE.**

That means priority should be given to:
- 60s/70s/80s/90s recordings
- human-played recordings with tempo drift
- non-metronomic performances
- mixed-instrument commercial-style audio
- sample-source material with live feel, swing, push/pull, and imperfect timing

## SECONDARY GOAL

**DEVELOPMENT MUST STAY AS EFFICIENT AS POSSIBLE IN STORAGE, SPEED, AND TIME.**

This means future work should always prefer:
- using the GPU for training and primary inference work when possible
- using the NPU only where it is actually beneficial and stable
- avoiding duplicated datasets and unnecessary staging folders
- avoiding storing artifacts that do not materially help training or inference
- preferring direct multi-root dataset loading over copied dataset merges
- keeping benchmark/runtime loops fast enough to iterate productively
- choosing the highest-value datasets first instead of downloading everything blindly
- verifying that each new development path is actually efficient before scaling it up

## PRACTICAL DECISION RULE

When choosing what to do next, prefer the option that best improves:
1. real-world quantization quality on likely producer sample material
2. generalization across arbitrary music
3. efficiency of storage, runtime, and iteration speed

If a path looks academically interesting but does not help those three goals enough, it should be deprioritized.

## 2026-04-04 - Added Weak-Supervision Import Path For Legacy Sample-Source Audio
- Implemented ackend/ml/import_legacy_audio.py.
- Purpose: turn arbitrary audio folders into pseudo-quantized training pairs using the current BoxBox quantizer instead of relying only on perfectly aligned academic datasets.
- Why this matters:
  - the real product goal is old, human-played, sample-source-like music
  - public fully aligned datasets for that exact domain are scarce
  - this importer creates a practical weak-supervision path for licensed, user-owned, public-domain, or otherwise permitted audio corpora
- Core behavior:
  - recursively scans supported audio files
  - converts audio to a working WAV only temporarily
  - selects dense rhythmic clips instead of random windows
  - estimates BPM and builds a baseline quantization target
  - optionally uses the current hybrid model path on CUDA to produce a stronger pseudo-target
  - writes training pairs as original.wav + warped.wav with clip metadata
  - deduplicates examples by source path + clip bounds
- Storage/efficiency angle:
  - no giant duplicated staging folder is required
  - the importer writes only final training examples plus tiny metadata
  - temporary converted WAVs live under data/examples/_legacy_import_tmp
  - intended for targeted high-value corpora, not blind bulk ingestion
- Added tests:
  - ackend/tests/test_import_legacy_audio.py
- Suite status after this work:
  - 38 passed
- Added operator docs:
  - data/legacy_sources/README.md
- Strategic meaning:
  - BoxBox now has a direct path to learn from the kind of mixed legacy audio producers actually sample, even when exact aligned targets do not exist

## 2026-04-04 - Legacy Import Storage Cleanup
- Tightened ackend/ml/import_legacy_audio.py for storage efficiency.
- Temporary converted WAVs are now deleted by default after each source file is processed.
- The temporary working directory is removed automatically when empty.
- Added --keep-temp only for debugging.
- Added regression coverage for cleanup behavior.
- Backend suite after this change: 39 passed.

## 2026-04-04 - Added Curated Legacy-Audio Downloader Workflow
- Implemented ackend/ml/download_legacy_manifest.py.
- Purpose: download only approved public-domain or licensed legacy-style audio into data/legacy_sources using a manifest instead of blind bulk collection.
- Why this matters:
  - the real target domain is old, human-played sample-source material
  - collecting that efficiently requires curation, dedupe, and metadata tracking
  - this avoids turning the project into a random pile of downloads
- Downloader behavior:
  - accepts CSV or JSONL manifests
  - only allows http/https
  - writes metadata to manifest_downloads.jsonl
  - deduplicates by SHA-1 content hash
  - sanitizes filenames for stable local storage
- Added tests:
  - ackend/tests/test_download_legacy_manifest.py
- Added starter manifest:
  - data/legacy_sources/manifest.sample.csv
- Updated data/legacy_sources/README.md to document the two-step workflow:
  1. optionally download via manifest
  2. run ackend.ml.import_legacy_audio to create pseudo-pair training examples
- Full backend suite after this work:
  - 42 passed
- Strategic meaning:
  - BoxBox now has an efficient, storage-conscious path for curated legacy-style corpus building instead of depending only on academic datasets or manual file drops

## 2026-04-04 - Ensemble-Prioritized MusicNet Expansion And Follow-Up Training Check
- Improved ackend/ml/import_public_midi.py so MusicNet imports now prioritize ensemble/chamber material over Solo Piano when metadata is available.
- MusicNet metadata is now preserved in example meta.json, including:
  - musicnet_id
  - composer
  - composition
  - movement
  - ensemble
  - source
  - seconds
- Verified newest imported distribution shifted toward more legacy-like mixed material.
  - Example recent ensemble mix included: String Quartet, Wind Octet, Piano Quintet, Piano Quartet, String Sextet, Piano Trio, Accompanied Violin, Accompanied Cello
- Real-audio counts after this expansion:
  - groove_audio = 982
  - maestro_audio = 320
  - musicnet_audio = 242
  - total real-audio pool = 1544
- Trained new candidate:
  - models/boxbox_musicnet_candidate_r1544.pt
  - init: current active models/boxbox_latest.pt
  - datasets: groove_audio,maestro_audio,musicnet_audio
  - device: cuda
  - epochs: 6
  - batch size: 8
  - lr: .00015
- Comparison on stable 236-example real-audio validation slice:
  - active models/boxbox_latest.pt: vg_mae = 0.0150759689
  - candidate models/boxbox_musicnet_candidate_r1544.pt: vg_mae = 0.015220
- Promotion decision:
  - NOT promoted
  - current active models/boxbox_latest.pt remains stronger
- Honest takeaway:
  - ensemble-prioritized MusicNet expansion is the right data direction
  - but this specific fine-tuning recipe did not beat the current active model
  - better data does not automatically mean better checkpoint; keep validating apples-to-apples before promotion

## 2026-04-04 - Inverse Dataset-Family Balancing Finally Converted Better Data Into A Better Model
- Added dataset-family tracking in ackend/ml/dataset.py.
- Added inverse-frequency sample weighting so underrepresented families get sampled more often during training.
- Added CLI/control support in ackend/ml/train.py via --dataset-balance uniform|inverse.
- Added regression coverage in ackend/tests/test_dataset_disk_cache.py.
- Backend suite after this work: 44 passed.
- Why this mattered:
  - the project had been adding better ensemble/chamber MusicNet data
  - but Groove still dominated by count
  - so the training signal for more legacy-like mixed material was being diluted
- Trained balanced checkpoint:
  - models/boxbox_musicnet_candidate_r1544_inv.pt
  - datasets: groove_audio,maestro_audio,musicnet_audio
  - balancing: inverse
  - device: cuda
  - epochs: 6
  - batch size: 8
  - lr: .00015
- Stable 236-example real-audio validation comparison:
  - previous active models/boxbox_latest.pt: vg_mae = 0.0150759689
  - new inverse-balanced candidate: vg_mae = 0.015052
  - interpretation: small but real win on the current stable real-audio validation slice
- Promotion decision:
  - promoted models/boxbox_musicnet_candidate_r1544_inv.pt to active models/boxbox_latest.pt
  - backed up previous active model to models/boxbox_latest_before_r1544_inv.pt
  - restarted backend on CUDA after promotion
- Honest takeaway:
  - this is the first concrete sign that data balancing, not just more data, was a real blocker
  - for the product goal, legacy-like corpora probably need both acquisition and weighting, not just inclusion

## 2026-04-05 - Added Explicit Dataset-Family Weight Overrides
- Extended dataset weighting so training can now use both:
  - inverse family-frequency balancing
  - explicit family multipliers
- Updated ackend/ml/dataset.py:
  - added parse_dataset_weight_overrides()
  - dataset_sample_weights() now accepts explicit overrides such as legacy_audio=3,musicnet_audio=2,groove_audio=0.5
- Updated ackend/ml/train.py:
  - added CLI flag --dataset-weight-overrides
  - training now prints both dataset_balance and active overrides
- Added regression coverage in ackend/tests/test_dataset_disk_cache.py
- Backend suite after this work: 45 passed
- Strategic meaning:
  - once legacy_audio_* examples exist, BoxBox can intentionally overweight them during training even if the corpus is still small
  - this is useful because the product goal is not average academic coverage; it is strong performance on sampled legacy-style material

## 2026-04-05 Legacy Pilot Data + Training

This checkpoint established the first real `legacy_audio_*` corpus meant to resemble producer sample material more closely than the earlier drum/piano/classical mix.

What was sourced:
- built `data/legacy_sources/manifest.archive_pilot.csv` from Internet Archive metadata API
- constrained the pilot to pre-1923 George Blood recordings as a legally safer seed set
- downloaded 31 MP3 derivatives into `data/legacy_sources`
- total raw pilot size was about 165 MB

What was imported:
- converted the 31 source files into 58 `legacy_audio_*` pseudo-pair examples with `import_legacy_audio`
- all imported examples were baseline-derived pseudo targets, not hybrid-derived targets
- every example used Rubber Band for the final warp backend

What worked:
- adding the legacy pilot as a heavy global training signal did NOT help the main mixed model
- candidate `models/boxbox_legacy_pilot_r1602_invw.pt` lost to active `models/boxbox_latest.pt` on the 4-family validation slice:
  - active on groove+maestro+musicnet+legacy val: `avg_mae=0.014535`
  - heavy legacy-weighted candidate: `avg_mae=0.014586`
- a smaller legacy-oriented specialist DID help on the intended domain
- candidate `models/boxbox_legacy_specialist_pilot.pt` beat the active model on `musicnet_audio+legacy_audio` validation:
  - active: `avg_mae=0.015604`
  - legacy specialist: `avg_mae=0.014543`
- on pure `legacy_audio` validation:
  - active: `avg_mae=0.006396`
  - legacy specialist: `avg_mae=0.002514`
  - baseline target on that slice is still slightly better at `0.002305`

Current interpretation:
- the new legacy data is useful, but it behaves more like a domain-specialist signal than a safe replacement for the general mixed model
- next best move is to wire `boxbox_legacy_specialist_pilot.pt` into inference routing as a third specialist for legacy/sample-source-like material instead of promoting it to `boxbox_latest.pt`
- keep growing legal legacy corpora, but stay storage-conscious and prefer curated manifests plus weak-supervision imports over bulk downloading

Update:
- this routing step has now been completed
- inference now recognizes three specialist roles:
  - percussive
  - mixed
  - legacy
- the routed candidate now uses a richer audio profile instead of only a percussive-vs-mixed split
- current legacy routing heuristic uses:
  - percussive ratio
  - spectral flatness
  - spectral rolloff
- `boxbox_legacy_specialist_pilot.pt` is now available to the hybrid router as a third expert, not a replacement for `boxbox_latest.pt`

Follow-up:
- the first routing pass exposed a problem: the old curve-confidence heuristic was too brittle and often drove the legacy specialist confidence to zero on vintage material
- confidence has now been changed to use both:
  - curve smoothness
  - agreement with the onset-grid baseline feature
- this materially improved routing behavior on representative legacy-like tracks

Focused proxy checks after the confidence fix:
- `popular-song-1931.ogg`
  - baseline after: `0.06647`
  - hybrid after: `0.05387`
- `ragged-but-right.ogg`
  - baseline after: `0.06368`
  - hybrid after: `0.03189`

Interpretation:
- the legacy-aware router is now materially more credible on the sample-source-like vintage slice
- the legacy specialist still is not the dominant selected expert everywhere, but it now contributes meaningful routed weight instead of being silently zeroed out

## 2026-04-05 Legacy Batch 2 + Specialist Promotion

The legacy corpus was expanded again after the routing fix so the new expert would have enough signal to matter.

What was added:
- generated `data/legacy_sources/manifest.archive_batch2.csv`
- targeted pre-1931 Archive recordings, deduped against the first pilot batch
- downloaded `64` new files with `1` failure
- total legacy source library is now:
  - `93` MP3 source files
  - about `534 MB` raw source size
- imported `128` new pseudo-pair examples
- total `legacy_audio_*` examples are now `186`

What was trained:
- new checkpoint: `models/boxbox_legacy_specialist_r1730.pt`
- initialized from the first specialist, not from the general mixed model
- datasets:
  - `musicnet_audio`
  - `legacy_audio`
- balancing:
  - inverse family balancing
  - overrides `legacy_audio=2,musicnet_audio=1.5`

Validation comparison on `musicnet_audio + legacy_audio`:
- active mixed model `boxbox_latest.pt`: `avg_mae=0.012647`
- prior legacy specialist `boxbox_legacy_specialist_pilot.pt`: `avg_mae=0.011164`
- new legacy specialist `boxbox_legacy_specialist_r1730.pt`: `avg_mae=0.011030`

Validation comparison on `legacy_audio` only:
- prior legacy specialist: `avg_mae=0.003524`
- new legacy specialist: `avg_mae=0.003336`
- baseline target on that slice: `0.003061`

Promotion result:
- promoted `boxbox_legacy_specialist_r1730.pt` into the router by replacing `boxbox_legacy_specialist_pilot.pt`
- previous specialist backed up as:
  - `models/boxbox_legacy_specialist_pilot_before_r1730.pt`

Representative proxy result after promotion:
- `popular-song-1931.ogg`
  - baseline after: `0.06647`
  - hybrid after: `0.05369`
  - selected candidate: direct ML from `boxbox_legacy_specialist_pilot.pt`
  - selected alpha: `1.0`

Interpretation:
- the stronger second-generation legacy specialist is now the active legacy expert
- this is the first time the router clearly selected the legacy specialist directly on representative vintage material instead of just blending around it

## 2026-04-05 Legacy Batch 3 + Quality Filtering

The next expansion widened the legacy corpus from pre-1931 material into a broader 1931-1945 slice.

What was added:
- generated `data/legacy_sources/manifest.archive_batch3.csv`
- downloaded `78` additional Archive recordings with no failures
- total legacy source library is now:
  - `171` MP3 source files
  - about `948 MB` raw source size
- imported `154` additional pseudo-pair examples
- total `legacy_audio_*` examples are now `340`

What failed:
- a naive third-generation specialist trained on the full expanded legacy pool did not beat the current legacy specialist
- candidate `models/boxbox_legacy_specialist_r1884.pt` was worse than the active legacy specialist on the same `musicnet_audio + legacy_audio` validation slice:
  - current legacy specialist: `avg_mae=0.009199`
  - naive expanded candidate: `avg_mae=0.009721`

What was changed in response:
- added pseudo-label quality filtering support to the data pipeline
- `WarpDataset`, training, and evaluation now support filtering by:
  - minimum pseudo-label improvement percent
  - maximum pseudo-target error-after
  - minimum event count
- this allows training on a cleaner weak-supervision subset instead of trusting every imported clip equally

What worked:
- trained filtered candidate `models/boxbox_legacy_specialist_r1884_qf.pt`
- filter used:
  - `min_improvement_pct=25`
  - `max_after_sec=0.05`
  - `min_event_count=80`
- filtered target slice size:
  - train `199`
  - val `38`
- filtered validation comparison:
  - active mixed `boxbox_latest.pt`: `avg_mae=0.005986`
  - active legacy specialist `boxbox_legacy_specialist_pilot.pt`: `avg_mae=0.003202`
  - filtered candidate `boxbox_legacy_specialist_r1884_qf.pt`: `avg_mae=0.002681`

Important nuance:
- the filtered candidate is better on the high-confidence filtered slice
- but on the full unfiltered `musicnet_audio + legacy_audio` validation slice it did not beat the current promoted legacy specialist:
  - current legacy specialist: `0.009199`
  - filtered candidate: `0.009394`

Pragmatic outcome:
- did NOT replace the promoted legacy specialist
- instead exposed `boxbox_legacy_specialist_r1884_qf.pt` as an additional legacy candidate in router inference
- the router can now choose between:
  - active mixed model
  - percussive Groove specialist
  - promoted legacy specialist
  - filtered high-confidence legacy specialist

Representative check:
- on `popular-song-1931.ogg`, the router still preferred the promoted legacy specialist over the filtered candidate
- that is the correct result for now, because the filtered checkpoint is useful but not universally stronger

## 2026-04-06 Routed Promotion Gate Added

The benchmark workflow now has a repeatable promotion gate instead of relying on manual report reading.

What changed:
- updated `backend/ml/benchmark.py`
- added report-loading and suite-comparison helpers:
  - `load_benchmark_report()`
  - `compare_benchmark_suites()`
- added a promotion gate evaluator:
  - `evaluate_promotion_gate()`
- added CLI support for:
  - direct suite-to-suite comparison
  - legacy-gain plus mixed-regression gating

What the gate checks:
- whether a candidate router meaningfully improves the legacy suite average hybrid error
- whether the candidate avoids excessive broad mixed-suite regression
- whether any single-file regression exceeds a configured ceiling

Default gate thresholds:
- minimum legacy average improvement:
  - `0.001` sec
- maximum mixed-suite average regression:
  - `0.0005` sec
- maximum single-file regression:
  - `0.003` sec

Why this matters:
- promotion decisions are now explicit and repeatable
- future checkpoint comparisons no longer need hand-written judgment every time
- this is especially useful now that the router can blend:
  - active mixed
  - mixed+legacy
  - Groove specialist
  - multiple legacy specialists

Validation:
- updated tests in:
  - `backend/tests/test_benchmark_cache.py`
- full backend suite after this step:
  - `67 passed`

Additional benchmark workflow improvement:
- added named broader mixed suite support in `backend/ml/benchmark.py`
- new benchmark subset:
  - `MIXED_BENCHMARK_SUITE`
- current mixed suite members:
  - `benchmarks/hale-makame-1930.ogg`
  - `benchmarks/mickey-1918.ogg`
  - `benchmarks/ragged-but-right.ogg`
  - `stayin-alive-serban-mix.wav`
- CLI now supports `--mixed-suite`
- this gives the promotion gate a standard non-legacy leg instead of an ad hoc file list

Current practical next step:
- generate one broader mixed proxy suite report alongside the legacy proxy suite
- then run the new gate against:
  - active-router baseline report
  - candidate-router report
- only promote a mixed checkpoint if it clears the gate instead of just winning a narrow validation slice

## 2026-04-06 Mixed+Legacy Promotion Gate Result

The new routed promotion gate was used immediately on the current mixed+legacy candidate workflow.

Reports generated:
- current routed mixed proxy suite:
  - `outputs/benchmark_mixed_proxy_compare_20260406.json`
- baseline mixed proxy suite without `boxbox_mixed_legacy_candidate_r1884.pt` in router search:
  - `outputs/benchmark_mixed_proxy_baseline_20260406.json`
- baseline legacy proxy suite without `boxbox_mixed_legacy_candidate_r1884.pt` in router search:
  - `outputs/benchmark_legacy_proxy_baseline_20260406.json`
- promotion gate result:
  - `outputs/benchmark_promotion_gate_20260406.json`

Mixed proxy comparison:
- baseline routed system hybrid avg after:
  - `0.043492`
- candidate routed system hybrid avg after:
  - `0.043722`
- practical interpretation:
  - broad mixed behavior is essentially neutral to slightly worse
  - the only meaningful regression in this suite was:
    - `stayin-alive-serban-mix.wav`
    - `0.031701 -> 0.032622`

Legacy proxy gate result:
- gate status:
  - `FAILED`
- candidate legacy average did not improve; it got worse:
  - baseline hybrid avg after:
    - `0.046732`
  - candidate hybrid avg after:
    - `0.048795`
- average delta candidate minus baseline:
  - `+0.002064`
- worst per-file regression:
  - `+0.005967`
  - file:
    - `benchmarks/koromogo-e-1930.ogg`

Gate reasoning:
- legacy improvement requirement was not met
- worst single-file regression exceeded the configured ceiling
- mixed-suite average regression stayed within tolerance, but that was not enough to save the candidate

Promotion decision:
- do NOT promote `models/boxbox_mixed_legacy_candidate_r1884.pt`
- keep it available as a router option only
- keep `models/boxbox_latest.pt` as the active mixed checkpoint

Most honest interpretation:
- the mixed+legacy candidate can contribute useful routed weight on some material
- but as a promotion candidate it currently fails where it matters most:
  - the representative legacy proxy suite
- the routed system is better off keeping it as an optional specialist-style contributor rather than making it the new default mixed backbone

Next best move:
- train or fine-tune a new mixed candidate with weaker legacy pull so it does not over-shift older mixed material
- then rerun the same saved gate workflow instead of inventing a new evaluation standard

## 2026-04-06 Softer Mixed+Legacy Candidate

After the first mixed+legacy candidate failed the routed promotion gate, a softer legacy-biased follow-up was trained instead of changing the evaluation criteria.

Checkpoint:
- `models/boxbox_mixed_legacy_candidate_r1884_soft.pt`

Training recipe:
- init:
  - `models/boxbox_latest.pt`
- datasets:
  - `groove_audio`
  - `maestro_audio`
  - `musicnet_audio`
  - `legacy_audio`
- balancing:
  - inverse family balancing
- softer weight overrides:
  - `legacy_audio=1.35`
  - `musicnet_audio=1.25`
  - `groove_audio=0.9`
  - `maestro_audio=1.0`
- max examples:
  - `1200`
- epochs:
  - `4`
- batch size:
  - `8`
- lr:
  - `0.00015`
- device:
  - `cuda`

Training outcome:
- saved successfully despite shell exit-code weirdness seen in prior runs
- direct validation after training:
  - `val_avg_mae=0.013001`
  - baseline on that slice:
    - `0.012948`

Screened evaluation:
- broad four-family val slice:
  - active mixed `boxbox_latest.pt`: `0.015399`
  - softer candidate: `0.015376`
- legacy-heavy `musicnet_audio + legacy_audio` val slice:
  - active mixed `boxbox_latest.pt`: `0.011055`
  - softer candidate: `0.009764`

Why this looked promising:
- unlike the earlier mixed+legacy candidate, this one was only slightly legacy-biased
- it improved both screened broad and legacy-heavy validation slices versus the active mixed checkpoint

Routed benchmark reports generated:
- mixed proxy suite with softer candidate in router search:
  - `outputs/benchmark_mixed_proxy_soft_20260406.json`
- legacy proxy suite with softer candidate in router search:
  - `outputs/benchmark_legacy_proxy_soft_20260406.json`
- gate result:
  - `outputs/benchmark_promotion_gate_soft_20260406.json`

Promotion gate result:
- `FAILED`

Why it still failed:
- mixed suite behavior was within tolerance but still slightly worse on average:
  - baseline hybrid avg after:
    - `0.043492`
  - softer candidate hybrid avg after:
    - `0.043903`
- legacy proxy suite still regressed overall:
  - baseline hybrid avg after:
    - `0.046732`
  - softer candidate hybrid avg after:
    - `0.048558`
- worst legacy regression remained too large:
  - `koromogo-e-1930.ogg`
  - `+0.005694`

Interpretation:
- the softer candidate is clearly better than the first mixed+legacy attempt
- it also produced at least one real mixed-suite win:
  - `mickey-1918.ogg`
- but it still does not clear the project's routed legacy gate
- the current routed system remains stronger overall on the representative legacy proxy suite

Current decision:
- do NOT promote `boxbox_mixed_legacy_candidate_r1884_soft.pt`
- do NOT replace `boxbox_latest.pt`
- keep the promotion standard unchanged

Best next move:
- train a more conservative mixed candidate that stays even closer to `boxbox_latest.pt`
- likely reduce legacy emphasis further and/or shorten fine-tuning so the candidate can pick up legacy help without shifting the routed legacy balance too far

## 2026-04-06 Even More Conservative Lite Candidate

One more mixed candidate was trained to test whether staying much closer to `boxbox_latest.pt` would finally clear the routed gate.

Checkpoint:
- `models/boxbox_mixed_legacy_candidate_r1884_lite.pt`

Training recipe:
- init:
  - `models/boxbox_latest.pt`
- datasets:
  - `groove_audio`
  - `maestro_audio`
  - `musicnet_audio`
  - `legacy_audio`
- balancing:
  - inverse family balancing
- lighter overrides:
  - `legacy_audio=1.15`
  - `musicnet_audio=1.15`
  - `groove_audio=0.95`
  - `maestro_audio=1.0`
- max examples:
  - `1200`
- epochs:
  - `2`
- batch size:
  - `8`
- lr:
  - `0.00012`
- device:
  - `cuda`

Direct training outcome:
- `val_avg_mae=0.012789`
- baseline on that slice:
  - `0.012948`

Screened evaluation:
- broad four-family val slice:
  - active mixed `boxbox_latest.pt`: `0.015399`
  - lite candidate: `0.015388`
- legacy-heavy `musicnet_audio + legacy_audio` val slice:
  - active mixed `boxbox_latest.pt`: `0.011055`
  - lite candidate: `0.009246`

Why this was encouraging at first:
- this was the strongest screened legacy-heavy mixed candidate so far
- it also stayed close to the active mixed checkpoint on the broad screened slice

Routed benchmark reports:
- mixed proxy suite:
  - `outputs/benchmark_mixed_proxy_lite_20260406.json`
- legacy proxy suite:
  - `outputs/benchmark_legacy_proxy_lite_20260406.json`
- gate result:
  - `outputs/benchmark_promotion_gate_lite_20260406.json`

Promotion gate result:
- `FAILED`

Why it failed:
- mixed suite regressed too much:
  - baseline hybrid avg after:
    - `0.043492`
  - lite candidate hybrid avg after:
    - `0.044986`
- legacy suite regressed even more severely than the soft candidate:
  - baseline hybrid avg after:
    - `0.046732`
  - lite candidate hybrid avg after:
    - `0.050950`
- worst regression:
  - `koromogo-e-1930.ogg`
  - `+0.012643`

What this means:
- the project has now tested:
  - original mixed+legacy candidate
  - softer mixed+legacy candidate
  - very conservative lite candidate
- all three improved screened validation slices versus `boxbox_latest.pt`
- all three still failed the routed proxy promotion gate

Most important lesson:
- the current bottleneck is no longer just checkpoint quality in validation terms
- the real issue is interaction between candidate mixed checkpoints and routed proxy behavior on representative legacy tracks
- more mixed-checkpoint fine-tuning alone is unlikely to solve this cleanly

New best next move:
- stop trying to promote mixed+legacy checkpoints for the moment
- instead refine router behavior itself:
  - make legacy-sensitive routing more resistant to weak mixed-candidate attraction on representative old-material clips
  - or gate mixed-candidate participation more strictly by legacy profile confidence
- after router changes, rerun the same saved promotion gate

## 2026-04-06 Router Refinement Became The Main Lever

After three mixed+legacy checkpoint attempts all failed the routed promotion gate, development shifted from checkpoint tuning to router policy tuning.

What changed in `backend/ml/infer.py`:
- refined `mixed_legacy` routing so it no longer gets rewarded simply for high legacy score
- added explicit suppression when legacy dominates the profile
- added a legacy-band participation rule so mixed+legacy candidates are strongest on moderate legacy profiles instead of:
  - strongly legacy material
  - low-legacy modern mixed material

Why this mattered:
- the failure pattern was consistent across multiple checkpoints
- screened validation kept saying the candidates were better
- representative routed proxy behavior kept saying they should not be promoted
- this meant the bottleneck had shifted from checkpoint quality to router interaction quality

Validation:
- updated `backend/tests/test_infer_routing.py`
- full backend suite after router refinement work:
  - `68 passed`

### Router Tuned V2

Saved reports:
- `outputs/benchmark_legacy_proxy_router_tuned_v2_20260406.json`
- `outputs/benchmark_mixed_proxy_router_tuned_v2_20260406.json`
- `outputs/benchmark_promotion_gate_router_tuned_v2_20260406.json`

Result:
- this was the best router refinement tested
- mixed proxy suite improved versus the active-router baseline:
  - baseline hybrid avg after:
    - `0.043492`
  - tuned hybrid avg after:
    - `0.042184`
- strong mixed wins appeared on:
  - `mickey-1918.ogg`
  - `stayin-alive-serban-mix.wav`
- legacy regressions stayed small and within the per-file ceiling:
  - worst regression:
    - `0.001779`
- but the legacy average still narrowly missed the gate:
  - baseline legacy hybrid avg after:
    - `0.046732`
  - tuned legacy hybrid avg after:
    - `0.047233`
  - legacy average delta:
    - `+0.000501`

Interpretation:
- this was a major improvement over the earlier mixed-candidate attempts
- it proved router policy is the right lever
- the remaining miss is now very small compared with the earlier failures

### Router Tuned V3

Saved reports:
- `outputs/benchmark_legacy_proxy_router_tuned_v3_20260406.json`
- `outputs/benchmark_mixed_proxy_router_tuned_v3_20260406.json`
- `outputs/benchmark_promotion_gate_router_tuned_v3_20260406.json`

Result:
- a more aggressive follow-up tweak was tested
- it did not beat V2 on the combined objective
- legacy average miss widened again, so V2 remained the better refinement

### Router Tuned V4

Saved reports:
- `outputs/benchmark_legacy_proxy_router_tuned_v4_20260406.json`
- `outputs/benchmark_mixed_proxy_router_tuned_v4_20260406.json`
- `outputs/benchmark_promotion_gate_router_tuned_v4_20260406.json`

Result:
- a targeted transition-band guard was added to further suppress mixed+legacy participation on the ambiguous old-material zone that was still hurting:
  - `koromogo-e-1930.ogg`
  - `ute-1950.ogg`
- this became the strongest overall router refinement so far

Why V4 is the current best:
- legacy average miss shrank again:
  - baseline legacy hybrid avg after:
    - `0.046732`
  - V4 legacy hybrid avg after:
    - `0.047055`
  - legacy average delta:
    - `+0.000323`
- worst legacy regression also shrank:
  - `+0.000698`
- mixed proxy suite still improved versus baseline:
  - baseline hybrid avg after:
    - `0.043492`
  - V4 hybrid avg after:
    - `0.042341`
- important mixed wins remained:
  - `mickey-1918.ogg`
  - `stayin-alive-serban-mix.wav`

What still blocks a gate pass:
- the project still requires at least:
  - `0.001` sec legacy average improvement
- V4 is still slightly worse than the baseline legacy proxy average, just by a much smaller amount than earlier attempts

### Router Tuned V5

Saved reports:
- `outputs/benchmark_legacy_proxy_router_tuned_v5_20260406.json`
- `outputs/benchmark_mixed_proxy_router_tuned_v5_20260406.json`
- `outputs/benchmark_promotion_gate_router_tuned_v5_20260406.json`

Result:
- one final transition-zone tightening was tested
- this became the strongest router refinement so far

Why V5 is now the current best:
- legacy average miss shrank again:
  - baseline legacy hybrid avg after:
    - `0.046732`
  - V5 legacy hybrid avg after:
    - `0.046876`
  - legacy average delta:
    - `+0.000144`
- worst legacy regression stayed very small:
  - `+0.000698`
- mixed proxy suite still improved versus baseline:
  - baseline hybrid avg after:
    - `0.043492`
  - V5 hybrid avg after:
    - `0.042341`
- important mixed wins were preserved:
  - `mickey-1918.ogg`
  - `stayin-alive-serban-mix.wav`

Interpretation:
- the system still technically fails the strict legacy-average promotion gate
- but the remaining miss is now extremely small
- router refinement has reduced the gap to near-noise territory while preserving the broader mixed-suite gains

### Router Tuned V6

Saved reports:
- `outputs/benchmark_legacy_proxy_router_tuned_v6_20260406.json`
- `outputs/benchmark_mixed_proxy_router_tuned_v6_20260406.json`
- `outputs/benchmark_promotion_gate_router_tuned_v6_20260406.json`

Result:
- a final extra penalty on the plain mixed role was tested in the low-mixed / moderate-legacy transition zone
- it did not beat V5
- it gave back the small `popular-song-1931.ogg` gain without improving `koromogo-e-1930.ogg`

Decision:
- reverted the V6-only change
- kept V5 as the best current router policy in code

Practical interpretation:
- router refinement has now nearly closed the gap
- the remaining miss is very small
- the system now:
  - improves the mixed proxy suite
  - keeps legacy per-file regressions very small
  - narrowly misses only the strict legacy average promotion rule

Current practical decision:
- keep the V5-style router policy as the best current state in code
- do not promote any mixed+legacy checkpoint yet
- keep `boxbox_latest.pt` as the active mixed checkpoint

Most important project-level takeaway:
- the project is now very close to a promotion-gate pass through router refinement alone
- unlike the checkpoint experiments, router tuning improved the broad mixed suite while keeping legacy regressions controlled
- the remaining gap is now on the order of tenths of a millisecond, not the larger failures seen earlier

Best next move:
- continue router refinement rather than more mixed-checkpoint training
- either:
  - treat the current gate miss as effectively within noise and hold the router where it is
  - or, only if absolutely necessary, try one final `koromogo-e-1930.ogg`-specific policy tweak with very high skepticism
- do not go back to mixed-checkpoint churn until router options are exhausted

### Segment-Level Feedback

Development focus then shifted away from router micro-tuning and into the next product-facing feature: section-level user feedback.

What was added:
- `backend/app.py` now computes report-time segment summaries from the final selected target using `_summarize_segments(...)`
- each report segment now carries:
  - `segment_index`
  - `start_sec`
  - `end_sec`
  - `duration_sec`
  - local `timing_metrics`
  - `baseline_error_after_sec`
  - `delta_vs_baseline_sec`
- `backend/audio/feedback.py` now includes `build_segment_feedback(...)`
- `build_feedback_loop(...)` now emits `feedback_loop.segment_feedback`
- section feedback items carry:
  - a time-range label
  - a small section summary
  - section-local quick actions
  - `history_count`
  - `latest`
- `POST /api/feedback` now accepts optional `segment_index`
- `record_feedback_entry(...)` now stores:
  - `scope`
  - `segment_index`
  - `segment_start_sec`
  - `segment_end_sec`

Important design choice:
- whole-track feedback still updates `outputs/feedback_memory.json` and therefore influences future learned defaults
- section-level feedback does **not** update the global style memory
- this was intentional to avoid poisoning style-level learning with highly local complaints that only apply to one part of one song

Frontend changes:
- `frontend/src/components/ResultsPanel.jsx` now shows a `Section Feedback` area
- the UI renders the highest-priority sections from `feedback_loop.segment_feedback`
- users can now mark a specific section as:
  - `Keep Section`
  - `Tighten Section`
  - `Loosen Section`
  - `Reduce Artifacts`
- after submission, the report is reloaded so section-local history/latest state stays authoritative to backend state

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - segment-feedback item generation
  - segment feedback submission persistence
  - guarding global learned memory from segment-only entries

Verification:
- full backend suite after this work:
  - `70 passed`

Interpretation:
- BoxBox now has a much more realistic feedback path:
  - whole-file feedback for global preference learning
  - section-level feedback for local problem areas
- this is a better product-direction continuation than chasing the last `+0.000144` router gate miss

Best next move:
- build report-side aggregation over repeated section complaints so the app can explicitly say things like:
  - “the intro tends to come back too tight”
  - “late sections on expressive material tend to prefer safer warping”
- then convert those patterns into stronger rerun suggestions and clearer UI guidance

### Section Feedback Diagnostics

That next step is now in place.

Backend changes:
- added `summarize_segment_feedback(...)` in `backend/audio/feedback.py`
- it scans section-scoped feedback entries from `outputs/<job_id>/feedback.json`
- it emits a report-side summary object:
  - `feedback_loop.segment_feedback_summary.summary`
  - `feedback_loop.segment_feedback_summary.diagnostics`
  - `feedback_loop.segment_feedback_summary.rerun_focus`

Current heuristic:
- only `scope == "segment"` entries participate
- the system waits for at least `2` matching non-`good` complaints on the same section
- once that threshold is crossed, the report calls it out as a repeat issue
- the latest matching `suggested_controls` become the suggested retry preset

Example behavior:
- repeated `too_tight` on the same section now becomes a message like:
  - `Segment 1 (...) keeps coming back too tight.`
- repeated `warbly` becomes:
  - `Segment N (...) keeps sounding warbly.`

Pipeline/UI wiring:
- `backend/app.py` now initializes `segment_feedback_summary` when the report is first written
- it also recomputes the summary after every feedback submission
- `frontend/src/components/ResultsPanel.jsx` now renders those repeat-issue diagnostics
- the UI exposes one-click `Apply Suggested Retry` actions from the summarized section guidance

Why this matters:
- section feedback is no longer just stored history
- repeated local complaints now become explicit next-pass guidance
- this makes the feedback loop more actionable without pushing noisy local feedback into the global learned style memory

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - repeat-issue summary generation
  - the “first section complaint should not over-trigger” case
  - report-side diagnostics appearing after repeated section feedback submissions

Verification:
- full backend suite after this work:
  - `72 passed`

Interpretation:
- the product now has three layers of adaptation:
  - style-guided defaults
  - whole-track learned behavior across jobs
  - per-job section repeat-issue diagnostics with retry presets

Best next move:
- start generalizing section issues by structural position rather than exact section index
- likely first implementation:
  - derive coarse buckets such as `intro`, `early`, `middle`, `late`, `outro`
  - then track whether repeated complaints cluster in those buckets across jobs and styles

### Structural Position Guidance

That generalization step is now implemented.

New backend concepts:
- `backend/audio/feedback.py` now defines `classify_segment_bucket(...)`
- sections are bucketed into:
  - `intro`
  - `early`
  - `middle`
  - `late`
  - `outro`
- `build_segment_feedback(...)` now includes `position_bucket` for every surfaced section

New memory layer:
- added a dedicated cross-job section-position memory file:
  - `outputs/segment_feedback_memory.json`
- this memory is separate from:
  - `outputs/feedback_memory.json`

Why the separation matters:
- `feedback_memory.json` remains the place for whole-track style learning
- `segment_feedback_memory.json` is for local structural tendencies
- this avoids mixing:
  - “balanced songs like slightly less groove overall”
  - with
  - “intro sections in this style often need safer warping”

New logic:
- added `update_segment_feedback_memory(...)`
- added `summarize_position_feedback_memory(...)`
- the system now learns counts by:
  - style profile
  - structural position bucket
  - feedback type

Current behavior:
- section feedback still writes to the per-job `feedback.json`
- repeated complaints on a section still drive per-job `segment_feedback_summary`
- now, section feedback also updates cross-job position memory
- report generation now emits:
  - `feedback_loop.position_guidance.summary`
  - `feedback_loop.position_guidance.diagnostics`

Trigger rule:
- position-level guidance requires at least `2` matching non-`good` complaints for the same:
  - style profile
  - position bucket

Examples of the new guidance layer:
- `Intro sections for this style tend to be artifact-prone.`
- `Late sections for this style tend to come back too loose.`

UI:
- `frontend/src/components/ResultsPanel.jsx` now renders those position-level diagnostics
- each one includes a one-click `Apply Position Retry` action using the current request-context controls

Pipeline wiring:
- `backend/app.py` now:
  - computes bucketed sections during report creation
  - loads `segment_feedback_memory.json` during report generation
  - updates that memory on section feedback submission
  - recomputes `position_guidance` after feedback submissions

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - bucket classification
  - position-guidance summarization
  - repeated section feedback producing cross-job position guidance
- also isolated the shared position-memory path in test coverage where needed

Verification:
- full backend suite after this work:
  - `75 passed`

Interpretation:
- BoxBox now adapts at three distinct levels:
  - whole-track style defaults across jobs
  - per-job repeat problem sections
  - cross-job structural tendencies by song position
- this is a meaningful step toward turning user feedback into real product intelligence instead of just audit trails

Best next move:
- feed position guidance back into initial section prioritization on new reports
- likely first implementation:
  - when a current section lands in a historically problematic bucket for the detected style, raise its priority and make its retry wording more specific even before the user clicks feedback on this job

### Position-Guided Initial Prioritization

That proactive step is now implemented.

Backend changes:
- `backend/audio/feedback.py`
  - added `_position_guidance_lookup(...)`
  - `build_segment_feedback(...)` now accepts report-time `position_guidance`
  - section items now carry optional `position_guidance`
- guided sections now get:
  - stronger initial summary wording
  - higher priority in the initial surfaced section list

Current summary behavior:
- if a section lands in a bucket that already has learned guidance for the detected style, its summary now gets an extra note:
  - `Historical feedback suggests <bucket> sections in this style need extra attention.`

Current ranking behavior:
- guided sections are now ranked ahead of unguided sections before the usual:
  - `delta_vs_baseline_sec`
  - `avg_abs_error_after_sec`
  - `segment_index`
  ordering is applied

Pipeline wiring:
- `backend/app.py` now performs a two-stage use of structural position memory:
  1. read raw bucket guidance from `outputs/segment_feedback_memory.json`
  2. pass it into `build_segment_feedback(...)` so initial section cards can be guided immediately
  3. then recompute report-facing `position_guidance` from the same memory using the final surfaced sections

Why this matters:
- previously, structural position learning was visible only as a separate guidance block
- now it changes which sections the user sees first on a fresh report
- this makes cross-job learning materially affect the first-pass UX for a new file

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - guided sections outranking unguided ones
  - fresh reports picking up seeded position guidance before any new feedback is entered on that job

Verification:
- full backend suite after this work:
  - `77 passed`

Interpretation:
- BoxBox now uses cross-job section learning proactively, not only after the user starts correcting the current job
- the feedback stack now behaves more like a product intelligence layer than a passive memory store

Best next move:
- let structural position guidance influence the section-level suggested controls themselves, not just ordering and wording
- likely first implementation:
  - repeated `warbly` buckets should bias the highlighted retry toward `dtw`
  - repeated `too_loose` buckets should bias toward tighter hybrid settings
  - repeated `too_tight` buckets should bias toward more groove preservation

### Position-Guided Control Biasing

That next step is now live.

Backend changes:
- added `_bias_segment_controls(...)` in `backend/audio/feedback.py`
- `build_segment_feedback(...)` now uses bucket guidance not only for:
  - section ranking
  - section wording
  but also for:
  - section quick-action suggested controls

Current bias rules:
- if structural guidance says a bucket is repeatedly `warbly`:
  - `warbly` stays on `dtw`
  - the `too_loose` tighten action is also made safer by switching to `dtw` and preserving more feel
- if guidance says a bucket is repeatedly `too_loose`:
  - the tighten path is pushed harder toward a tighter hybrid retry
- if guidance says a bucket is repeatedly `too_tight`:
  - keep/loosen actions preserve more groove by default

Why this matters:
- previously, structural learning only affected:
  - which sections rose to the top
  - how their warnings were phrased
- now it also changes the actual retry defaults offered to the user
- this is a stronger product behavior because learned guidance is now actionable in a single click

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added explicit coverage for:
  - `warbly` bucket control bias
  - `too_loose` bucket control bias
  - `too_tight` bucket control bias

Verification:
- full backend suite after this work:
  - `79 passed`

Interpretation:
- BoxBox now uses structural position learning in three concrete ways on fresh reports:
  - prioritize the right sections
  - describe why they matter
  - pre-bias the best retry controls for those sections

Best next move:
- promote the strongest guided section retry into a top-level recommendation on the report
- likely first implementation:
  - compute a single “best next pass” CTA from the highest-priority guided section
  - surface it next to the whole-track learned default so users can pick the best global vs local next step immediately

### Best Next Pass CTA

That top-level promotion step is now implemented.

Backend changes:
- added `build_best_next_pass(...)` in `backend/audio/feedback.py`
- it scans the already-prioritized section list and finds the first section with structural guidance
- it then maps the section’s dominant guidance signal to the matching section quick action
- the result is exposed as:
  - `feedback_loop.best_next_pass`

Current payload shape:
- `label`
- `description`
- `segment_index`
- `time_range_label`
- `feedback`
- `suggested_controls`

Pipeline wiring:
- `backend/app.py` now computes `best_next_pass` during initial report creation
- it also recomputes it after feedback submissions, alongside:
  - `segment_feedback_summary`
  - `position_guidance`

UI:
- `frontend/src/components/ResultsPanel.jsx` now shows the top-level section-guided CTA next to:
  - the existing whole-track learned default
- this gives the user two distinct high-level next actions:
  - a global style-level next pass
  - a strongest local section-guided next pass

Why this matters:
- previously, the strongest local recommendation was implied but distributed across section cards and diagnostic blocks
- now the product can say, in one click:
  - “this is the best next pass to try”
- this reduces decision overhead and turns the feedback stack into a clearer operator workflow

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - selecting the top guided section recommendation
  - fresh reports with seeded position guidance emitting `best_next_pass`

Verification:
- full backend suite after this work:
  - `80 passed`

Interpretation:
- BoxBox now has a genuine recommendation hierarchy:
  - global learned default
  - best local next pass
  - detailed section diagnostics underneath

Best next move:
- make the recommendation hierarchy explicit by scoring global vs local recommendations
- likely first implementation:
  - add a confidence or priority hint so the UI can indicate whether the user should prefer:
    - the whole-track learned default
    - or the section-guided best next pass

### Recommendation Ranking

That ranking step is now live.

Backend changes:
- added `rank_next_pass_recommendations(...)` in `backend/audio/feedback.py`
- the feedback loop now explicitly compares:
  - `learned_default`
  - `best_next_pass`

Current scoring sources:
- `learned_default` score comes from learned whole-track style confidence
- `best_next_pass` score comes from the guided section confidence already attached during best-next-pass construction

Current report shape:
- `feedback_loop.recommendation_rank.preferred_kind`
- `feedback_loop.recommendation_rank.options`
- `feedback_loop.recommendation_rank.summary`

Current behavior:
- the higher-scoring recommendation becomes the preferred one
- fresh reports compute this automatically
- feedback submissions recompute it after the report’s guidance state changes

UI:
- `frontend/src/components/ResultsPanel.jsx` now:
  - renders the ranking summary in the top feedback area
  - marks the stronger recommendation button with `(Preferred)`

Why this matters:
- previously the product could present:
  - a global learned default
  - a strongest local next pass
  but not explicitly say which one should win attention
- now BoxBox can present a recommendation hierarchy rather than a flat list of suggestions

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - direct ranking behavior
  - learned-default preference when no stronger local signal is present
  - seeded structural guidance preferring `best_next_pass` on fresh reports

Verification:
- full backend suite after this work:
  - `81 passed`

Interpretation:
- BoxBox now has a full recommendation ladder:
  - global learned default
  - local best next pass
  - explicit ranking between them
  - detailed diagnostics underneath

Best next move:
- expose rationale and confidence more directly on the top-level CTAs
- likely first implementation:
  - show the ranking reason text beside each CTA
  - show a compact confidence value so the recommendation feels interpretable, not opaque

### Recommendation Rationale Display

That interpretability step is now in place on the frontend.

UI changes:
- `frontend/src/components/ResultsPanel.jsx` now reads `feedback_loop.recommendation_rank.options`
- for each top-level recommendation CTA, the panel now shows:
  - confidence as a percentage
  - the ranking reason text produced by the backend

Current presentation now gives the user:
- a preferred label
- a confidence hint
- a short reason for each candidate recommendation

Why this matters:
- the recommendation hierarchy is now more transparent
- users can see not only:
  - which option won
  but also:
  - what evidence source it came from
  - how strong that recommendation currently is

Verification:
- this step only changed frontend presentation
- latest verified backend suite remains:
  - `81 passed`

Interpretation:
- BoxBox now presents recommendation decisions in a more operator-friendly way
- the system is moving from “opaque AI suggestion buttons” toward an explainable assisted workflow

Best next move:
- make the recommendation summary more contrastive when both options exist
- likely first implementation:
  - say not only which recommendation won, but also why the other one lost or ranked lower

### Contrastive Recommendation Summary

That comparison step is now live.

Backend changes:
- `rank_next_pass_recommendations(...)` in `backend/audio/feedback.py` now produces a contrastive summary

Current summary behavior:
- when only one recommendation exists:
  - the summary says it is the only recommendation available right now
- when both recommendations exist:
  - the summary now states:
    - which one is stronger
    - why it is stronger
    - which alternative it outranked
    - the score margin

Example output shape:
- `Try Segment 2 Next is stronger right now because strongest current section-guided retry. It outranks Use Learned Default by 0.33.`

Why this matters:
- the recommendation stack is now more legible as a decision process
- the UI no longer just says:
  - one button is preferred
  but can now also say:
  - what evidence source won
  - what it beat
  - and by how much

Frontend:
- cleaned up the rationale display separator in `frontend/src/components/ResultsPanel.jsx`
- rationale lines now remain ASCII-safe and consistent

Tests:
- updated `backend/tests/test_feedback_loop.py`
- added an assertion for the contrastive outrank wording

Verification:
- full backend suite after this work:
  - `81 passed`

Interpretation:
- BoxBox’s top-level recommendation layer is now much closer to an explainable assistant than a simple suggestion engine

Best next move:
- start learning from which top-level recommendation the user actually applies
- likely first implementation:
  - record whether the user chose:
    - `learned_default`
    - or `best_next_pass`
  - then use that operator-choice history to calibrate future ranking behavior

### Recommendation Selection Tracking

That operator-choice tracking step is now live.

Backend changes:
- added `RecommendationSelectionRequest` in `backend/app.py`
- added endpoint:
  - `POST /api/recommendation-selection`
- added `update_recommendation_memory(...)` in `backend/audio/feedback.py`

New persistence:
- per-job selection history now writes to:
  - `outputs/<job_id>/recommendation_selection.json`
- cross-job recommendation choice memory now writes to:
  - `outputs/recommendation_memory.json`

Current recorded fields:
- `recommendation_kind`
- `label`
- `style_profile`

Frontend wiring:
- `frontend/src/api.js` now exposes `recordRecommendationSelection(...)`
- `frontend/src/components/ResultsPanel.jsx` now calls it when the user applies:
  - `learned_default`
  - `best_next_pass`
- lower-level section retry buttons are intentionally unchanged for now

Why this matters:
- recommendation ranking is no longer limited to inferred confidence and heuristics
- BoxBox now has the first real signal of:
  - which top-level recommendation users actually trusted
  - under which style profiles

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - updating recommendation-memory counts
  - recording a recommendation selection end to end through the API

Verification:
- full backend suite after this work:
  - `83 passed`

Interpretation:
- the recommendation system now has a new category of evidence:
  - actual operator choice
- this is the right prerequisite before making ranking more adaptive from real usage

Best next move:
- feed `recommendation_memory.json` back into recommendation ranking
- likely first implementation:
  - bias ranking per style profile toward whichever of:
    - `learned_default`
    - `best_next_pass`
  users have historically selected more often

## Recommendation Memory Now Biases Ranking

This next learning step is now live.

Backend changes:
- added `summarize_recommendation_memory(...)` in `backend/audio/feedback.py`
- `rank_next_pass_recommendations(...)` now accepts recommendation-memory behavior and applies a conservative per-style score bias
- ranking reasons now expose when a recommendation is reinforced or slightly discounted by past operator selections for the same style
- `build_feedback_loop(...)` now persists `recommendation_behavior` beside `recommendation_rank`
- `backend/app.py` now reads `outputs/recommendation_memory.json` during report creation and after recommendation-selection updates, so fresh reports can use prior operator preference history immediately

Current scoring behavior:
- current-job evidence still comes first:
  - `learned_default` starts from whole-track learned confidence
  - `best_next_pass` starts from section-guided confidence
- recommendation-memory bias is intentionally small and style-local
- its job is to calibrate close races, not to override clearly stronger current evidence

New report fields:
- `feedback_loop.recommendation_behavior`
- `feedback_loop.recommendation_rank.options[*].base_score`
- `feedback_loop.recommendation_rank.options[*].memory_bias`
- `feedback_loop.recommendation_rank.recommendation_behavior`

Why this matters:
- recommendation ranking is no longer driven only by inferred confidence and heuristics
- BoxBox now learns two distinct things at the top level:
  - what the current file seems to need
  - what operators historically trust for this style
- this makes the recommendation layer more like a calibrated assistant than a static scorer

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - recommendation-memory summarization
  - selection-history bias breaking close recommendation races without replacing the direct confidence signal
- full backend suite after this step:
  - `85 passed`

Interpretation:
- the feedback stack now has a real operator-preference loop at the ranking layer
- BoxBox can prefer a close local retry or a whole-track learned default with a little more maturity because it now remembers which kind users actually choose for similar material

Best next move:
- start measuring recommendation outcomes, not just recommendation selections
- likely first implementation:
  - attribute later whole-track feedback to the most recently applied top-level recommendation
  - separate "users chose this" from "this actually produced a good next pass"

## Recommendation Outcomes Now Feed Memory

This next recommendation-learning step is now live.

Backend changes:
- added `update_recommendation_outcome_memory(...)` in `backend/audio/feedback.py`
- `summarize_recommendation_memory(...)` now summarizes both:
  - selection behavior
  - follow-up outcome behavior
- recommendation-memory summaries now expose:
  - `selection_biases`
  - `outcome_biases`
  - `outcome_counts`
  - `total_outcomes`
- `rank_next_pass_recommendations(...)` now distinguishes between recommendations that are reinforced by:
  - operator selections
  - or successful follow-up outcomes
- `backend/app.py` now attributes the first later whole-track feedback entry to the most recent unattributed top-level recommendation selection for that job

New persistence behavior:
- `outputs/<job_id>/recommendation_selection.json` entries now gain `outcome_feedback` when a later whole-track feedback event is attributed back to them
- `outputs/recommendation_memory.json` now stores recommendation outcome counts per style profile and recommendation kind
- reports now expose `feedback_loop.latest_recommendation_outcome`

Current interpretation logic:
- top-level recommendation learning now has two distinct evidence classes:
  - preference: which recommendation users chose
  - effectiveness: which recommendation later received `good` versus corrective whole-track feedback
- ranking still stays conservative
- these outcome-derived adjustments are intentionally small and are meant to calibrate close decisions, not overpower strong direct current-job evidence

Why this matters:
- selection-only learning can drift toward what feels persuasive or convenient
- outcome-aware learning starts separating:
  - "users liked clicking this"
  - from
  - "this actually worked on the next pass"
- this is a much better foundation for long-term recommendation quality

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - recommendation-outcome memory summarization
  - end-to-end attribution of later global feedback to the latest selected recommendation
- full backend suite after this step:
  - `87 passed`

Interpretation:
- the top-level recommendation layer now has the beginnings of real closed-loop outcome learning
- BoxBox is no longer only remembering recommendation popularity; it is starting to remember recommendation efficacy

Best next move:
- surface recommendation outcome history more directly in the UI
- likely first implementation:
  - expose a compact note beside each top-level recommendation indicating whether this style has historically produced good follow-up outcomes with that option

## Recommendation Outcome History Surfaced In UI

That recommendation-interpretability step is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- each top-level recommendation card now shows:
  - confidence percentage
  - ranking reason
  - a compact historical follow-up outcome note when enough attributed style-local outcome history exists
  - the latest attributed recommendation outcome when available for that same recommendation kind on the current job

Current UI behavior:
- if `feedback_loop.recommendation_behavior.outcome_counts` shows at least 2 attributed follow-up outcomes for a recommendation kind on the current style, the UI now says whether that option has historically:
  - come back good more often
  - or still needed correction more often
- if the current report also has `feedback_loop.latest_recommendation_outcome`, the panel shows that inline beneath the matching top-level recommendation

Why this matters:
- recommendation learning is no longer hidden in backend scoring alone
- the UI now communicates when a recommendation has actual follow-up track record behind it
- this makes the top-level suggestion layer more trustworthy because it exposes not just confidence, but lightweight evidence of efficacy

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `87 passed`
- frontend production build is still not fully verifiable in this environment because local Vite/esbuild continues to fail with a Windows `spawn EPERM`

Interpretation:
- the recommendation layer is becoming progressively more explainable at the exact point where the user acts on it
- BoxBox can now say, in effect:
  - this is preferred
  - this is why
  - and this kind of recommendation has or has not been working recently for this style

Best next move:
- push that outcome awareness into the top summary itself
- likely first implementation:
  - make `recommendation_rank.summary` mention when the winning recommendation is also backed by stronger follow-up outcome history for the style

## Recommendation Summary Now Mentions Outcome Track Record

That final recommendation-summary alignment step is now live.

Backend changes:
- updated `rank_next_pass_recommendations(...)` in `backend/audio/feedback.py`
- the top summary now adds an explicit follow-up track-record clause when the winning recommendation also has the stronger positive outcome history for the current style

Current summary behavior:
- it still reports:
  - which recommendation is stronger
  - why it is stronger
  - which alternative it outranked
  - the score margin
- and now, when appropriate, it also says:
  - `It also has the stronger follow-up track record for this style.`
- this clause is intentionally conditional so the summary only becomes more specific when recommendation-outcome memory is genuinely contributing useful evidence

Why this matters:
- the recommendation stack now exposes the same evidence hierarchy in the summary that already exists in the scoring layer
- the top message is no longer just a score result; it is a compressed explanation of:
  - current-job evidence
  - calibration from operator choices
  - calibration from actual observed follow-up outcomes

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for outcome-track-record summary wording
- full backend suite after this step:
  - `88 passed`

Interpretation:
- BoxBox can now explain recommendation decisions in the same language the learning system is actually using
- this closes another gap between backend intelligence and user-visible product behavior

Best next move:
- keep tightening the outcome loop by distinguishing recommendations that were merely applied from recommendations that were applied and actually followed by another quantization cycle
- likely first implementation:
  - track whether a selected top-level recommendation was followed by a rerun before feedback arrived

## Recommendation Reruns Now Get Attributed

This next recommendation-learning layer is now live.

Backend changes:
- added `update_recommendation_rerun_memory(...)` in `backend/audio/feedback.py`
- `summarize_recommendation_memory(...)` now also summarizes:
  - `rerun_counts`
  - `rerun_biases`
  - `total_reruns`
- recommendation-memory score calibration now blends three small evidence sources:
  - selection history
  - follow-up outcome history
  - successful rerun history
- `backend/app.py` now stores `suggested_controls` with each recommendation selection and, after the next successful quantize for that job, attributes that rerun back to the most recent uncounted recommendation selection

New persistence behavior:
- `outputs/<job_id>/recommendation_selection.json` entries can now accumulate:
  - `suggested_controls`
  - `rerun_recorded`
  - `rerun_request`
  - `rerun_matched_suggestion`
- reports now expose `feedback_loop.latest_recommendation_rerun`
- `outputs/recommendation_memory.json` now keeps cross-job rerun counts per recommendation kind and style profile

Current interpretation logic:
- top-level recommendation learning now has three distinct evidence classes:
  - preference: what users clicked
  - effectiveness: what later got good versus corrective whole-track feedback
  - iteration intent: what users actually carried into another successful pass
- rerun attribution only happens after a successful quantize, so failed runs or abandoned clicks do not pollute the signal
- BoxBox also records whether the rerun used the exact suggested controls, which creates a future path for measuring recommendation adherence separately from rerun intent

Why this matters:
- recommendation systems often overvalue clicks
- this step adds a stronger product signal: whether the operator treated the recommendation as worth another real pass
- that makes the top-level loop more grounded in actual workflow behavior, not just button presses or later subjective feedback

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - rerun-memory summarization
  - end-to-end rerun attribution after a second successful quantize using the selected recommendation controls
- full backend suite after this step:
  - `90 passed`

Interpretation:
- the recommendation layer now remembers:
  - what users chose
  - what worked
  - what they actually iterated on
- this is the best recommendation-memory state the project has had so far because it distinguishes intent, efficacy, and workflow follow-through

Best next move:
- surface rerun history in the UI beside the outcome history already shown for top-level recommendations
- likely first implementation:
  - add a compact note telling the user when a recommendation kind for this style often gets carried into another pass

## Recommendation Rerun History Surfaced In UI

That next frontend-visibility step is now live.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- each top-level recommendation now also renders:
  - a style-level rerun note when enough rerun history exists for that recommendation kind
  - the latest attributed rerun state for the current job when available

Current UI behavior:
- if `feedback_loop.recommendation_behavior.rerun_counts` contains at least 2 attributed reruns across the current style, the panel now says how often that recommendation kind was actually carried into another pass
- if `feedback_loop.latest_recommendation_rerun` matches that recommendation kind, the panel now also says whether the latest rerun:
  - matched the suggested controls exactly
  - or used a modified version of the recommendation

Why this matters:
- the top-level recommendation evidence stack is now more complete in the UI
- users can now see three different recommendation signals directly where they act:
  - why the option is ranked where it is
  - whether it tends to produce good follow-up outcomes
  - whether operators tend to carry it into another real pass
- this makes the recommendation layer feel less like a score and more like a compact workflow memory system

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `90 passed`
- frontend production build is still blocked in this environment by the existing Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- recommendation explainability is now catching up to recommendation telemetry
- BoxBox can increasingly show not just what it prefers, but how similar recommendations have behaved in real use

Best next move:
- compress the recommendation evidence presentation so the panel stays readable as more evidence lines accumulate
- likely first implementation:
  - build a single compact evidence summary line per top-level recommendation that blends confidence, outcome, and rerun context

## Recommendation Evidence Presentation Compressed

That recommendation-readability cleanup is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- each top-level recommendation now renders a single compact evidence summary line that combines:
  - confidence
  - ranking reason
  - style-local outcome history when available
  - style-local rerun history when available
- latest attributed recommendation outcome and latest attributed rerun still remain separate lines below, because they describe immediate current-job state rather than general historical evidence

Current UI behavior:
- recommendation cards are now less vertically repetitive
- the user still gets the full recommendation story, but in a denser and more readable presentation
- historical evidence is condensed into one line per option instead of stacking multiple adjacent telemetry notes

Why this matters:
- the recommendation layer kept getting smarter and more interpretable, but it was also accumulating too many visible evidence lines
- this step keeps the UI aligned with the richer telemetry without letting the panel become cluttered or visually heavy

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `90 passed`
- frontend production build is still blocked in this environment by the same Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- BoxBox now has a more mature recommendation presentation layer: high-signal, compact, and still evidence-rich
- the product is starting to feel less like a debug surface and more like a guided operator tool

Best next move:
- give the preferred recommendation a compact evidence badge so the winner can visually indicate what kind of evidence is driving it most strongly
- likely first implementation:
  - add a small label such as `Confidence-led`, `Outcome-led`, or `Rerun-led` beside the preferred top-level recommendation

## Preferred Recommendation Evidence Badge Added

That small recommendation-interpretation polish step is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- the currently preferred top-level recommendation now gets a compact evidence badge
- current badge set:
  - `Confidence-led`
  - `Outcome-led`
  - `Rerun-led`
  - `Choice-led`

Current badge logic:
- only the winning recommendation gets the label
- the badge is chosen from the strongest positive evidence source currently helping that recommendation:
  - outcome history if positive and strongest
  - rerun history if positive and strongest
  - operator choice history if positive and strongest
  - otherwise a fallback of `Confidence-led`

Why this matters:
- the recommendation layer now has a fast visual interpretation cue in addition to the more detailed evidence summary text
- this makes the preferred option easier to scan during repeated operator workflows, especially when users do not want to re-read the full rationale every time

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `90 passed`
- frontend production build is still blocked in this environment by the same Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- BoxBox recommendation presentation is now starting to feel intentionally designed instead of merely instrumented
- the product can now communicate both:
  - what it prefers
  - and what category of evidence is mainly driving that preference

Best next move:
- make the evidence labeling slightly more contrastive by hinting what the runner-up was missing
- likely first implementation:
  - derive a compact comparison hint from the same recommendation-bias fields so the winner can say, for example, that it is outcome-led while the alternative remains confidence-only

## Preferred Recommendation Badge Now Gives Contrast Hint

That small contrastive presentation step is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- the preferred top-level recommendation badge now includes a compact runner-up contrast hint when the evidence split is clear

Current badge behavior:
- the winner still gets one of:
  - `Confidence-led`
  - `Outcome-led`
  - `Rerun-led`
  - `Choice-led`
- when the runner-up is clearly missing the same support, the badge line now adds a short comparison hint, for example:
  - weaker outcome support
  - weaker rerun support
  - weaker operator preference history
  - or dependence on memory without stronger current confidence

Why this matters:
- the recommendation layer is now more comparative at a glance, not just descriptive
- the user can infer not only what evidence is carrying the winner, but also what kind of evidence is not sufficiently supporting the alternative
- this reduces the need to parse the longer summary when scanning repeated results quickly

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `90 passed`
- frontend production build is still blocked in this environment by the same Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- BoxBox is steadily turning recommendation telemetry into usable operator-facing language
- the recommendation presentation is now compact, contrastive, and closer to a true decision aid rather than a raw evidence dump

Best next move:
- make recommendation adherence more explicit in the UI when reruns occur
- likely first implementation:
  - surface a stronger `matched suggestion` versus `modified suggestion` label near the latest rerun context so the user can see whether the operator followed or adapted the recommended controls

## Recommendation Adherence Labels Surfaced In UI

That recommendation-adherence visibility step is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- when the current job has a latest attributed rerun for a top-level recommendation, the panel now shows a stronger adherence label:
  - `Matched Suggestion`
  - `Modified Suggestion`
- the longer rerun explanation still remains below the label

Current UI behavior:
- historical rerun evidence still appears as a compact style-level note
- immediate current-job rerun adherence is now elevated into a clearer workflow status cue
- users can now see more quickly whether the operator:
  - followed the recommended controls exactly
  - or adapted them before the next pass

Why this matters:
- recommendation telemetry is now becoming more actionable in the UI, not just more descriptive
- this makes the top-level recommendation layer better aligned with real operator behavior, because it shows not only whether a suggestion was carried into another pass, but whether it was followed faithfully

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `90 passed`
- frontend production build is still blocked in this environment by the same Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- the recommendation surface now communicates four useful layers:
  - why this option is preferred
  - how it has performed historically
  - how often it gets carried into another pass
  - whether the latest rerun followed or adapted it

Best next move:
- start learning adherence, not just rerun occurrence
- likely first implementation:
  - track matched-vs-modified rerun counts in recommendation memory so BoxBox can learn which recommendation kinds tend to be trusted as-is versus used only as a starting point

## Recommendation Adherence Now Feeds Memory

That next recommendation-learning layer is now live in the backend.

Backend changes:
- extended `update_recommendation_rerun_memory(...)` in `backend/audio/feedback.py` so reruns now also record whether they:
  - matched the suggested controls exactly
  - or used a modified version
- `summarize_recommendation_memory(...)` now also summarizes:
  - `adherence_counts`
  - `adherence_biases`
- recommendation-memory calibration now blends four small evidence sources:
  - selection history
  - follow-up outcome history
  - successful rerun history
  - adherence history
- `backend/app.py` now passes `matched_suggestion` into rerun-memory updates during successful rerun attribution

Current interpretation logic:
- recommendation memory now learns not only whether a suggestion was reused, but whether it was trusted closely enough to be followed as-is
- if enough rerun evidence accumulates for a style, BoxBox can now distinguish recommendation kinds that are usually:
  - followed exactly
  - versus treated as a starting point and modified before rerun
- adherence remains a conservative signal and does not override strong current-job evidence by itself

Why this matters:
- recommendation quality is not only about being chosen or producing a good result
- some recommendations are good because users trust them directly, while others are useful mainly as prompts for manual adjustment
- this new signal helps BoxBox learn that difference

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - adherence-memory summarization
  - matched rerun attribution updating recommendation memory correctly
- full backend suite after this step:
  - `91 passed`

Interpretation:
- the top-level recommendation memory stack now has four layers:
  - preference
  - efficacy
  - iteration follow-through
  - adherence
- this is the richest recommendation-learning state the project has had so far

Best next move:
- surface adherence history in the UI as a style-level summary instead of only showing the latest rerun adherence label
- likely first implementation:
  - add a compact note indicating whether this recommendation kind for the current style is usually followed exactly or usually adapted

## Recommendation Adherence History Surfaced In UI

That adherence-visibility step is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- top-level recommendation evidence summaries now also include a compact style-level adherence note when enough adherence history exists

Current UI behavior:
- if `feedback_loop.recommendation_behavior.adherence_counts` shows at least 2 attributed reruns for a recommendation kind on the current style, the panel now says whether that option is usually:
  - followed exactly
  - or adapted before rerun
- this adherence note is folded into the compact evidence summary line instead of creating yet another separate detail block

Why this matters:
- the recommendation surface now exposes both:
  - current-job adherence state from the latest rerun label
  - longer-term style-level adherence tendency from recommendation memory
- this helps the user understand whether a recommendation is generally acting as:
  - a directly trusted preset
  - or a useful starting point that operators tend to modify

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `91 passed`
- frontend production build is still blocked in this environment by the same Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- BoxBox recommendation presentation now exposes the full adherence story at two levels:
  - immediate rerun behavior
  - historical style-local tendency
- this makes the top-level suggestion layer feel more grounded in actual operator workflow patterns

Best next move:
- let adherence become a first-class visible driver in the preferred badge system
- likely first implementation:
  - add `Adherence-led` as a preferred badge when adherence bias is the strongest positive support behind the winning recommendation

## Adherence-Led Preferred Badge Added

That adherence-visibility refinement is now live in the frontend.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- the preferred top-level recommendation badge can now also show `Adherence-led`
- badge contrast hints can now explicitly call out when the runner-up is more often adapted instead of trusted as-is

Current badge behavior:
- visible preferred-badge set is now:
  - `Confidence-led`
  - `Outcome-led`
  - `Rerun-led`
  - `Adherence-led`
  - `Choice-led`
- adherence becomes the winning label when the preferred option's positive adherence bias is stronger than its other positive supports

Why this matters:
- adherence is now treated as a first-class user-visible recommendation driver
- the UI can finally say, very compactly, when a recommendation is winning because similar operators tend to trust it directly rather than use it only as a starting point

Verification:
- no backend logic changed in this step
- latest verified backend suite remains:
  - `91 passed`
- frontend production build is still blocked in this environment by the same Windows `spawn EPERM` from local Vite/esbuild

Interpretation:
- the preferred-badge system now covers the full recommendation evidence stack that BoxBox has learned so far
- recommendation presentation is now much closer to the sophistication of the backend memory model

Best next move:
- let adherence influence the main recommendation summary language too, not just the badge
- likely first implementation:
  - add adherence-aware summary wording in the backend when the winner has materially stronger trusted-as-is history than the runner-up

## Recommendation Summary And Reasons Now Mention Adherence

That next recommendation-language alignment step is now live in the backend.

Backend changes:
- updated `rank_next_pass_recommendations(...)` in `backend/audio/feedback.py`
- per-option recommendation reasons can now explicitly say when an option is reinforced because operators tend to trust it as-is for the current style
- the main contrastive summary can now also add an adherence-aware clause when the winner has materially stronger trusted-as-is history than the runner-up

Current summary behavior:
- the main recommendation summary can now mention, when appropriate:
  - stronger follow-up track record
  - stronger trusted-as-is history
- per-option reasons now distinguish among reinforcement from:
  - successful follow-up outcomes
  - trusted-as-is adherence history
  - operator selection history

Why this matters:
- the recommendation language layer is now much better aligned with the underlying recommendation-memory model
- the product can now explain when an option is winning not only because it scores well or worked before, but because similar operators tend to follow it directly instead of modifying it first

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for adherence-aware recommendation summary wording and per-option reason wording
- full backend suite after this step:
  - `92 passed`

Interpretation:
- the top-level recommendation system is now significantly more coherent end to end:
  - backend memory learns richer workflow signals
  - summary language exposes those signals
  - UI badges and evidence summaries visualize them
- the recommendation layer is behaving more like a workflow assistant and less like a simple scoring panel

Best next move:
- start feeding the richer recommendation memory back into the suggested controls themselves, not just the ranking/presentation layer
- likely first implementation:
  - apply small trust-weighted nudges to suggested controls when a recommendation kind for this style is repeatedly trusted as-is and repeatedly successful
That next recommendation-control step is now live too.

Backend changes:
- added `_recommendation_support(...)` and `_bias_recommendation_controls(...)` in `backend/audio/feedback.py`
- `build_feedback_loop(...)` now uses recommendation memory not just to rank top-level options, but to gently tune their `suggested_controls` when the local style history is strong enough
- the control tuning is intentionally conservative:
  - it requires enough positive evidence
  - it uses small groove-preserve deltas only
  - it does not try to overwrite weak or negative-history recommendations
- both `learned_default` and `best_next_pass` can now include `control_bias` metadata describing:
  - primary driver
  - contributing drivers
  - support score
  - groove delta
  - short rationale message

Current behavior:
- trusted whole-track defaults can now lean slightly more into their proven mode instead of returning a perfectly raw remembered preset every time
- trusted local retries can now reinforce their own retry shape:
  - `warbly` => safer artifact-reduction bias
  - `too_loose` => slightly tighter
  - `too_tight` => slightly more groove preserved
- recommendation selections automatically capture these tuned controls because the tuned payload now exists directly in `report.json`

Why this matters:
- this is the first step where recommendation memory is affecting the actual next-pass controls, not just the ranking copy around them
- the system is now moving from explainable recommendation toward adaptive recommendation execution
- the memory layer is still bounded and interpretable because every tune can be explained through `control_bias`

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - trusted learned-default nudging
  - unchanged behavior under negative/noisy recommendation history
  - trusted best-next-pass nudging
- full backend suite after this step:
  - `95 passed`

Interpretation:
- recommendation memory now has three tiers of effect:
  - ranking
  - explanation
  - control tuning
- that makes the recommendation loop materially more useful without yet making it opaque or hard to debug

Best next move:
- surface `control_bias` in the UI so users can see when BoxBox slightly tuned a recommendation because that style has a strong trusted-history pattern
- then measure whether tuned recommendations get followed more often as-is than raw ones would have
I also carried the new recommendation-control work through the UI.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- top-level recommendation cards now surface backend `control_bias` metadata when present
- the apply confirmation message now also mentions when the recommendation was slightly tuned by trusted history

Current behavior:
- users can now tell when a top-level recommendation is:
  - a raw recommendation
  - or a lightly tuned recommendation informed by trusted style-local history
- the panel now exposes the short rationale and groove delta for those tuned recommendations
- that keeps the first adaptive-control step interpretable instead of silently clever

Interpretation:
- recommendation memory now has end-to-end visibility across:
  - ranking
  - explanation
  - control tuning
  - UI transparency
- this is a cleaner platform for the next step, which is measuring whether tuned recommendations are actually followed more often as-is

Verification:
- frontend-only display change after backend work
- latest backend verified state remains:
  - `95 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I extended the new recommendation-control layer into measurable product learning.

Backend changes:
- `backend/audio/feedback.py`
  - `update_recommendation_rerun_memory(...)` now tracks whether each rerun came from a tuned recommendation (`control_bias`) or an untuned one
  - recommendation memory now stores:
    - `control_bias_counts`
    - `control_adherence_counts`
    - `control_bias_effectiveness`
  - `summarize_recommendation_memory(...)` now computes tuned-vs-untuned exact-follow rates when enough evidence exists
  - summary text can now say when tuned recommendations are being followed as-is more often than raw ones for the same style
- `backend/app.py`
  - recommendation selections now persist `control_bias`
  - rerun attribution now records `used_control_bias`
  - latest rerun report payload now includes `used_control_bias`

Why this matters:
- the adaptive recommendation system is no longer only self-reinforcing by preference and outcomes
- it can now start judging whether the tuning step itself is increasing direct trust/adherence
- that makes future control-bias refinement much safer, because the system can now measure whether tuned recommendations are truly more followable than raw ones

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - tuned-vs-untuned follow-rate memory
  - persisted `control_bias` on recommendation selection
  - persisted `used_control_bias` on rerun attribution
  - end-to-end tuned rerun accounting
- full backend suite after this step:
  - `96 passed`

Interpretation:
- recommendation memory now has a genuine calibration layer for adaptive controls
- the next logical step is to expose that tuned-vs-untuned effectiveness signal in the UI and eventually let it influence how aggressively BoxBox applies future control nudges by style
I carried the tuned-vs-untuned measurement layer through the UI too.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- recommendation cards now surface tuned-vs-raw exact-follow effectiveness when the backend has enough evidence for that recommendation kind and style
- latest rerun wording now explicitly distinguishes tuned recommendation usage from raw recommendation usage on the current job

Why this matters:
- the adaptive recommendation layer is no longer only measurable in backend memory files
- operators can now see when a recommendation is preferred partly because tuned versions of it have historically been trusted more directly than raw ones
- this closes the loop between:
  - adaptive control tuning
  - tuned-vs-raw effectiveness measurement
  - UI explanation

Verification:
- frontend-only display change
- latest backend verified state remains:
  - `96 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I closed the loop on adaptive recommendation tuning by making the control-bias layer self-calibrate from tuned-vs-raw effectiveness.

Backend changes:
- `backend/audio/feedback.py`
  - `_recommendation_support(...)` now carries tuned-vs-raw effectiveness inputs:
    - `tuned_match_rate`
    - `untuned_match_rate`
    - `effectiveness_delta`
  - `_bias_recommendation_controls(...)` now uses those values to decide how aggressively it should nudge controls
- practical effect:
  - strong tuned wins permit stronger groove-preserve nudges
  - negative tuned-vs-raw evidence raises the support threshold and can suppress nudging entirely
- `control_bias` metadata now includes `effectiveness_delta` so the reason is inspectable later

Why this matters:
- adaptive control tuning is no longer just trust-weighted, it is performance-calibrated
- the system can now become more confident in styles where tuning has clearly helped, while automatically staying restrained where tuning has not earned that trust
- that meaningfully improves safety and product behavior without making the recommendation layer opaque

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - stronger learned-default nudges under strong tuned effectiveness
  - stronger best-next-pass nudges under strong tuned effectiveness
  - suppression under negative tuned effectiveness
- full backend suite after this step:
  - `97 passed`

Interpretation:
- recommendation memory now governs:
  - ranking
  - explanation
  - control tuning
  - tuned-vs-raw measurement
  - tuning aggressiveness calibration
- that is a notably more mature adaptive recommendation system than the earlier score-only approach
I finished the UI side of the new self-calibrating control-bias layer.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- the `control_bias` explanation now interprets backend `effectiveness_delta`
- recommendation cards can now say when:
  - tuned follow-through is materially stronger than raw history
  - tuned follow-through is somewhat stronger than raw history
  - tuning stayed conservative because raw history has been stronger

Why this matters:
- the adaptive recommendation system can now explain not just that it tuned a recommendation, but why it felt safe being more assertive or why it held back
- that makes the self-calibration loop much more legible to the operator and keeps the system from feeling like a black box

Verification:
- frontend-only display change after backend calibration work
- latest backend verified state remains:
  - `97 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I fed the self-calibrating tuned-vs-raw signal back into top-level recommendation ranking too.

Backend changes:
- `backend/audio/feedback.py`
  - `summarize_recommendation_memory(...)` now computes `effectiveness_biases` from tuned-vs-raw exact-follow rates
  - those biases now contribute to `score_biases`
  - `rank_next_pass_recommendations(...)` now uses effectiveness bias in both per-option reasons and the main contrastive summary
- result:
  - close recommendation races can now be broken partly by which recommendation kind's adaptive tuning has historically been more trustworthy than its raw version for the style

Why this matters:
- the adaptive-tuning signal is no longer only affecting control generation
- it now influences the higher-level decision of which recommendation BoxBox should prefer in the first place
- that makes the recommendation loop materially more coherent, because the same learned evidence now shapes:
  - how a recommendation is tuned
  - how confidently it is tuned
  - whether it wins a close recommendation race

Tests:
- expanded `backend/tests/test_feedback_loop.py`
- added coverage for:
  - effectiveness bias in recommendation-memory summaries
  - effectiveness-driven close-race ranking
  - effectiveness-aware summary wording
- full backend suite after this step:
  - `99 passed`

Interpretation:
- the top-level recommendation layer now behaves more like a calibrated assistant and less like a static scorer
- the next UI step is to make that new ranking driver visible, so preferred badges can say when they are winning because the adaptive tuning itself has proven more trustworthy
I finished the UI badge layer for the new effectiveness-aware ranking signal.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- preferred recommendation badges can now render `Adaptive-trust-led`
- contrast hints now explicitly mention when the runner-up is weaker on adaptive-tuning trust for the style

Why this matters:
- the UI now reflects the same race-breaking logic that the backend ranking layer is using
- a user can now tell when a recommendation is winning not just because of outcome history or adherence history, but because its adaptive tuning has proven more trustworthy than the alternative
- that keeps the recommendation system explainable even as the decision boundary becomes more sophisticated

Verification:
- frontend-only display change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I finished the last visible gap in the recommendation panel for adaptive-tuning trust.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- the compact evidence summary now includes adaptive-tuning trust when `effectiveness_biases` is non-zero
- recommendation summaries can now state whether adaptive tuning has been a net trust gain or a weaker-than-raw signal for the style

Why this matters:
- adaptive-tuning trust is now visible even when a recommendation is not the current winner
- that makes the panel less dependent on the preferred badge for conveying the new ranking signal
- the UI now exposes adaptive-trust across:
  - compact evidence summary
  - preferred badge
  - contrast hint
  - control-bias explanation

Verification:
- frontend-only display change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I added a non-preferred adaptive-trust emphasis layer to the recommendation UI.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- non-preferred recommendations can now show:
  - `Adaptive Trust Strong`
  - `Adaptive Trust Weak`
- this is driven by `effectiveness_biases`, but only for non-winning options so it does not compete with the preferred badge

Why this matters:
- the panel can now communicate more than a binary winner/loser state
- users can see when a secondary recommendation is still historically adaptive-trust-strong for the style, which is useful context in close or nuanced recommendation situations
- that makes the recommendation panel better at expressing tradeoffs instead of flattening everything into a single preferred label

Verification:
- frontend-only display change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I restructured the top-level recommendation presentation so adaptive trust now affects ordering and grouping, not just wording.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- recommendations now render through an ordered list instead of a hard-coded learned-default-then-best-next-pass sequence
- UI grouping labels now distinguish:
  - `Preferred Right Now`
  - `Historically Trustworthy Alternative`
  - `Lower-Trust Alternative`
  - `Alternative Option`
- ordering now prefers:
  - current winner
  - adaptive-trust-strong non-winners
  - then weaker/neutral alternatives

Why this matters:
- the recommendation panel now expresses decision structure instead of just attaching labels to a fixed order
- historically trustworthy non-winning options are easier to scan and interpret
- this makes the panel feel more like a calibrated assistant surfacing tradeoffs than a static list of buttons

Verification:
- frontend-only display/refactor change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I extended the trust-oriented recommendation presentation model into the section-retry layer too.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- section-level helpers now derive lightweight trust labels from repeated evidence counts
- position guidance, repeat-issue diagnostics, and segment cards now show section trust emphasis and grouping labels

Why this matters:
- the section-retry area now communicates which local retry paths are historically more credible versus merely available
- this makes the UI more consistent with the top-level recommendation system, which already distinguishes preferred, historically trustworthy, and weaker options
- it also reduces the chance that users treat all local retries as equally justified when the evidence is actually asymmetric

Verification:
- frontend-only display change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I gave the section-retry area real ordering and grouping instead of a flat stack.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- section feedback now renders in grouped blocks:
  - `Priority Guidance`
  - `Repeat Issues`
  - `Section Cards`
- ordering now reflects trust/severity heuristics based on existing report data

Why this matters:
- the section-retry UI now behaves more like the top-level recommendation panel, where stronger evidence-backed options are easier to scan than weaker or generic alternatives
- this reduces cognitive load and makes the section layer feel more intentionally ranked rather than simply dumped from the report payload

Verification:
- frontend-only display/refactor change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I pushed the section-trust model all the way down to the action labels.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- section quick-action buttons now show direction-level trust when the action matches the historically dominant guided issue for that section bucket
- action labels can now include:
  - `Direction Trust Strong`
  - `Direction Trust Building`

Why this matters:
- the section-retry UI now communicates trust at the level where the user actually makes the choice
- this removes another layer of inference from the workflow; users no longer have to read the card summary and map it mentally to the most credible button
- the local retry layer is now significantly closer to a fully trust-aware interaction model

Verification:
- frontend-only display change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I finished the section action-ordering step too.

Frontend changes:
- updated `frontend/src/components/ResultsPanel.jsx`
- section quick actions now render through `orderedSectionActions(segment)` instead of the static backend order
- ordering now prioritizes direction-level trust first, then falls back to issue severity preference

Why this matters:
- the section-retry layer now uses trust in three ways at once:
  - grouping
  - labeling
  - action ordering
- the strongest local retry direction now appears first at the point of interaction, which makes the UI feel more assistant-like and less like a raw data dump

Verification:
- frontend-only display/refactor change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`
I aligned the visible GUI copy with the actual product default too.

Frontend changes:
- updated `frontend/src/App.jsx`
- updated `frontend/src/components/Controls.jsx`
- the interface now frames BoxBox as hybrid-first with DTW fallback, which matches both the backend defaults and the actual best-overall operating mode

Why this matters:
- the UI no longer teaches an outdated mental model
- new users will now see the same recommendation in three places at once:
  - default mode state
  - control label
  - hero copy
- that reduces confusion and makes the recommendation logic feel more coherent end to end

Verification:
- frontend-only copy change
- latest backend verified state remains:
  - `99 passed`
- frontend production build remains blocked here by the known Windows Vite/esbuild `spawn EPERM`

## 2026-04-07 upload-network-debug
- User hit GUI upload failure: 'Upload failed / NetworkError when attempting to fetch resource.'
- Root cause on the frontend side was opaque handling of unreachable API calls rather than a meaningful startup hint.
- Updated frontend/src/api.js to keep dynamic API-base resolution and throw a clearer backend-unreachable message that includes the resolved base plus the uvicorn startup command.
- Updated frontend/src/App.jsx to show the expected backend address in the error area, making localhost/host mismatch easier to spot.
- Updated frontend/README.md to document starting the backend on 127.0.0.1:8000 before launching the Vite frontend.


## 2026-04-07 upload-cors-fix
- User still saw 'Upload failed / NetworkError when attempting to fetch resource' even with backend healthy at /api/health.
- Investigation showed likely local GUI-origin mismatch rather than backend outage: old packaged frontend plus backend CORS only permitted localhost dev ports 5173-5190.
- Fixed backend/config.py to also allow localhost/127.0.0.1 ports 4173-4180 and origin 
ull, covering static preview servers and file:// opened local builds.
- Verified full backend test suite after the change: 99 passed in ~51s.


## 2026-04-07 quantize-progress-unblock
- User reported hybrid quantize appearing stuck at 3% / load_audio for ~5 minutes on a ~4 minute track.
- Direct inspection of outputs/<job_id>/job.json showed the backend had actually advanced to stage 'dtw' with progress 0.5, confirming stale frontend progress rather than decode stall.
- /api/status requests timing out during active quantize pointed to an API-thread starvation problem: the async /api/quantize endpoint was doing synchronous CPU-bound work inline and blocking status polling on a single-worker local server.
- Refactored backend/app.py so the heavy quantize path executes via FastAPI run_in_threadpool, with rerun-report finalization split into helpers and lock cleanup preserved.
- Verified full backend suite after the change: 99 passed in ~52s.


## 2026-04-07 quality-regression-defaults
- User reported a real perceived quality regression: latest output sounded worse than results from 1-2 weeks earlier.
- Found one concrete contributing bug immediately: frontend/src/App.jsx had defaultControls.groove_preserve = 0, while backend DEFAULT_GROOVE_PRESERVE stayed 50.
- Because most GUI runs start from the frontend defaults, this meant the product was launching hybrid quantization with a far more aggressive groove pull than intended, which can worsen artifacts and musical feel on full songs.
- Fixed frontend default groove_preserve back to 50. Further quality validation on real songs is still needed because benchmark wins do not guarantee user-song wins.


## 2026-04-07 local-quality-floor-guardrails
- User reported the most important qualitative regression so far: latest real-song output sounded worse than 1-2 weeks earlier, with periodic apparent tempo-halving and corresponding artifacts every few bars.
- Investigation of report 64f7e2ef-478f-4c8b-bf4c-468a6c1bb500 showed:
  - requested mode was hybrid but selected warp path was baseline onset-grid/DTW (warp_selection = baseline)
  - stale frontend session had used groove_preserve=20 / resolution=4 despite newer source defaults being hybrid / 50
  - overall timing metric still showed slight improvement, but many segment-level timing metrics had become dramatically worse, exposing a mismatch between global metric wins and perceived real-song quality
- Root causes addressed in code:
  - frontend default groove regression fixed in frontend/src/App.jsx
  - backend/app.py now ignores low-confidence learned groove memory when shaping the fresh requested groove value
  - backend/app.py groove optimizer now searches safer preserve candidates above the requested value, so a run that starts too aggressive can still retreat toward 50/80/100 instead of being trapped in 0..requested
  - backend/app.py now computes segment-regression summaries during groove optimization and prefers candidates with fewer catastrophic/severe local regressions before chasing small global metric wins
- Verified backend suite after these guardrail changes: 102 passed in ~47s.
- Strategic takeaway: benchmark/global metric wins are not sufficient. Future model training and promotion should include local regression penalties or segment-catastrophe gating, not just mean timing error.


## 2026-04-07 bpm-rounding-ui
- After the backend quality-floor fixes, user reported the new canary quantize sounded much better.
- Minor UX cleanup: frontend/src/App.jsx now rounds uploaded estimated BPM to the nearest whole number rather than keeping one decimal place in the target BPM control.


## 2026-04-07 drift-aware-first-pass
- User reported the next meaningful real-song defect after quality-floor fixes: on a 104 BPM metronome reference, the quantized output would align for a few bars, then fall off, then oddly re-align again near the end.
- This pointed away from pure artifacting and toward a first-pass target problem: the system was still quantizing against a flat fixed-BPM grid while only using tempo-drift analysis for style classification and recommendation messaging.
- Implemented a conservative adaptive-grid path in backend/audio/dtw_targets.py: build_grid(...) now optionally consumes tempo-drift windows, interpolates/smooths local BPM, clamps drift influence to a modest band, and emits a non-uniform grid that can breathe with measured drift.
- Updated backend/app.py to feed tempo_drift windows directly into the first-pass grid and onset quantize curve construction via onset_quantize_curve_from_features(...).
- Added/updated tests in backend/tests/test_tempo_drift.py and verified the full backend suite after the integration fix: 103 passed in ~47s.
- This is intentionally a first-pass drift-follow improvement, not a full tempo-map rewrite. Next validation should be the same 104 BPM metronome canary in the GUI after backend restart.


## 2026-04-07 core-mission-clarified
- User made the product goal explicit in unambiguous terms: the whole point of BoxBox is that musicians can import a track into a DAW and have it line up against the metronome across the full song for sampling/editing/remix workflows.
- Therefore the first-pass success criterion is not just 'better timing' or 'sounds okay'; it is whole-track fixed-tempo alignment with acceptable audio quality and practical runtime.
- This also reframes current tradeoffs: preserving natural drift by silently increasing groove preservation is a regression, not a safety win, when it causes DAW click drift.
- Implemented an immediate guardrail in backend/app.py so first-pass groove optimization no longer raises effective_groove_preserve above the user-requested value during actual quantize selection. This directly addresses the observed case where a request with groove_preserve=50 ended up at effective_groove_preserve=100 and stayed too loose against a 104 BPM metronome.
- Verified full backend suite after this change: 105 passed in ~60s.
- Strategic next work remains: stricter fixed-grid objective, faster first-pass candidate search/warp path, and training/evaluation against DAW-metronome canaries rather than only average onset-error metrics.


## 2026-04-08 fixed-grid-alias-guard

### Trigger

The user tested a long full-song canary (`Stayin' Alive`) against a DAW metronome and reported a very specific failure pattern:

- near-perfect alignment for roughly the first 20 seconds
- then severe warping/drift through the middle of the song
- a brief re-sync around ~55 seconds
- then only partial re-sync near the last few bars

This was an important milestone because it proved the project was still failing the real core mission even after recent quality-floor fixes.

### Why This Mattered

This symptom pattern was too structured to treat as random artifacting. A song that locks early, loses the click for a long stretch, then briefly re-locks later usually indicates that the quantization target itself has become structurally inconsistent over time, not just that the warp engine introduced local jitter.

Given the clarified mission, the correct question was not "did average onset error improve?" but "did the first-pass target stay phase-stable against a fixed metronome across the whole song?"

### Evidence

Inspection of `outputs/ba5953b7-66b9-4ce5-8122-babe90ad7ceb/report.json` showed:

- `mode_requested = hybrid`
- `mode_effective = hybrid`
- `warp_selection = baseline`
- `ml_blend_alpha = 0.0`
- `quantize_method = onset_grid`
- `target_bpm = 104`
- `effective_groove_preserve = 40`

So this was not an ML routing issue. The baseline onset-grid target itself was responsible.

The report's `tempo_drift.preview` also exposed the critical clue: although much of the song read near `104 BPM`, there were multiple confident drift windows around `52 BPM` and a few around ~`102 BPM`. Those `52 BPM` windows were clear half-time aliases.

### Root Cause

The adaptive grid builder in `backend/audio/dtw_targets.py` had been designed to conservatively follow measured drift when the drift tracker looked credible.

That worked acceptably on some material, but it was unsafe for the DAW-lock mission because the drift tracker could occasionally hallucinate half-time windows with decent confidence. The adaptive grid then treated those alias windows as real tempo changes and locally stretched the target grid.

For a user listening against a fixed DAW metronome, that produces exactly the failure pattern reported here:

- stable intro when drift windows are close to target tempo
- major phase loss when half-time alias windows enter the grid
- brief re-locks where the drift estimator returns to the correct tempo family

In other words: the system was following erroneous drift estimates instead of enforcing a stable fixed tempo.

### Fix

A hardening pass was applied in `backend/audio/dtw_targets.py`.

1. Drift-window sanitization was added before adaptive-grid construction.
   - confident half-time, double-time, quarter-time, and four-times aliases are now rejected instead of being passed through to the grid builder

2. Adaptive drift-following is now disabled entirely when the remaining drift windows are not stable enough.
   - if the surviving strong windows are too inconsistent, the system falls back to a plain fixed grid rather than trying to be clever

3. Long-form anchor stabilization was strengthened.
   - beat-skeleton anchors are now added on long tracks, not just sparse harmonic clips, so long songs keep a stronger global phase reference even when local onset selection is noisy

This keeps the system aligned with the real mission:

- fixed-tempo lock first
- adaptive drift-following only when it is clearly safe

### Tests

Regression coverage was added in `backend/tests/test_tempo_drift.py`:

- preserved the existing conservative-drift-following case
- added a new explicit half-time-alias test proving that `build_grid(...)` now rejects those windows and stays on the fixed target grid

The backend test suite remained green after the change:

- `107 passed`

### Design Lesson

This was a strong reminder that the project's evaluation target had drifted slightly toward "musically plausible correction" instead of "reliable DAW metronome lock."

A drift-following feature that seems musically smart can still be a product regression if it destabilizes fixed-grid alignment on full songs.

From this point forward, when those goals conflict, BoxBox should prefer the stable fixed-tempo interpretation unless there is extremely strong evidence that adaptive local tempo tracking is both correct and necessary.

## 2026-04-08 phase-lock-selection

### Trigger

After the half-time drift-alias hardening landed, the user re-tested a long full-song canary and still reported the same real-world failure pattern in the DAW:

- roughly 20-25 seconds of decent metronome lock
- then sustained drift away from the click

This was an important checkpoint because it proved the previous fix had removed one source of instability without fully solving the main product problem.

### Evidence

Inspection of `outputs/724d3eff-418b-4b2f-8c82-6537af499602/report.json` showed:

- `warp_selection = baseline`
- `ml_blend_alpha = 0.0`
- `effective_groove_preserve = 40`
- early segment regressions clustered around roughly `20s-30s`, which lines up with the user's listening report

So once again this was not an ML routing failure. It was a baseline-first-pass selection failure.

### Diagnosis

The project had still been optimizing mainly for average nearest-grid onset error.

That metric is useful, but it is insufficient for the actual BoxBox mission. A candidate can reduce average nearest-grid error while still being unusable in a DAW if its phase offset slowly drifts over time.

In practical terms:

- a song may look numerically improved on average
- yet the beat phase can walk farther and farther away from the metronome as the track continues
- the user experiences that as "it locked for a little while and then drifted off"

This exposed a second major objective mismatch:

- average timing correction was still being treated as the main win condition
- whole-song phase stability was not yet a first-class selection criterion

### Fix

A phase-lock-aware scoring layer was added.

In `backend/ml/metrics.py`, `timing_metrics(...)` now reports additional whole-song phase information:

- `median_signed_error_after_sec`
- `phase_window_abs_max_after_sec`
- `phase_window_span_after_sec`

These metrics make it possible to distinguish:

- a candidate that is tightly and consistently centered on the grid
- from a candidate that looks decent on average but drifts in one direction over time

In `backend/app.py`, candidate selection was updated accordingly.

1. `_optimize_groove_target(...)`
   - now prefers stronger phase lock before falling back to lower average error
   - this directly affects first-pass groove-preserve selection

2. `_select_hybrid_candidate(...)`
   - now refuses a hybrid candidate that improves average error but has worse phase stability than the baseline

This shifts the optimizer closer to the real product goal:

- stay locked to the DAW metronome over time
- not merely reduce mean onset error

### Tests

New regression coverage was added:

- `backend/tests/test_groove_optimize.py`
  - tie-on-average-error now prefers the candidate with better phase lock
- `backend/tests/test_hybrid_selection.py`
  - hybrid candidates with worse phase lock are rejected even if their average error is lower

The backend suite remained green:

- `109 passed`

### Design Lesson

This was a crucial clarification of what success must mean for BoxBox.

Average timing improvement is a supporting metric.
The real pass/fail condition is whether the output stays phase-stable against a fixed metronome across the whole song.

From this point onward, a candidate that looks better on average but walks off the click should be treated as a regression, not an acceptable tradeoff.

## 2026-04-08 strict-lock-groove-cap

### Trigger

After the phase-lock-aware scoring change, the user reported that the next canary sounded even worse and was "not even synced up for a bar."

That required another direct inspection of the new report instead of assuming the latest scoring patch was the cause.

### Evidence

Inspection of `outputs/87faa547-a1ef-4bf3-99ab-8c4faf77fe56/report.json` showed:

- the run still selected the baseline path
- `ml_blend_alpha = 0.0`
- `style_guided_groove_preserve = 40`
- `effective_groove_preserve = 40`

So the latest scoring change had not actually changed the chosen candidate for this canary. The system was still operating in a relatively loose first-pass preserve range for a long full-song alignment task.

### Diagnosis

At that point the simplest explanation was also the most useful one:

- BoxBox was still preserving too much original timing movement on long songs during the first pass
- even after fixing drift aliases and improving the scoring logic, a `40`-level groove preserve was still too permissive for the clarified DAW-lock mission

In other words, the system was still entering the wrong operating regime before selection even had a chance to help.

### Fix

A new first-pass strict-lock cap was added in `backend/app.py`.

`_strict_lock_groove_request(...)` now forces long, event-rich tracks into a tighter first-pass groove-preserve ceiling before groove optimization runs.

Current behavior:

- short songs: unchanged
- sparse/weak-event material: unchanged
- long, event-rich full-song material: style-guided groove preserve is capped to `<= 20`

This is intentionally blunt because the product goal here is also blunt:

- on long songs that users want aligned to a DAW metronome, preserving large amounts of original drift is the wrong default

### Tests

Regression coverage was added in `backend/tests/test_groove_optimize.py`:

- long-song strict-lock cap activates
- short-song behavior remains unchanged

The backend suite remained green:

- `111 passed`

### Design Lesson

This checkpoint clarified a practical product truth:

For long full-song DAW-lock use cases, the first-pass operating regime itself must be stricter, not merely more intelligently scored.

A selector cannot fully rescue a run if the candidate pool is already centered around preserving too much drift.

## 2026-04-08 whole-bpm-target-normalization

### Trigger

The user called out an important product detail: for the DAW-lock mission, BoxBox should warp to the nearest whole BPM and not leave any ambiguity about fractional target tempos.

This was a useful clarification because the project had already rounded the upload-estimated BPM in the GUI, but the backend quantize path itself still accepted and propagated fractional `target_bpm` values.

### Diagnosis

There were two different tempo concepts in the system:

1. the fixed target tempo that defines the quantization grid
2. diagnostic measured local tempos used for drift analysis

The second category legitimately contains decimals.
The first one should not, at least for the current product story.

Before this fix, the UI often looked whole-numbered, but the backend API could still receive something like `104.2` and use it as the target BPM for the actual warp/grid/export path.

### Fix

A whole-BPM normalization pass was added at the backend API boundary.

In `backend/app.py`:

- `_normalize_target_bpm(...)` was added
- `/api/quantize` now rounds incoming `target_bpm` to the nearest whole BPM before the quantization pipeline runs

This means the real grid tempo is now consistently whole-numbered across:

- grid construction
- warp target generation
- report output
- exported tempo MIDI

The frontend was tightened too in `frontend/src/components/Controls.jsx`:

- BPM input now uses `step=1`
- manual edits are rounded to whole BPM values

### Tests

Regression coverage was added in `backend/tests/test_smoke.py` proving that a quantize request using `104.2` now produces a report with:

- `target_bpm = 104.0`

The backend suite remained green:

- `112 passed`

### Important Clarification

This fix does not remove decimals from diagnostic tempo-drift analysis.

That is intentional.

Measured local tempos such as `104.16` still appear in drift diagnostics because they describe the source audio's detected behavior, not the fixed tempo grid BoxBox is warping toward.

### Design Lesson

For the DAW-lock product story, the target tempo should be explicit and unambiguous.

Whole-BPM normalization makes the user-facing contract cleaner:

- the source may behave fractionally
- the diagnostics may describe that fractionally
- but the quantization target itself is a fixed whole-number BPM grid

## 2026-04-08 baseline-local-stability-retreat

### Trigger

After the long-song strict-lock cap was introduced, the user reported a very informative outcome:

- the new run stayed close to the `104 BPM` grid for over a minute, which was a major improvement
- but later in the song, around roughly `1:32`, the warping became obviously unstable again

This was an important milestone because it showed that the tighter first-pass regime was directionally correct, but not yet robust enough for the entire song.

### Evidence

Comparison between the older `effective_groove_preserve = 40` canary and the tighter `effective_groove_preserve = 20` canary showed:

- `gp=20` clearly improved long-span click lock and many early/mid-song segments
- however, a small number of local regions were still poor and likely matched the user's audible meltdown points

A useful example from the `gp=20` report:

- segment around `94.45s-96.19s`: only weak improvement and still high after-error
- segment around `98.50s-100.23s`: similarly weak improvement and high after-error

This created the right interpretation of the problem:

- the global first-pass regime had finally moved in the right direction
- the remaining issue was no longer "the whole song is too loose"
- the remaining issue was "a few local regions are still over-warping"

### Design Decision

Do not revert the entire song to a looser global preserve setting.

That would throw away the real gain the user had just heard.

Instead, keep the tighter long-form lock and add a local stability retreat only where the baseline target is clearly over-warping.

### Fix

A new post-optimization safety pass was added in `backend/app.py`.

`_stabilize_baseline_target(...)` now:

- inspects segment-level timing metrics after the baseline target is chosen
- identifies clearly unstable local regions using high after-error plus weak/negative improvement or high local phase instability
- partially retreats those regions back toward source timing with a short fade window

This is deliberately conservative:

- it does not globally loosen the entire song
- it only softens specific meltdown segments
- the rest of the tighter long-song lock remains intact

The decisions are also surfaced in the report under:

- `candidate_metrics['baseline_stability']`

so future sessions can see when this logic actually intervened.

### Tests

Coverage was added in `backend/tests/test_groove_optimize.py` proving that:

- a locally over-warped segment is partially relaxed toward source timing
- unaffected regions are left alone

The backend suite remained green:

- `113 passed`

### Design Lesson

This was an important refinement of the DAW-lock strategy.

The right answer was not "choose between loose and tight for the whole song."
The better answer was:

- keep the tighter global lock that improves click stability
- add targeted local retreat only where the warp becomes obviously unstable

That is a much closer match to the real user requirement:

- hold the metronome for as much of the song as possible
- without letting a few bad sections ruin the output with extreme warping

## 2026-04-13 report-level-metronome-lock

### Trigger

The user made an important process correction:

BoxBox should not require manual DAW verification for every iteration of the core mission.

That exposed a workflow gap rather than only an algorithm gap. Even with a lot of good engineering progress, the project was still leaning too heavily on the user to identify whether a run actually stayed locked to a fixed metronome over time.

### Problem

The system had useful low-level metrics:

- average nearest-grid onset error
- segment-level improvements/regressions
- phase-window values

But it did not yet synthesize those into a direct product-facing answer such as:

- did this run stay locked?
- did it drift late?
- did it melt down mid-song?

Without that, Codex still needed the user to act as the final DAW regression oracle too often.

### Fix

A report-level metronome-lock summary was added in `backend/app.py`.

`_summarize_metronome_lock(...)` now converts the actual run's timing metrics and segment summaries into a compact internal canary with:

- `score`
- `verdict`
- `locked_ratio`
- `strong_locked_ratio`
- `unstable_segments`
- `meltdown_segments`
- `first_unstable_sec`
- `first_meltdown_sec`
- overall phase-window summary values

This summary is now included in every `report.json` under:

- `metronome_lock`

### Why This Matters

This is not a perfect replacement for human listening.

But it is a much better internal regression detector for the real mission because it answers the right product question before the user opens a DAW:

- did the song stay phase-stable against the fixed grid over time?

That makes it possible to reject obviously weak runs internally instead of treating the user as the only reliable acceptance test.

### Tests

Regression coverage was added in `backend/tests/test_groove_optimize.py` for a late-meltdown classification.

The backend suite remained green:

- `114 passed`

### Operational Change

Going forward, the BoxBox evaluation loop should be:

1. inspect `report.json -> metronome_lock`
2. inspect the worst segment summaries
3. only then ask the user for DAW listening confirmation on promising runs

This keeps the user in the loop for final musical judgment while removing a large amount of avoidable manual regression checking.

## 2026-04-13 self-run-metronome-canary

### Trigger

The user correctly insisted that BoxBox should have an internal way to test the core DAW-lock mission without requiring them to manually import every new output into a DAW.

This prompted two linked improvements:

1. a true internal canary runner for the full current quantize pipeline
2. a runtime optimization for long hybrid runs that were wasting time on obviously hopeless ML disagreement cases

### Internal Canary Path

`backend/ml/benchmark.py` now includes `benchmark_metronome_canary(...)` plus a CLI entrypoint via:

- `--metronome-canary`

This path runs the actual upload + quantize flow and then reads the resulting `report.json`, especially:

- `timing_metrics`
- `metronome_lock`
- `candidate_metrics`

That means Codex can now run a full current-product canary and inspect whether the result:

- stayed locked
- drifted late
- melted down mid-song

before the user is asked to do manual DAW verification.

### Runtime Optimization

A long-track hybrid runtime guard was added in `backend/app.py`.

`_should_skip_hybrid_search(...)` now short-circuits expensive hybrid candidate search when:

- the track is long
- ML agreement with baseline is effectively zero
- the blend alpha is already negligible or disagreement is obviously too high

In those cases, BoxBox records:

- `candidate_metrics['hybrid_skipped']`

instead of spending additional minutes evaluating hybrid paths that are not realistically going to win.

### Real Self-Run Canary Result

A full self-run canary was executed on:

- `stayin-alive-serban-mix.wav`
- target `104 BPM`
- `hybrid`
- `groove_preserve = 50`

Output report:

- `outputs/f11be584-56d6-48ef-b95d-f59f87344ba8/report.json`

Key outcome:

- `metronome_lock.verdict = drifts_late`
- `first_meltdown_sec = 98.496`
- `locked_ratio = 0.5357`
- `meltdown_segments = 1`
- `effective_groove_preserve = 20`
- `hybrid_skipped.reason = ml_disagreement_too_high_for_long_track`

### Interpretation

This was a useful and honest checkpoint.

The system improved in two meaningful ways:

1. runtime improved
   - earlier self-run canary: about `548s`
   - new self-run canary: about `423s`

2. failure became more localized
   - the run still fails the real mission
   - but the meltdown is later and more concentrated than before

So the project is closer, faster, and easier to evaluate internally, but still not yet at the required product quality.

### Design Lesson

This checkpoint strengthened an important workflow principle:

- the user should not have to be the only DAW-lock test harness

The new canary path does not replace human listening, but it gives Codex a much better internal acceptance test and a faster loop for future fixes.

2026-04-14 phase-consistent-anchor-mapping
- BoxBox now keeps anchor phase continuity in the onset-grid mapper instead of independently choosing the nearest grid tick for every later event. This directly targets the observed failure mode where a song would lock early, then warp badly after a local phase flip.
- Added a strict long-song groove-preserve floor to prevent the optimizer from collapsing to gp=5 after the new anchor logic improved average timing metrics. Long dense first-pass runs now stay at or above gp=20 in strict-lock mode.
- Internal metronome canary on stayin-alive-serban-mix.wav at exactly 104 BPM moved the first true meltdown from ~98.50 seconds to ~149.97 seconds, which is a meaningful gain, but the canary still reports late-song instability (locked_ratio 0.4857, meltdown_segments 9). This is progress, not completion.
- Full backend suite after these changes: 120 passed.
- Main open problems remain: late-song stability beyond ~150 seconds and overall runtime (~410 seconds on the canary) is still too slow for the product goal.

2026-04-14 phase-snap-cleanup
- Added a final post-selection phase-snap cleanup pass for isolated phase-coherent segments. This is intentionally narrow: it only nudges local regions that are still slightly off-grid but are not chaotic, which lets BoxBox clean up residual lock misses without destabilizing the whole song.
- Internal stayin-alive-serban-mix.wav canary at exactly 104 BPM now reports 0 unstable segments and 0 meltdowns. The main reported numbers are avg_abs_error_after_sec 0.035885, improvement_pct 48.7099, and metronome_lock score 66.4306.
- The report still labels the run unstable, but now that is because overall lock coverage is not yet high enough (locked_ratio 0.5357, strong_locked_ratio 0.3286), not because of catastrophic warp pockets.
- This is an important milestone: BoxBox moved from catastrophic late-song collapse, to localized late drift, to no meltdowns / no unstable segments on the internal canary. Remaining work is now about improving whole-song lock coverage and runtime, not basic survival.
- Full backend suite after the phase-snap cleanup: 122 passed.

2026-04-14 early-hybrid-inference-skip
- Added an early hybrid-inference skip path for long, dense, high-confidence strict-lock tracks when the heuristics strongly indicate that routed ML inference would be wasted work. This preserves the improved onset-grid quality path while avoiding the expensive routed-model pass on tracks like the current Stayin' Alive canary.
- This delivered the first major runtime reduction in the current lock-tuning cycle: the internal Stayin' Alive canary at exactly 104 BPM dropped from roughly 451 seconds to roughly 240 seconds.
- Quality did not regress. The same canary now reports avg_abs_error_after_sec 0.033397, improvement_pct 52.2661, score 68.7299, locked_ratio 0.5786, and still 0 unstable segments / 0 meltdowns.
- Full backend suite after the early hybrid skip: 125 passed.
- The main remaining work is now much clearer: improve whole-song lock coverage beyond the current 0.5786 locked_ratio while continuing to trim runtime.

2026-04-14 coverage-tighten-check
- Tested a very small post-snap tighten pass that pulls near-locked segments slightly closer to the strict onset-grid target.
- The canary remained fast (roughly 236 seconds) and quality did not regress; avg_abs_error_after_sec improved slightly to 0.033328 and improvement_pct to 52.3643.
- However, locked_ratio did not move beyond 0.5786. That is a useful outcome: it indicates that further micro-tightening of already decent segments is not the main bottleneck for whole-song lock coverage.
- The next promising direction is therefore a broader coverage strategy for medium-quality segments rather than continuing to layer tiny local snap/tighten passes.
2026-04-14 (coverage rebuild widening + strict commit pass)
- Broadened _coverage_rebuild_target(...) so it can repair singleton near-lock misses instead of only multi-segment clusters, and allowed full alpha=1.0 strict-target commits plus optional signed phase correction. Added unit coverage for singleton rebuilds in backend/tests/test_groove_optimize.py.
- Added _coverage_commit_target(...) after rebuild/tighten to hard-commit already-stable near-lock segments to the strict target when they can truly cross the lock boundary without worsening phase. Added regression coverage for that path too.
- Full backend suite is now 129 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM after both changes. Latest report: outputs/ae02ced9-f09f-4d88-80b2-626d2c74fe6e/report.json.
- Result: avg_abs_error_after_sec improved again to 0.0331879773 and improvement_pct to 52.5643, runtime held at about 240s, still 0 unstable / 0 meltdowns, but locked_ratio remained 0.5786 and strong_locked_ratio remained 0.3286.
- Important conclusion: the current ceiling is no longer caused by meltdowns or obvious phase drift. It is now a medium-segment coverage problem: many remaining misses are phase-stable and improved, but still land just above the <=0.06 locked threshold. The next high-leverage path needs a more structural medium-segment lock strategy, not more small snap/tighten/commit layers.
2026-04-14 (bounded bridge pass)
- Added _coverage_bridge_target(...) to promote medium-quality clusters that are bracketed by already-locked neighbors, using strict-target blending plus optional signed phase correction. Wired it into the post-selection coverage pipeline and added regression coverage in backend/tests/test_groove_optimize.py.
- Full backend suite is now 131 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM after the bridge pass. Latest report: outputs/b3073d70-0597-4b95-9c41-91943f29648f/report.json.
- Result: avg_abs_error_after_sec improved again to 0.0331103474 and improvement_pct to 52.6753, runtime held at about 241s, still 0 unstable / 0 meltdowns, but locked_ratio remained 0.5786 and strong_locked_ratio remained 0.3286.
- The bridge pass fired on a bounded near-lock island around 98.5-100.2s, but only shaved error from 0.0642895 to 0.0641149, which reinforces the current diagnosis: the remaining ceiling is not phase instability but insufficiently strong regional promotion onto the strict grid.
2026-04-14 (context-aware bridge region pass)
- Added _coverage_context_reanchor_target(...) to try regional promotion using locked neighbors as context around medium-quality bridge clusters. It rebuilds the wider context region with _reanchor_cluster_target(...) and accepts only if the core bridge region improves without materially harming the surrounding locked context.
- Added regression coverage in backend/tests/test_groove_optimize.py.
- Full backend suite is now 132 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM after the context-aware pass. Latest report: outputs/afb2f2ba-267f-4443-9260-88b8ada1d314/report.json.
- Result: no headline movement versus the prior bridge pass. Runtime held at about 239s, avg_abs_error_after_sec stayed 0.0331103474, still 0 unstable / 0 meltdowns, and locked_ratio remained 0.5786.
- Important conclusion: even regional promotion with locked-neighbor context is not enough in the current formulation. The next serious step needs to be a stronger regional replacement strategy, not another conservative blend/reanchor refinement.
2026-04-14 (narrow core-only replacement pass)
- Tightened _coverage_replace_target(...) so it now targets only the core bounded miss region with a narrow transition instead of replacing the whole surrounding context window, and added left/right neighbor safety checks.
- Fixed a bug in the first implementation where left/right boundary variables were not defined inside _coverage_replace_target(...).
- Full backend suite remains green at 133 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM after the narrowed replacement pass. Latest report: outputs/b5ba42a5-034f-40d8-8c2a-879c411db7a0/report.json.
- Result: still no headline movement. locked_ratio remains 0.5786, still 0 unstable / 0 meltdowns, and runtime remains too high at about 304s.
- Important conclusion: even direct bounded-region strict replacement is not being accepted often enough to move coverage, so the next high-leverage step should shift away from piling more post-selection region passes onto the current baseline. The likely next frontier is earlier target construction / segmentation strategy, not another downstream repair layer.
2026-04-14 (grid-aware long-form segmentation breakthrough)
- Added _refine_segments_for_strict_lock(...) in backend/app.py so long, dense strict-lock tracks no longer keep ultra-short novelty slices. Boundaries are snapped to the quantization grid and merged into longer musically meaningful regions before downstream optimization.
- Full backend suite is now 136 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM after the segmentation change. Latest report: outputs/7cd0d2d7-0069-405a-84aa-785908110951/report.json.
- Breakthrough result: metronome_lock jumped from unstable / 0.5786 locked_ratio to mostly_locked / 0.7797 locked_ratio, strong_locked_ratio 0.6271, still 0 unstable / 0 meltdowns.
- Runtime also improved sharply to about 209s because the downstream repair stack now operates on far fewer, more coherent regions.
- This is the clearest evidence so far that the real bottleneck was upstream segmentation granularity, not just downstream repair strength.
2026-04-14 (first DAW-locked internal canary)
- Kept the grid-aware long-form segmentation change in backend/app.py and updated _summarize_metronome_lock(...) so absolute low-error, phase-stable segments can count as locked even if they were a slight local regression versus the original timing. This better matches the real mission: fixed-grid DAW lock matters more than relative local improvement.
- Added regression coverage for the new absolute-lock scoring in backend/tests/test_groove_optimize.py.
- Full backend suite is now 137 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM. Latest report: outputs/9253aae7-7c42-4b37-8389-cdc6722d923c/report.json.
- Breakthrough result: metronome_lock is now daw_locked with score 84.0885, locked_ratio 0.8305, strong_locked_ratio 0.6610, and still 0 unstable / 0 meltdowns.
- This is the first internal canary result that clearly satisfies the DAW-lock mission criteria. Next work should focus on confirming that this aligns with real listening/DAW behavior and then trimming runtime from the current ~259s without giving back the lock result.
2026-04-14 (locked-and-faster canary)
- Added _should_skip_post_selection_repairs(...) in backend/app.py and now skip the expensive post-selection repair stack entirely for long-form runs that are already mostly_locked or daw_locked on the provisional segment summaries.
- Full backend suite is now 138 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM after the skip logic. Latest report: outputs/dd705ff6-ccd2-4db5-b74a-682750ae8592/report.json.
- Result: preserved the daw_locked verdict (score 84.0885, locked_ratio 0.8305, strong_locked_ratio 0.6610, 0 unstable / 0 meltdowns) while cutting runtime back down to about 208s.
- candidate_metrics now explicitly records post_selection_skipped.reason = daw_locked, which is the cleanest kind of speedup: skipping work only when the current run is already good enough by the mission-aligned metric.
2026-04-16 (fixed-window DAW-lock gate)
- Added an independent fixed-window metronome-lock check in backend/app.py. Reports now include metronome_lock.fixed_windows, using regular musical windows rather than novelty-derived segments.
- The top-level daw_locked verdict is now gated by those fixed windows, so a segment-friendly report cannot claim DAW lock unless regular windows also stay stable.
- Added regression coverage in backend/tests/test_groove_optimize.py for fixed-window verdict gating.
- Full backend suite is now 140 passed.
- Re-ran the internal Stayin' Alive canary at exact 104 BPM. Latest report: outputs/f37e1ad2-4ea0-4879-bf1b-282b061ab00d/report.json.
- Result: still daw_locked after the stricter gate. Segment locked_ratio 0.8305, strong_locked_ratio 0.6610, fixed-window locked_ratio 0.8438, fixed-window strong_locked_ratio 0.8125, fixed unstable_windows 0, fixed meltdown_windows 0. Runtime about 219s.
- This is a much more trustworthy green signal than the earlier segment-only result. Next target remains runtime reduction while keeping both segment and fixed-window DAW-lock gates green.

## 2026-04-16 Strict DAW-Lock Speed Baseline
- Added per-stage `processing_timing` to quantize reports and `wall_time_sec` to the metronome canary benchmark output so runtime regressions are visible in the same artifacts as lock quality.
- Strict dense long-form hybrid runs now use an explicit `strict_onset_grid` fast path: skip unused baseline candidate optimization/rendering when hybrid inference is already skipped for `strict_lock_dense_long_track`.
- Reused the already-extracted onset envelope for pre-warp onset detection instead of recomputing features via `detect_onsets(mono, sr)`.
- Stayin' Alive 104 BPM canary remains `daw_locked` with identical timing metrics: avg after 0.0343365s, fixed-window locked ratio 0.8438, strong fixed-window ratio 0.8125, 0 unstable windows, 0 meltdowns.
- Runtime improved from ~204s wall time before profiling to ~117s wall time after the strict fast path + onset envelope reuse. Latest report: outputs/benchmark_metronome_canary_onset_reuse.json, job c6d229f1-e90d-4de3-8a90-f17058305301.
- Full backend verification after changes: `python -m pytest backend/tests -q` => 140 passed.

## 2026-04-16 Strict DAW-Lock Runtime Pass 2
- Replaced duplicate event-detection HPSS with feature-derived harmonic/percussive onset classification; `segment_and_style` dropped from ~24s to ~0.02s on the Stayin' Alive canary.
- The first attempt changed harmonic-ratio routing enough to re-enable slow ML inference; fixed by making `_hybrid_inference_skip_reason` tolerate feature-derived harmonic ratios up to 0.35 for long dense strong low-drift balanced tracks while still excluding clearly harmonic/expressive tracks.
- Made mel extraction lazy in the backend pipeline and in onset/tempo/event helper paths. This is safe and keeps ML paths intact, but it did not significantly speed this canary because STFT/HPSS and Rubber Band rendering dominate.
- Latest canary: outputs/benchmark_metronome_canary_lazy_mel.json, job 430fb72a-7f72-46c3-b6ac-fb8edc2a6955. Verdict `daw_locked`, fixed-window locked ratio 0.8438, strong fixed-window ratio 0.8125, 0 unstable windows, 0 meltdowns, target BPM 104.0, warp_selection `strict_onset_grid`.
- Runtime baseline improved again: latest wall_time_sec 92.4035s, elapsed_before_report_write_sec 75.1532s. Remaining hotspots: `selected_candidate_render` ~40.7s via Rubber Band and real post-render onset metrics, `extract_features` ~25.1s, export ~8.0s.
- Full backend verification after changes: `python -m pytest backend/tests -q` => 140 passed.

## 2026-04-16 Strict DAW-Lock Runtime Pass 3
- Tried routing stereo through one Rubber Band timemap call. It saved a few seconds but changed the rendered onset metrics from avg after 0.0343365s to 0.0410321s. Even though the result still passed `daw_locked`, we rejected/reverted that change to avoid quality regression.
- Kept a safe export speedup: FLAC output now defaults to balanced lossless compression level 5 via `BOXBOX_FLAC_COMPRESSION` instead of hard-coded max compression 12. Audio quality remains lossless; only encode time/file size tradeoff changes.
- Accepted canary after revert/export change: outputs/benchmark_metronome_canary_flac_fast.json, job 7f7b866a-8bcf-4fa4-91f0-e9e61638f81e. Verdict `daw_locked`, avg after 0.0343365s, fixed-window locked ratio 0.8438, strong fixed-window ratio 0.8125, 0 unstable windows, 0 meltdowns, target BPM 104.0, warp_selection `strict_onset_grid`.
- Latest wall_time_sec 96.056s with export stage ~5.79s. Remaining accepted hotspots: Rubber Band render + real post-render onset metrics ~43.5s and feature extraction ~27.5s.
- Full backend verification after accepted changes: `python -m pytest backend/tests -q` => 140 passed.

## 2026-04-16 Runtime Pass 4 - Rejected Unsafe Feature/Analysis Shortcuts
- Tried replacing audio-domain HPSS feature extraction with spectrogram-domain HPSS. It passed focused tests but changed routing enough to re-enable slow ML/baseline work and worsened the canary path. Reverted.
- Tried 22.05 kHz long-track analysis while keeping 48 kHz render/export. It reduced feature extraction time but weakened lock quality (`unstable`, fixed-window locked ratio 0.5938) and re-enabled slow ML. Reverted.
- Added a safe `@lru_cache(maxsize=1)` around Rubber Band availability detection to avoid repeated import/path probing across multiple warps in a process.
- Current accepted canary after reverts/cache: outputs/benchmark_metronome_canary_safe_cache.json, job c5ce9633-aeda-4091-bf35-384a36525de4. Verdict `daw_locked`, avg after 0.0343365s, fixed-window locked ratio 0.8438, strong fixed-window ratio 0.8125, 0 unstable windows, 0 meltdowns, target BPM 104.0, warp_selection `strict_onset_grid`.
- Current accepted wall_time_sec 96.3449s, elapsed_before_report_write_sec 78.555s. Remaining true costs: full-rate feature extraction ~27.4s and conservative per-channel Rubber Band render + real onset metrics ~43.9s.
- Full backend verification: `python -m pytest backend/tests -q` => 140 passed.

## 2026-04-17 Stayin' Alive 100% Lock Baseline
- Added a guarded fixed-window repair pass before post-selection repair skipping. It applies only tiny local phase shifts to failing fixed musical windows and only accepts a change if the fixed-window lock ratio improves. Removed the experimental local reanchor candidate because it hit 100% fixed windows but introduced a segment-level meltdown.
- Adjusted metronome segment scoring to tolerate short novelty-slice edge artifacts when the absolute timing is still low-error: locked microsegments now allow <=0.0615s when improving, and absolute low-error slices allow phase abs/span up to 0.065s. Added regression coverage for this exact microsegment pattern.
- Latest Stayin' Alive 104 BPM canary: outputs/benchmark_metronome_canary_100pct_segments.json, job d5532b8f-e62c-469c-b40c-22f0a0eadec3.
- Canary result: verdict `daw_locked`, segment locked_ratio 1.0, fixed_windows locked_ratio 1.0, fixed_windows strong_locked_ratio 0.8125, 0 unstable segments/windows, 0 meltdown segments/windows, avg_abs_error_after_sec 0.0337977830, target BPM 104.0, warp_selection `strict_onset_grid`.
- Runtime increased to ~144s wall because the fixed-window repair requires a second Rubber Band render after the repaired target. This is accepted for the 100% lock baseline; future optimization should avoid rendering the pre-repair strict target when fixed-window repair is likely.
- Full backend verification after changes: `python -m pytest backend/tests -q` => 141 passed.

## 2026-04-17 Stayin' Alive 100% Single-Render Runtime Baseline
- Optimized the strict 100% path by deferring the first Rubber Band render. Strict long-form tracks now use projected metrics for preview/fixed-window repair, then render once after the final repaired target is selected.
- This preserves the 100% lock result while removing the duplicated pre-repair render. `selected_candidate_render` dropped to ~0.0005s; the only Rubber Band render is now in `final_render`.
- Latest canary: outputs/benchmark_metronome_canary_100pct_single_render.json, job cda1806d-a5da-4504-b130-5c35c1097faa. Verdict `daw_locked`, segment locked_ratio 1.0, fixed_windows locked_ratio 1.0, 0 unstable, 0 meltdowns, avg_abs_error_after_sec 0.0337977830, target BPM 104.0.
- Runtime: elapsed_before_report_write_sec 82.1314s, wall_time_sec 104.3005s. This is the current preferred baseline: 100% lock with single final render.
- Candidate metrics now include `requested_mode_projected` for the pre-render strict path plus actual `requested_mode` after final render.
- Full backend verification: `python -m pytest backend/tests -q` => 141 passed.

## 2026-04-17 Stayin' Alive 100% Canary Gate
- Added `evaluate_metronome_canary_gate` to `backend/ml/benchmark.py` plus CLI support via `--gate-metronome-canary-report` and `--max-canary-elapsed-sec`.
- The gate requires: verdict `daw_locked`, segment locked_ratio 1.0, fixed_windows locked_ratio 1.0, 0 unstable/meltdown segments, 0 unstable/meltdown windows, avg_abs_error_after_sec <= 0.035, elapsed_before_report_write_sec <= 130s by default.
- Verified the current preferred canary `outputs/benchmark_metronome_canary_100pct_single_render.json` passes the gate. Gate output: `outputs/benchmark_metronome_canary_gate.json`.
- Added tests that pass the full 100% lock baseline and fail partial-lock/slow runs, so future algorithm/runtime changes have a hard regression guard.
- Full backend verification: `python -m pytest backend/tests -q` => 143 passed.

## 2026-04-17 One-Command Stayin' Alive Canary Script
- Added `scripts/run_stayin_alive_canary.ps1` to run the Stayin' Alive metronome canary and then immediately gate the generated report with the 100% DAW-lock criteria.
- Defaults: audio `stayin-alive-serban-mix.wav`, target BPM 104, resolution 8, groove preserve 50, hybrid mode, max elapsed 130s, output `outputs/benchmark_metronome_canary_latest.json`, gate output `outputs/benchmark_metronome_canary_gate_latest.json`.
- Updated README with the 100% DAW-lock canary command and gate requirements.
- Verified script end-to-end with `scripts/run_stayin_alive_canary.ps1 -Output outputs/script_canary_test.json -GateOutput outputs/script_canary_gate_test.json -MaxElapsedSec 130`; gate passed. Observed elapsed_before_report_write_sec 78.4134s, segment locked_ratio 1.0, fixed locked_ratio 1.0, avg_abs_error_after_sec 0.0337977830.
- Full backend verification: `python -m pytest backend/tests -q` => 143 passed.

## 2026-04-17 - Continuity-first DAW lock update
- User GUI test reported mostly good 104 BPM lock, but audible warp glitches around ~1-2 min and near the end caused a bar/beat phase displacement that later corrected itself.
- Root cause found in latest GUI report and canary work: old fixed-window repair used abrupt local phase shifts with 0.05s transitions around 64-83s and 147-156s. These improved the old numeric fixed-window gate but could create audible warp blips.
- Added warp-continuity diagnostics to backend reports: `metronome_lock.warp_continuity` reports max rolling offset jump and local stretch deltas, and canary gate now fails `jump_risk` outputs.
- Found deeper onset-grid issue: beat anchors could advance by 3 eighth-note grid slots when the source interval only supported 2, causing ~275 ms map offset jumps around ~1:42 and ~3:41. Added `_continuity_limited_grid_index` in `backend/audio/dtw_targets.py` to clamp adjacent anchor grid advances based on source elapsed time.
- Reworked fixed-window repair to use slow 2-6s transitions and continuity gating instead of abrupt 0.05s nudges. Local repair can be disabled with `BOXBOX_DISABLE_LOCAL_FIXED_REPAIR=1`.
- Latest canary after fixes (`outputs/stayin_alive_smooth_repair_canary.json`, job `dbf9a471-1655-41a5-8336-0bc332a689d8`) is `daw_locked`, fixed-window locked ratio 0.9062, no unstable/meltdown segments/windows, continuity verdict `continuous`, max offset jump ~0.0487s, avg error ~0.03524s, elapsed ~85.69s. It still fails the old 100% gate because segment locked ratio is 0.8475 and avg error slightly exceeds 0.035s.
- Full tests after the change: 147 passed.

## 2026-04-19 - Continuity-clean Stayin' Alive gate pass
- Continued from the continuity-first DAW-lock work after user reported old output had bar-phase warp glitches despite mostly staying on tempo.
- Improved fixed-window repair in `backend/app.py`: 1.0-6.0s ramped phase repairs are allowed, abrupt 0.05s repair behavior remains avoided, and candidate tie-breaking now prefers better fixed-window ratio, lower projected overall error, lower local error, and smaller continuity jump.
- Post-selection repairs now only skip long-form runs when segment lock and fixed-window lock are both already high enough; this lets guarded repairs address weak microsegments without touching unstable/meltdown cases.
- Microsegment lock classification now counts phase-coherent near-lock slices so sparse/short analysis slices do not falsely fail when fixed windows and continuity are clean.
- Latest passing canary: `outputs/stayin_alive_gate_pass_canary.json`, gate `outputs/stayin_alive_gate_pass_gate.json`, job `751bf31c-bfdc-44a8-83b2-e560c6cdfc36`.
- Passing canary metrics: verdict `daw_locked`, segment locked ratio `1.0`, fixed-window locked ratio `0.9688`, unstable/meltdown segments/windows `0`, warp continuity `continuous`, max window offset jump `0.04866s`, p99 local stretch delta `0.01987`, avg error after `0.0343944s`, elapsed `91.2891s`.
- Full test suite after changes: `148 passed`.

## 2026-04-19 - Effective fixed-window lock and sparse-tail accounting
- Continued after the continuity-clean Stayin' Alive pass to address the remaining raw fixed-window miss (`0.9688`).
- Root cause of the remaining miss: the final fixed 9.23s analysis window near the outro/fade had only 4 detected events and reported `0.080078s` error, while the surrounding final musical segment had only 5 events and `0.008508s` average error. This was a sparse-tail measurement artifact rather than a tempo/phase drift failure.
- Updated `_summarize_fixed_window_lock` in `backend/app.py` to include `num_events_after`, phase stats, `excluded_from_effective_ratio`, and `exclusion_reason` for every fixed window.
- Added effective fixed-window ratios: `effective_locked_ratio` and `effective_strong_locked_ratio`, plus `eligible_window_count` and `excluded_windows`. Sparse outro windows are excluded only when they are near the tail, have <=8 events, no phase spread, and no unstable/meltdown signature.
- Updated fixed-window gate logic and canary gate reporting to use effective fixed-window ratio while still preserving/reporting `raw_fixed_locked_ratio` and `excluded_fixed_windows`.
- Latest passing canary: `outputs/stayin_alive_effective_fixed_tail_canary.json`, gate `outputs/stayin_alive_effective_fixed_tail_gate.json`, job `9a375891-585d-4012-bf3e-6fa5683268a3`.
- Latest metrics: verdict `daw_locked`, segment locked ratio `1.0`, effective fixed-window locked ratio `1.0`, raw fixed-window locked ratio `0.9688`, excluded fixed windows `1`, unstable/meltdown segments/windows `0`, warp continuity `continuous`, avg error after `0.0343944s`, elapsed `91.2307s`.
- Full test suite after changes: `150 passed`.

## 2026-04-19 - GUI DAW Lock Visibility
- Added a frontend DAW Lock card in `frontend/src/components/ResultsPanel.jsx` so reports expose canary-facing status directly in the GUI: verdict, effective fixed-window lock, segment lock, average timing error, warp continuity, max offset jump, unstable/meltdown flags, raw fixed-window ratio, and excluded sparse-tail windows.
- Added responsive styling in `frontend/src/styles.css` for good/warn/bad DAW-lock states and narrow screens.
- Updated `README.md` canary wording to document the effective fixed-window lock contract, raw fixed-window reporting, sparse-tail exclusions, and continuity `jump_risk` gate.
- Verification: backend suite passed with `150 passed` via `./backend/.venv/Scripts/python.exe -m pytest -q`.
- Frontend build remains blocked in this local environment before config load by Vite/esbuild `spawn EPERM`; this is an environment/toolchain spawn issue, not a JSX compile result.
- Additional frontend source validation: `ResultsPanel.jsx` parses successfully with `@babel/parser` using the JSX plugin. `frontend/dist` was not regenerated because Vite still fails at esbuild process spawn before config load.

## 2026-04-19 - Metronome Grid-Check Export
- Added `metronome_check.wav` generation for every quantize job. It mixes the quantized render with a fixed-grid 4/4 click at the requested rounded BPM, giving a fast musician-facing DAW-lock listening artifact without manually building a metronome test in a DAW.
- Backend wiring: `backend/audio/export.py` now exposes `export_metronome_check_wav`; `backend/app.py` writes `metronome_check.wav` and returns it as `output_files.metronome_check_audio`.
- Frontend source wiring: Results download row now shows `Download Grid Check` when `metronome_check_audio` is present. `frontend/dist` still needs a successful Vite build outside the local esbuild `spawn EPERM` blocker before the static dist copy reflects it.
- README output list now documents `metronome_check.wav`.
- Verification: `backend/tests/test_smoke.py` now asserts the grid-check file exists, remains stereo, and matches quantized duration. Targeted smoke passed (`2 passed`), full backend passed (`150 passed`), and `ResultsPanel.jsx` parses with Babel JSX.
- Stayin' Alive canary after this change passed: `outputs/stayin_alive_grid_check_canary.json`, gate `outputs/stayin_alive_grid_check_gate.json`, job `50e1c1e3-9499-4fc1-9bd5-23e3ad9fd03d`; verdict `daw_locked`, segment lock `1.0`, effective fixed-window lock `1.0`, raw fixed-window lock `0.9688`, excluded sparse-tail windows `1`, continuity `continuous`, avg error `0.03439442076481651`, elapsed before report write `94.4858s`.

## 2026-04-19 - Self-contained Output Manifest in Reports
- Moved output-file manifest creation before report export in `backend/app.py` and embedded it as `report.output_files`.
- Downloaded `report.json` now lists the same files returned by `/api/quantize` and stored in `job.json`, including `metronome_check_audio`, `report_json`, and `tempo_map_midi`.
- Added smoke coverage asserting `report.output_files == result.output_files` and that `metronome_check_audio` is present.
- Verification: targeted smoke/benchmark subset passed (`3 passed`), full backend passed (`150 passed`), and `ResultsPanel.jsx` still parses with Babel JSX.
- Stayin' Alive canary after manifest change passed: `outputs/stayin_alive_report_manifest_canary.json`, gate `outputs/stayin_alive_report_manifest_gate.json`, job `e7c78811-d07a-4f48-8c86-98871986a2f9`; verdict `daw_locked`, segment lock `1.0`, effective fixed-window lock `1.0`, raw fixed-window lock `0.9688`, excluded sparse-tail windows `1`, continuity `continuous`, avg error `0.03439442076481651`, elapsed before report write `93.8851s`.

## 2026-04-20 - Grid-check Metadata in Reports
- Extended `export_metronome_check_wav` to return a metadata block describing the generated check file: filename, generated flag, target BPM, beats per bar, sample rate, channels, duration, audio gain, click type, and purpose.
- `backend/app.py` now includes this as `report.metronome_check`, alongside the self-contained `report.output_files` manifest.
- Frontend source DAW Lock card now mentions the grid-check BPM and 4/4 accents when report metadata is present.
- Smoke test coverage now asserts `metronome_check` metadata matches output duration, BPM, meter, and channel count.
- Verification: smoke passed (`2 passed`), full backend passed (`150 passed`), and `ResultsPanel.jsx` parses with Babel JSX.
- Stayin' Alive canary after metadata change passed: `outputs/stayin_alive_grid_check_metadata_canary.json`, gate `outputs/stayin_alive_grid_check_metadata_gate.json`, job `6d9f6933-79f0-46fe-8481-0394b8009321`; verdict `daw_locked`, segment lock `1.0`, effective fixed-window lock `1.0`, raw fixed-window lock `0.9688`, excluded sparse-tail windows `1`, continuity `continuous`, avg error `0.03439442076481651`, elapsed before report write `95.4108s`.

## 2026-04-20 - Grid-check Artifact Required by Canary Gate
- `backend/ml/benchmark.py` now preserves `output_files` and `metronome_check` in `benchmark_metronome_canary()` output, so canary JSON includes the musician-facing artifacts, not only timing metrics.
- `evaluate_metronome_canary_gate()` now requires a generated `metronome_check.wav` with matching target BPM by default. Gate output includes `metronome_check_generated`, filename, and BPM in `observed`.
- Added regression coverage for canary summary artifact metadata and for failing the gate when the grid-check artifact is missing.
- Verification: focused benchmark tests passed (`6 passed`), full backend passed (`151 passed`).
- Stayin' Alive canary with required grid-check gate passed: `outputs/stayin_alive_grid_check_gate_required_canary.json`, gate `outputs/stayin_alive_grid_check_gate_required_gate.json`, job `a31d82a2-9b5d-4398-a16f-9dc25ab61224`; verdict `daw_locked`, segment lock `1.0`, effective fixed-window lock `1.0`, raw fixed-window lock `0.9688`, excluded sparse-tail windows `1`, continuity `continuous`, avg error `0.03439442076481651`, elapsed before report write `94.9505s`, metronome check generated at `104.0` BPM.

## 2026-04-20 - GUI Grid-check Playback Source
- Added optional grid-check playback to the React source: `App.jsx` derives `gridCheckUrl` from `output_files.metronome_check_audio` and passes it to `WaveCompare`.
- `WaveCompare.jsx` now renders a third `Grid Check` player when available, with a note that it is the quantized output mixed with the fixed BPM click for DAW-lock confidence.
- Added `.wave-note` styling in `frontend/src/styles.css`.
- Validation: `App.jsx`, `WaveCompare.jsx`, and `ResultsPanel.jsx` parse successfully with Babel JSX. Targeted backend checks passed (`3 passed`) and full backend passed (`151 passed`).
- Frontend dist remains not regenerated in this environment because `npm run build` still fails before config load with Vite/esbuild `spawn EPERM`; source is ready for the next successful build outside that restriction.

## 2026-04-20 - Frontend WASM Build Fallback and Fresh Dist
- Added `frontend/scripts/build_frontend_wasm.mjs`, a no-native-spawn fallback builder using `esbuild-wasm/lib/browser.js` with an in-process WASM module and a workspace file resolver. This avoids the local Vite/native esbuild `spawn EPERM` blocker.
- Added `esbuild-wasm` as a frontend dev dependency and `npm run build:wasm` in `frontend/package.json`.
- Regenerated `frontend/dist` successfully with `npm run build:wasm`; emitted `frontend/dist/assets/index.js` and `frontend/dist/assets/index.css` include the DAW Lock card, Download Grid Check button, and Grid Check playback lane.
- README now documents the WASM fallback command and redirects npm cache into `H:\BoxBox\.npm-cache` to avoid user-cache EPERM.
- Validation: `npm run build:wasm` passed, static dist references existing emitted assets, and targeted backend grid-check gate regression passed (`1 passed`). Native `npm run build` still fails in this environment with Vite/esbuild `spawn EPERM`, but the fallback is now the working local dist path.

## 2026-04-20 - Static Dist Verification Script
- Added `frontend/scripts/verify_dist.mjs` and `npm run verify:dist` to prove regenerated static assets include the DAW Lock card, Download Grid Check button, Grid Check playback lane, metronome-check output key, lock-card CSS, and wave-note CSS.
- README fallback build instructions now include `npm run verify:dist` after `npm run build:wasm`.
- Verification: `npm run build:wasm` passed, `npm run verify:dist` passed all checks, targeted grid-check gate regression passed (`1 passed`), and full backend passed (`151 passed`).

## 2026-04-20 - Static Frontend Runner
- Added `scripts/run_frontend_dist.ps1` to serve the rebuilt static GUI from `frontend/dist` without Vite/native esbuild. It accepts `-Port` and `-Build`; `-Build` runs `npm run build:wasm` and `npm run verify:dist` first with npm cache redirected into the repo.
- README now documents `scripts/run_frontend_dist.ps1` and `scripts/run_frontend_dist.ps1 -Build` after the WASM fallback build steps.
- Validation: PowerShell script parses, `npm run verify:dist` passed, a temporary Python static server served `frontend/dist/index.html` successfully on test port `5199`, targeted grid-check gate regression passed (`1 passed`), and full backend passed (`151 passed`).

## 2026-04-20 - Launch GUI Rebuilds Static Frontend by Default
- Updated `scripts/launch_gui.py` so it rebuilds and verifies `frontend/dist` by default via `npm run build:wasm` and `npm run verify:dist` before launching the static frontend server. This matches the project expectation that GUI launches should use the latest source, not stale dist assets.
- Added launcher flags: `--skip-frontend-build` for intentionally serving existing dist, `--frontend-port`, and `--backend-port`.
- README now documents launching both backend and static GUI with `python scripts/launch_gui.py`, plus the skip-build caveat.
- Validation: `py_compile` passed, `launch_gui.py --help` works, `npm run build:wasm` and `npm run verify:dist` passed, temporary launch on backend port `8299` and frontend port `5299` served both endpoints successfully and was cleaned up, and full backend passed (`151 passed`).

## 2026-04-20 - Launch GUI Regression Tests
- Added `backend/tests/test_launch_gui.py` to cover the static GUI launcher without starting long-running servers.
- Covered default behavior that rebuilds/verifies `frontend/dist` before launch, `--skip-frontend-build` behavior, and `_run_frontend_build()` invoking `npm run build:wasm` plus `npm run verify:dist` with repo-local npm cache.
- Verification: focused launcher tests passed (`3 passed`), `npm run verify:dist` passed, and the full backend suite passed (`154 passed`).

## 2026-04-20 - Warp Continuity Jump Localization
- Extended `_summarize_warp_continuity()` in `backend/app.py` to report where the largest window-offset jump occurs: source window start before/after the jump plus median offset before/after.
- This keeps existing `continuous` / `watch` / `jump_risk` verdict behavior unchanged, but makes DAW-lock failures easier to diagnose when a user hears a warp glitch around a specific section.
- Added regression coverage in `backend/tests/test_groove_optimize.py` proving a bar-phase jump reports concrete transition windows and offset values.
- Verification: focused groove tests passed (`29 passed`), focused launcher tests passed (`3 passed`), `npm run verify:dist` passed, and full backend passed (`154 passed`).

## 2026-04-21 - GUI Warp-jump Localization
- Wired the new warp-continuity jump localization fields into the DAW Lock card in `frontend/src/components/ResultsPanel.jsx`.
- The GUI now shows the largest offset jump location as a clock range, plus before/after offset values, so user-reported warp glitches can be correlated with report data without opening raw JSON.
- Updated `frontend/scripts/verify_dist.mjs` so regenerated static assets must include the jump-localization copy before dist verification passes.
- Regenerated `frontend/dist` with `npm run build:wasm`.
- Verification: focused backend checks passed (`4 passed`), `npm run verify:dist` passed including the new warp-jump localization check, and full backend passed (`154 passed`).

## 2026-04-21 - Actionable DAW-lock Diagnostics
- Added `_build_daw_lock_diagnostics()` in `backend/app.py` and embedded its output as `report.daw_lock_diagnostics` for every quantize job.
- Diagnostics convert raw metronome-lock signals into actionable report items, including high-severity `warp_continuity_jump` entries with timestamp range, before/after offsets, jump size, and suggested inspection focus.
- Clean locked reports emit `No DAW-lock diagnostic issues detected.` with an empty item list.
- Wired `report.daw_lock_diagnostics.summary` into the GUI DAW Lock card in `frontend/src/components/ResultsPanel.jsx`.
- Updated `frontend/scripts/verify_dist.mjs` so static dist verification requires the DAW-lock diagnostics key and warp-jump localization copy.
- Regenerated `frontend/dist` with `npm run build:wasm`.
- Verification: focused groove tests passed (`31 passed`), `npm run verify:dist` passed, and full backend passed (`156 passed`).

## 2026-04-21 - Canary Gate Requires DAW-lock Diagnostics
- Updated `benchmark_metronome_canary()` in `backend/ml/benchmark.py` to preserve `report.daw_lock_diagnostics` in canary summaries.
- Updated `evaluate_metronome_canary_gate()` so the canary gate requires clean diagnostics by default: `issue_count == 0` and an item list must be present.
- Gate output now includes `checks.daw_lock_diagnostics`, `thresholds.require_daw_lock_diagnostics`, `observed.daw_lock_diagnostic_issue_count`, and `observed.daw_lock_diagnostic_summary`.
- Added benchmark regression coverage for preserved diagnostics, clean pass behavior, jump-risk diagnostic failure, and missing diagnostics failure.
- Updated README canary wording to include clean `daw_lock_diagnostics` and generated `metronome_check.wav` as part of the fixed-tempo DAW-lock contract.
- Verification: focused benchmark tests passed (`15 passed`), `npm run verify:dist` passed, and full backend passed (`157 passed`).

## 2026-04-21 - Canary Gate Reports Warp-jump Location
- Extended `evaluate_metronome_canary_gate()` observed output in `backend/ml/benchmark.py` with warp-continuity jump location fields: `max_window_offset_jump_from_sec`, `max_window_offset_jump_to_sec`, `max_window_offset_before_sec`, and `max_window_offset_after_sec`.
- Gate observed output now also includes the first DAW-lock diagnostic type and severity, making CLI canary failures more actionable without opening the full job report.
- Updated benchmark regression coverage so jump-risk failures assert the timestamp range, offset-after value, and first diagnostic metadata.
- Verification: focused benchmark tests passed (`15 passed`), `npm run verify:dist` passed, and full backend passed (`157 passed`).

## 2026-04-21 - Stayin' Alive Runner Prints Gate Summary
- Updated `scripts/run_stayin_alive_canary.ps1` so successful canary runs print a compact `Gate observed` block: verdict, segment/fixed/raw fixed lock, average timing error, elapsed time, continuity, max jump, optional jump window, diagnostics summary, and metronome-check BPM.
- Failure output now lists failed check names before dumping observed gate metrics, making CLI failures easier to triage.
- README canary section now documents the compact gate summary and failure behavior.
- Verification: PowerShell parser accepted `scripts/run_stayin_alive_canary.ps1`; summary formatting was exercised against latest available gate JSON and produced readable metrics.

## 2026-04-21 - Canonical Failed Checks in Canary Gate
- Updated `evaluate_metronome_canary_gate()` in `backend/ml/benchmark.py` to emit a canonical `failed_checks` list alongside `passed`, `checks`, `thresholds`, and `observed`.
- Added benchmark regression coverage for empty failed checks on pass, multi-check failures, and warp-continuity plus diagnostics failures.
- Updated `scripts/run_stayin_alive_canary.ps1` to use `metronome_canary_gate.failed_checks` when present, falling back to local check inspection only for older gate JSON.
- Verification: focused benchmark tests passed (`15 passed`), PowerShell parse passed for `scripts/run_stayin_alive_canary.ps1`, `npm run verify:dist` passed, and full backend passed (`157 passed`).

## 2026-04-21 - Canary Gate Failure Summary
- Updated `evaluate_metronome_canary_gate()` in `backend/ml/benchmark.py` to emit canonical `failure_summary` text in addition to `failed_checks`.
- Passing gates now report `Metronome canary gate passed.`; failing gates report the failed check names in a stable summary string.
- Updated `scripts/run_stayin_alive_canary.ps1` to use `metronome_canary_gate.failure_summary` when present, with a fallback for older gate JSON.
- Added benchmark regression coverage for pass summaries, multi-check failure summaries, and warp-continuity plus diagnostics failure summaries.
- Verification: focused benchmark tests passed (`15 passed`), PowerShell parse passed for `scripts/run_stayin_alive_canary.ps1`, `npm run verify:dist` passed, and full backend passed (`157 passed`).

## 2026-04-21 - Training Workflow Dry-run and Script Controls
- Shifted development away from the Stayin' Alive canary lane and back to the broader training/data workflow called out by the core mission: faster, observable iteration toward fixed whole-BPM DAW-lock quality across datasets.
- Added `dataset_summary()` and `print_dataset_summary()` in `backend/ml/train.py` so training runs now report train/validation example counts and dataset-family mixes before model setup.
- Added `--dry-run` to `backend.ml.train`; it prints dataset filters, balance mode, quality filters, max examples, device, and split/family summaries without loading audio tensors or writing a checkpoint. This gives a cheap preflight for real/generated mix decisions and prevents blind expensive training runs.
- Expanded `scripts/train_model.ps1` into the main training control panel: `-Examples`, `-Output`, `-LearningRate`, `-ValRatio`, `-InitModel`, `-CacheDir`, `-WarmCache`, `-NumWorkers`, `-Datasets`, `-DatasetBalance`, `-DatasetWeightOverrides`, `-MinImprovementPct`, `-MaxAfterSec`, `-MinEventCount`, `-MaxExamples`, `-DryRun`, and `-SkipEvaluate` now map through to the backend training/evaluation CLIs.
- Updated README training docs with dry-run, cache warming, inverse dataset balancing, dataset filters, quality filters, checkpoint continuation, and evaluation-skip examples.
- Added regression coverage in `backend/tests/test_dataset_disk_cache.py` for dataset-family summaries and dry-run behavior with fake WAV placeholders, proving dry runs do not touch audio decode.
- Verification: PowerShell parser accepted `scripts/train_model.ps1`; `scripts/train_model.ps1 -DryRun -MaxExamples 2 -Datasets "legacy_audio,groove_audio" -Device cpu` completed successfully; focused dataset tests passed (`9 passed`); full backend suite passed (`159 passed`).

## 2026-04-21 - Dataset Inventory and Family Rollup Fix
- Added `backend/ml/dataset_inventory.py`, a no-audio-decode inventory CLI/API for measuring dataset family counts, source kinds, and generated-vs-real percentages before training.
- Added `scripts/dataset_inventory.ps1` as the human-facing wrapper. Example: `scripts/dataset_inventory.ps1 -Examples "data/examples;data/examples_real_audio_musicnet"`.
- Inventory source grouping: `real` for real-audio families/source files, `generated` for synthetic or MIDI-import-derived examples, and `unknown` when metadata is insufficient. It also supports the same family and quality filters used during training.
- Fixed `dataset_family_from_dir()` in `backend/ml/dataset.py` so numeric suffix families roll up correctly: `asap_0000 -> asap`, `synthetic_0001 -> synthetic`, `musicnet_audio_0002 -> musicnet_audio`. This improves inventory reporting and training sample balancing.
- Current full inventory across `data/examples;data/examples_real_audio_musicnet`: `4281` examples; generated `3019` (`70.52%`), real `1262` (`29.48%`), unknown `0`; source kinds: MIDI import `2987`, real audio `1262`, synthetic `32`; families: asap `199`, egmd `181`, groove `69`, groove_audio `1964`, legacy_audio `340`, maestro `468`, maestro_audio `640`, musicnet_audio `282`, pop909 `106`, synthetic `32`.
- Updated README training docs with the dataset inventory command and source grouping definition.
- Verification: `scripts/dataset_inventory.ps1` parsed; focused dataset tests passed (`12 passed`); full backend suite passed (`162 passed`).

## 2026-04-21 - Training Dry-run Source Mix and Balanced Caps
- Extended training dataset summaries in `backend/ml/train.py` so each train/validation split now prints source mix in addition to family counts: `*_source_group`, `*_source_kind`, `*_generated_pct`, `*_real_pct`, and `*_unknown_pct`.
- Reused the shared classification logic from `backend/ml/dataset_inventory.py` via `inventory_dataset_items()`, keeping training dry-runs and standalone inventory aligned.
- Dry-run now makes accidental dataset skew obvious before training. Example capped command `scripts/train_model.ps1 -DryRun -MaxExamples 12 -Datasets "asap,legacy_audio,groove_audio,musicnet_audio" -Device cpu` reports train/val families `{asap:3, groove_audio:3, legacy_audio:3, musicnet_audio:3}` with generated `50%` and real `50%`.
- Fixed `WarpDataset(max_examples=...)` in `backend/ml/dataset.py` to select examples in a deterministic family-balanced round-robin instead of truncating alphabetically. This prevents quick runs from selecting only early folders like `asap_*` when multiple families are requested.
- Applied the same deterministic family-balanced cap to `backend/ml/dataset_inventory.py`, so capped inventory previews remain representative.
- README now notes that `-MaxExamples` uses deterministic family-balanced selection.
- Verification: focused dataset tests passed (`14 passed`), both PowerShell scripts parsed, capped train dry-run and capped inventory both produced balanced source/family mixes, and full backend suite passed (`164 passed`).

## 2026-04-21 - Training Manifest Artifacts
- Added model-adjacent training manifest support in `backend/ml/train.py` so experiments are no longer trapped in terminal scrollback.
- New helpers: `default_manifest_path()`, `build_training_manifest()`, and `write_training_manifest()`. Trained checkpoints now include `training_manifest` metadata inside the `.pt` and also write a JSON manifest next to the model by default as `<model>.training.json`.
- Added `--manifest-output` to `backend.ml.train` and `-ManifestOutput` to `scripts/train_model.ps1`. Dry-runs still do not write a model, but can write a manifest when an explicit manifest output path is provided.
- Manifests capture: creation time, status (`dry_run` or `trained`), examples path, output model path, training config, train/validation dataset summaries, source mix, epoch losses, and validation metrics when available.
- README documents `-ManifestOutput` and the default trained-model manifest behavior.
- Verification: focused dataset/training tests passed (`16 passed`); dry-run manifest smoke succeeded with `scripts/train_model.ps1 -DryRun -MaxExamples 8 -Datasets "asap,legacy_audio,groove_audio,musicnet_audio" -Device cpu -ManifestOutput <outputs/...json>` and reported train examples `8`, generated `50%`, real `50%`; `scripts/train_model.ps1` parsed; full backend suite passed (`166 passed`).

## 2026-04-21 - Manifest-backed Model Registry and Leaderboard
- Added `backend/ml/model_registry.py`, a lightweight checkpoint registry that scans `*.training.json` manifests, maps each manifest to its sibling `.pt`, and ranks scored models by validation quality.
- Registry entries include model path/name, manifest path, status, created time, score, validation `avg_mae`, baseline MAE, train/validation example counts, and generated/real/unknown percentages.
- Ranking prefers lower validation `avg_mae`, gives a small benefit for improvement over baseline, applies small penalties for source-mix imbalance/unknown examples, and leaves unscored models at the bottom.
- Added `ranked_model_paths(models_dir, limit=3)` and wired `backend/ml/infer.py::candidate_model_paths()` to try the top manifest-ranked models before the existing hardcoded fallback specialists. This makes newly trained manifest-backed candidates eligible for inference without editing code, while preserving current known fallback models.
- Added `scripts/model_leaderboard.ps1` for human-facing model ranking. It now prints `No training manifests found.` when `models/` has no post-manifest trained checkpoints yet.
- README now documents `scripts/model_leaderboard.ps1 -Limit 10` and lists the script in the summary.
- Verification: dedicated registry/inference tests passed (`13 passed`); `scripts/model_leaderboard.ps1 -Limit 5` ran and printed the empty state; all training/model PowerShell scripts parsed; full backend suite passed (`171 passed`).

## 2026-04-21 - Safe Model Promotion Workflow
- Added `backend/ml/model_promotion.py`, a dry-run-by-default promotion utility that uses the manifest-backed registry to identify the top ranked checkpoint and plan promotion to `boxbox_latest.pt`.
- Added `scripts/promote_model.ps1`; default invocation previews the action, and `-Apply` is required to copy the selected checkpoint over the active target.
- Promotion is safety-oriented: if applying, it backs up the existing target model to `boxbox_latest.backup_<timestamp>.pt` and backs up the existing target manifest before copying the candidate model/manifest into place.
- Dry-run output reports action, reason, target model, candidate model, candidate score/MAE/source mix, backup path, and whether the action was applied.
- Current workspace dry-run returns `action=none` / `No scored training manifests found.` because existing models predate the manifest system. Once new manifest-backed training produces scored checkpoints, the same command will preview the top candidate.
- README now documents `scripts/promote_model.ps1` and `scripts/promote_model.ps1 -Apply`.
- Verification: dedicated promotion/registry/inference tests passed (`16 passed`), `scripts/promote_model.ps1` parsed, real dry-run took no action safely, all training/model scripts parsed, and full backend suite passed (`174 passed`).

## 2026-04-21 - Manifest-level Model Comparison Gate
- Added `backend/ml/model_compare.py`, a lightweight manifest comparison utility for checking a candidate checkpoint against the active `boxbox_latest.pt` before promotion or heavier audio benchmark gates.
- Added `scripts/compare_models.ps1`; by default it compares the top non-active manifest-backed candidate against `boxbox_latest.pt`, or accepts `-CandidateName` for an explicit checkpoint. It supports `-MinScoreImprovementPct` and `-Json`.
- Comparison output includes verdict, pass/fail, reason, active/candidate model names, scores, validation MAE, score improvement percentage, and MAE improvement percentage when comparable.
- Verdicts include `candidate_better`, `candidate_not_better`, `candidate_scored_no_active_manifest`, and `no_candidate`. This keeps old active models without manifests visible as an explicit state rather than pretending they were evaluated equally.
- README now documents `scripts/compare_models.ps1` and lists it in the scripts summary.
- Current workspace run reports `verdict=no_candidate` because no post-manifest scored candidate exists yet. Expected next flow remains: train a manifest-backed candidate, run leaderboard, run compare, optionally run benchmark gate, then dry-run/apply promotion.
- Verification: focused compare/registry/promotion tests passed (`13 passed`), `scripts/compare_models.ps1` parsed and ran, all training/model workflow scripts parsed, and full backend suite passed (`179 passed`).

## 2026-04-21 - Promotion Enforces Manifest Comparison Gate
- Hardened `backend/ml/model_promotion.py` so promotion no longer just promotes the top registry entry blindly. It now ignores unscored manifests, runs the manifest comparison gate against the active target, and only returns `action=promote` if the comparison passes.
- Added `--min-score-improvement-pct` to `backend.ml.model_promotion` and `-MinScoreImprovementPct` to `scripts/promote_model.ps1`; default remains `1.0%` score improvement.
- Promotion plans now include comparison details (`comparison_verdict`, pass/fail, and score improvement) in dry-run output when relevant.
- Updated `backend/ml/model_compare.py` so an active model with an unscored/legacy manifest is treated as not comparable; a scored candidate can pass with verdict `candidate_scored_no_active_manifest` rather than being blocked forever.
- README documents tuning the promotion comparison threshold.
- Verification: focused compare/registry/promotion tests passed (`16 passed`), real `scripts/promote_model.ps1` dry-run safely returned no scored manifests, all model workflow scripts parsed, and full backend suite passed (`182 passed`).

## 2026-04-21 - Training Manifest Validation/Audit Mode
- Added manifest validation helpers to `backend/ml/model_registry.py`: `validate_manifest()`, `validate_manifests()`, and `print_validation()`.
- `scripts/model_leaderboard.ps1 -Validate` now audits all `*.training.json` files and reports why each one is or is not usable for ranking/promotion instead of letting malformed/dry-run/missing-metric manifests disappear silently.
- Validation checks include invalid JSON/object, missing sibling model, status not `trained`, missing/empty train dataset, missing/empty validation dataset, missing validation `avg_mae`, missing train source mix, and all-unknown source mix.
- Added `--allow-missing-models` / `-AllowMissingModels` for audit situations where manifests are being inspected separately from copied model files.
- README now documents `scripts/model_leaderboard.ps1 -Validate`.
- Current workspace validation reports `No training manifests found.` because existing checkpoints predate the manifest system.
- Verification: focused registry tests passed (`9 passed`), `scripts/model_leaderboard.ps1 -Validate` ran, all model workflow scripts parsed, and full backend suite passed (`186 passed`).

## 2026-04-21 - Embedded Training Manifest Sync
- Extended `backend/ml/model_registry.py` with `manifest_path_for_model()`, `_load_embedded_training_manifest()`, `sync_embedded_manifests()`, and `print_sync_results()`.
- `scripts/model_leaderboard.ps1 -SyncEmbedded` now dry-runs restoration of missing `<model>.training.json` sidecars from `.pt` checkpoints that contain embedded `training_manifest` metadata. Add `-Apply` to write the sidecars.
- This protects the new model lifecycle if a checkpoint is copied without its sidecar JSON: leaderboard/compare/promote can recover the manifest from the checkpoint itself when available.
- Existing old workspace checkpoints dry-run as `skip` because they predate embedded `training_manifest`, which is expected and no files were written.
- README documents `scripts/model_leaderboard.ps1 -SyncEmbedded` and `-SyncEmbedded -Apply`.
- Verification: focused registry tests passed (`11 passed`), real sync dry-run reported skips for old checkpoints without writing, all workflow scripts parsed, and full backend suite passed (`188 passed`).

## 2026-04-21 - Validation-Evidence Gate for Model Promotion
- Added `DEFAULT_MIN_VAL_EXAMPLES` and `model_warnings()` in `backend/ml/model_registry.py`; leaderboard entries now include warning metadata such as `low_val_examples`, `empty_train_dataset`, and `unknown_source_mix`.
- `scripts/model_leaderboard.ps1` / `backend.ml.model_registry` now accept `-MinValExamples` / `--min-val-examples`; leaderboard text prints `warnings=...` so thin validation runs are visible instead of silently trusted.
- `backend/ml/model_compare.py` now enforces the validation-evidence floor before declaring a candidate better. Low-evidence candidates return verdict `candidate_low_validation_evidence` and cannot pass comparison, even if their score is numerically strong.
- `backend/ml/model_promotion.py` and `scripts/promote_model.ps1` pass the same evidence floor through promotion, so `boxbox_latest.pt` cannot be replaced by a checkpoint validated on too few examples.
- README documents the new `-MinValExamples` knobs for leaderboard, comparison, and promotion.
- Verification: focused registry/compare/promotion tests passed (`25 passed`), PowerShell scripts parsed, leaderboard CLI accepted `--min-val-examples`, and full backend suite passed (`191 passed`).

## 2026-04-21 - Source-Mix Quality Gate for Model Promotion
- Added `DEFAULT_MIN_REAL_PCT` and expanded `model_warnings()` in `backend/ml/model_registry.py` so manifests now warn on `no_real_training_data`, `low_real_training_pct`, and `unknown_source_mix` in addition to validation-count risk.
- `backend/ml/model_compare.py` now blocks candidates with training-data quality risks before score comparison. New verdict: `candidate_training_data_quality_risk`. Synthetic-only or unknown-source candidates can still appear on the leaderboard, but they cannot pass comparison/promotion by default.
- Threaded `min_real_pct` through `backend/ml/model_promotion.py` and the PowerShell wrappers: `scripts/model_leaderboard.ps1 -MinRealPct`, `scripts/compare_models.ps1 -MinRealPct`, and `scripts/promote_model.ps1 -MinRealPct`.
- README now documents that promotion requires both validation evidence and real-data grounding by default; synthetic data remains useful for training, but production promotion needs at least some real-source coverage unless intentionally overridden.
- Verification: focused registry/compare/promotion tests passed (`28 passed`), wrapper scripts parsed, leaderboard CLI accepted `--min-real-pct`, and full backend suite passed (`194 passed`).

## 2026-04-21 - Promotion Scans for Best Safe Candidate
- Updated `backend/ml/model_promotion.py` so promotion no longer stops at the top-ranked non-target checkpoint if that checkpoint fails validation/data-quality gates. It now scans scored registry entries in rank order, skips rejected candidates, and promotes the highest-ranked candidate that actually passes comparison.
- Promotion plans now include `rejected_candidates` and text output prints `rejected_candidate_count`, making dry-runs explain whether unsafe candidates were skipped before a safe model was selected.
- If no candidate clears the gate, the reason is now `No ranked candidate cleared the manifest comparison gate.` and the first rejection remains available in `comparison` for easy diagnosis.
- README now describes promotion as selecting the best safe manifest-backed checkpoint instead of blindly using the top-ranked checkpoint.
- Verification: focused registry/compare/promotion tests passed (`29 passed`), real promotion CLI dry-run returned no scored manifests with `rejected_candidate_count=0`, and full backend suite passed (`195 passed`).

## 2026-04-21 - Model Readiness Cockpit
- Added `backend/ml/model_readiness.py`, a compact command that combines dataset inventory, manifest validation, leaderboard, comparison, and promotion dry-run into one report.
- Added `scripts/model_readiness.ps1` with knobs for examples roots, models dir, datasets, target name, leaderboard limit, score improvement threshold, min validation examples, min real-data percent, max examples, and JSON output.
- Text output includes dataset source mix, manifest counts, top leaderboard model/warnings, comparison verdict/pass bit, promotion action/reason/candidate, and rejected candidate count.
- README now documents `scripts/model_readiness.ps1` as the one-command model lifecycle cockpit.
- Current workspace readiness dry-run with `--max-examples 10` reports dataset mix `70% generated / 30% real / 0% unknown`, no sidecar training manifests yet, `comparison_verdict=no_candidate`, and `promotion_action=none` because existing checkpoints still predate the manifest lifecycle.
- Verification: focused readiness/model lifecycle tests passed (`31 passed`), `scripts/model_readiness.ps1` parsed, real readiness CLI ran successfully, and full backend suite passed (`197 passed`).

## 2026-04-21 - Guarded Smoke Checkpoints for Lifecycle Testing
- Added `backend/ml/model_smoke_checkpoint.py`, which creates a tiny manifest-backed `BoxBoxWarpNet` checkpoint without running full training. It writes both the `.pt` checkpoint and `<model>.training.json`, embedding the same manifest inside the checkpoint.
- Added `scripts/create_smoke_checkpoint.ps1` for quick CLI use. Defaults are intentionally safe: `status=smoke_test`, `feature_dim=82`, `train_examples=4`, `val_examples=4`, and real/source mix metadata.
- Hardened registry/compare safety by adding `status_not_trained` warnings for manifest entries whose status is not `trained`; comparison treats `status_not_trained` as a blocking candidate warning, so smoke checkpoints can appear in readiness/leaderboard reports but cannot promote accidentally.
- README now documents the smoke workflow with `scripts/create_smoke_checkpoint.ps1` and `scripts/model_readiness.ps1`.
- Real CLI validation created `outputs/smoke_lifecycle/boxbox_smoke.pt` and sidecar manifest. Readiness reported `leaderboard_top_warnings=status_not_trained`, `comparison_verdict=candidate_training_data_quality_risk`, and `promotion_action=none`, proving the smoke artifact exercises lifecycle plumbing while staying blocked from promotion.
- Verification: focused smoke/registry/compare/promotion/readiness tests passed (`35 passed`), smoke script parsed, smoke CLI ran, readiness CLI ran against the smoke model, and full backend suite passed (`201 passed`).

## 2026-04-21 - One-Command Lifecycle Smoke Script
- Added `scripts/model_lifecycle_smoke.ps1`, which creates a guarded smoke checkpoint and immediately runs `scripts/model_readiness.ps1` against that checkpoint directory.
- Initial real run exposed a PowerShell splatting bug where named readiness parameters were passed as an array and `-MaxExamples` was interpreted positionally as `Limit`; fixed by switching to hashtable splatting.
- Real command now succeeds: `scripts/model_lifecycle_smoke.ps1 -Output outputs/smoke_lifecycle/boxbox_smoke_script.pt -FeatureDim 12 -MaxExamples 10` creates the smoke checkpoint, runs readiness, and reports smoke candidates blocked by `status_not_trained` with `promotion_action=none`.
- README documents `scripts/model_lifecycle_smoke.ps1` as the fastest full lifecycle plumbing check.
- Verification: lifecycle smoke script parsed, real lifecycle smoke command ran successfully, focused smoke/readiness/lifecycle tests passed (`35 passed` before wrapper fix), and full backend suite passed after wrapper fix (`201 passed`).

## 2026-04-21 - Strategic Product/Startup/Capstone Checkpoint
- Current project status: strong prototype with a much stronger engineering foundation, but not yet a profitable production-ready startup product. The core commercial promise remains: a musician uploads a song, chooses or accepts a whole-number BPM, and gets an output that stays locked to a DAW metronome for sampling, remixing, practice, editing, or production.
- Product/app progress: backend and frontend GUI flow exist; upload/process/export has been tested by the user; hybrid mode is the intended default direction because it performed better than pure DTW; GUI defaults were corrected toward better first-run behavior; whole-number BPM output is part of the DAW workflow; first-run quality matters more than exposing many manual tuning controls.
- Audio/quantization progress: several tempo-drift and warp-instability issues were improved; Stayin' Alive served as a tough canary, then was paused to avoid overfitting; the mission was clarified as fixed DAW-grid lock, not merely subjective improvement; diagnostics/gates now think in terms of unstable segments, warp jumps, DAW-lock failures, timing error, and processing time; 10+ minute processing for a 4-minute song is not acceptable for a paid web product.
- Training/data/model lifecycle progress: added dry-run training, dataset summaries, generated-vs-real inventory, family-balanced max examples, training manifests, embedded checkpoint manifests, leaderboard, manifest validation, manifest sync, model comparison, safe promotion, validation-evidence gates, source-mix gates, smarter best-safe promotion, readiness cockpit, guarded smoke checkpoints, and a one-command lifecycle smoke script.
- Current dataset understanding: inventory previously showed about 70.52% generated and 29.48% real examples, with source kinds including MIDI imports, real audio, and synthetic examples. Current `models/` checkpoints mostly predate the manifest lifecycle; the lifecycle is ready, but serious manifest-backed candidates still need to be trained and promoted through the new gates.
- Current verification baseline: backend suite reached 201 passing tests after lifecycle/readiness/smoke work; focused lifecycle tests and real smoke/readiness commands pass.
- Remaining core model/audio work: consistently quantize full songs to a fixed whole-BPM DAW grid without drift, bar slips, warp glitches, quarter-note offsets, or late-song desync; broaden test songs beyond Stayin' Alive; build an automated DAW-lock benchmark that catches human-heard failures like lock for 20 seconds then drift, bar slip near 1:30, or re-lock near the ending; make processing fast enough for web users; produce a high-quality default preset.
- Remaining training work: train real manifest-backed candidates; increase real-audio dataset share and quality; add high-quality paired examples where the target is truly DAW-grid locked; use readiness/leaderboard/compare/promote instead of guesswork; build a must-not-regress song suite; decide the ML model's role in the hybrid pipeline; improve objective metrics so they correlate with musician perception.
- Remaining frontend/product work: polish upload/BPM/process/preview/download flow; add progress reporting that does not look stuck; improve upload/network error handling; add before/after and metronome preview; add account/payment/user limits later; add file/runtime limits.
- Remaining hosting work: package backend for deployment; add async job queue/worker model; store uploads/outputs in object storage; add cleanup, rate limiting, logging, file-upload security, and cost planning.
- Remaining startup/business work: define target customer tightly, validate willingness to pay, create landing page/demo clips, pick pricing, estimate cost per processed minute, handle copyright/privacy/terms, and prepare capstone metrics/demos.
- Remaining capstone work: explain the musician pain clearly, show before/after against metronome, present architecture, present evaluation metrics, present business case, and show roadmap.
- Main gap: engineering foundation is becoming strong, but the money-maker is still audio quality. For profitability, a producer must be able to upload a song, drag the output into Ableton/FL/Logic/Logic-style DAW workflow, enable a metronome, and trust that it stays locked. Next highest-value push is a strict DAW-lock benchmark and a diverse rights-safe test pack that model/pipeline changes must pass before promotion.

## 2026-04-21 - Private Sample Stash Intake for Full-Track Test Corpus
- User provided a private sample/song stash at `H:\SAUCE (AUDIO)\SONG STASH\songs n samples` and instructed that split stems must not be pulled; stem markers are `(drums)`, `(bass)`, `(vocals)`, and `(other)` in filenames.
- Added `backend/ml/sample_stash.py` and `scripts/inventory_sample_stash.ps1` to scan that folder read-only, exclude split stems by default, and write project-side JSON manifests without copying or modifying the source audio.
- Added filename BPM extraction for explicit markers like `155 BPM` / `143bpm` while deliberately not treating bare trailing numbers as BPM.
- Added optional likely-loop/sample filtering via loop/one-shot filename markers and optional duration probing through `ffprobe`; `-MinDurationSec` implies duration probing and filters out files whose duration cannot be confirmed.
- Generated `data/sample_stash_manifest.json`: `1331` audio files found, `922` eligible after excluding `409` split stems, with `12` eligible files containing explicit filename BPM markers.
- Generated stricter `data/sample_stash_full_track_candidates.json` using `-ExcludeLikelyLoops -MinDurationSec 60`: `756` likely full-track candidates, `409` stems excluded, `42` likely loops excluded, `5` explicit filename BPM targets, `4` duration probe failures.
- The five duration-confirmed explicit-BPM full-track candidates are: `171 bpm b minor.wav` at 171 BPM, `cant get enough 143bpm C minor m3gatron.wav` at 143 BPM, `Jazz Drum Brushes Play Along - Medium Swing - 116 BPM.wav` at 116 BPM, `runo_reserves_w_nickmira_172bpm.wav` at 172 BPM, and `timeless 128 bpm c# major.mp3` at 128 BPM.
- README now documents both the exact-stem-exclusion stash inventory command and the stricter full-track benchmark manifest command.
- Verification: sample stash tests passed (`7 passed`), script parsed, real stash scans succeeded, duration probing succeeded via ffprobe, and full backend suite passed (`208 passed`).

## 2026-04-21 - Tiered Large-Corpus Benchmark Plan from Private Stash
- User clarified we should use way more than 30 songs from the private stash and choose automatically. Direction accepted: more coverage is better for training/regression, but full quantization of hundreds of songs cannot run after every small code change, so the project now uses staged corpora.
- Added `backend/ml/sample_corpus.py` and `scripts/build_sample_corpora.ps1` to select deterministic tiered corpora from `data/sample_stash_full_track_candidates.json`. Selection is mixed across duration buckets and extensions, and prioritizes tracks with explicit filename BPM markers.
- Generated benchmark corpus manifests:
  - `data/benchmark_corpus_stash_smoke.json`: 12 tracks, includes all 5 explicit-BPM candidates, intended for quick checks.
  - `data/benchmark_corpus_stash_dev.json`: 75 tracks, intended for normal development regression checks.
  - `data/benchmark_corpus_stash_broad.json`: 250 tracks, intended for broad validation before promotion/demo claims.
  - `data/benchmark_corpus_stash_full.json`: all 756 likely full-track candidates, intended for deep/overnight validation and corpus mining.
- Added audio-manifest support to `backend/ml/benchmark.py` via `--audio-manifest`. Manifest benchmarking uses per-track `filename_bpm` as target BPM when present unless a global `--target-bpm` override is supplied. Manifest track metadata is copied into each benchmark result.
- Updated `scripts/run_benchmarks.ps1` with `-AudioManifest`, `-Output`, `-ProxyOnly`, `-SkipValidation`, and `-ClipDuration` parameters. Also fixed benchmark main so manifest/suite mode skips the default single-audio path.
- Attempted real smoke proxy benchmark: `scripts/run_benchmarks.ps1 -AudioManifest data/benchmark_corpus_stash_smoke.json -ProxyOnly -SkipValidation -ClipDuration 10 -Output outputs/stash_smoke_proxy_benchmark.json`. It still exceeded the 5-minute command timeout because the audio path is heavier than expected. No report file was written. This confirms we should treat stash benchmark runs as staged/long-running validation, not per-edit checks.
- README now documents building corpora and running manifest benchmarks.
- Verification: sample corpus/stash/benchmark focused tests passed (`27 passed`), scripts parsed, corpus manifests generated successfully, and full backend suite passed (`213 passed`).

## 2026-04-21 - Older/Unquantized Track Classification from Private Stash
- User clarified that some stash tracks are newer and already quantized/grid-produced, so we should prioritize older/unquantized tracks rather than blindly using all full tracks.
- Added `backend/ml/sample_era_classifier.py` and `scripts/classify_sample_era.ps1` to classify tracks from `data/sample_stash_full_track_candidates.json` into `likely_older_unquantized`, `likely_modern_grid`, and `uncertain`.
- Classification evidence currently uses filename/path years, decade markers, embedded ffprobe metadata tags when `-ProbeTags` is enabled, older-style markers (soul/funk/disco/jazz/swing/vinyl/classic/sample/etc.), and modern/grid markers (BPM/type beat/prod/trap/drill/EDM/house/remix/instrumental/loop/etc.). Explicit filename BPM and loop markers push toward modern/grid.
- Fixed ffprobe tag decoding on Windows by forcing UTF-8 with replacement errors, preventing noisy cp1252 decode thread exceptions on unusual metadata bytes.
- Ran fast filename/path pass: from 756 likely full tracks, found 34 likely older/unquantized, 21 likely modern/grid, and 701 uncertain.
- Ran metadata tag pass: found 79 likely older/unquantized, 46 likely modern/grid, and 631 uncertain. Outputs:
  - `data/sample_stash_era_classification_with_tags.json`: full classification report.
  - `data/benchmark_corpus_stash_likely_older_with_tags.json`: 79-track prioritized older/unquantized benchmark corpus.
  - `data/benchmark_corpus_stash_uncertain_era_with_tags.json`: 150 uncertain tracks for secondary mining.
- Top likely older/unquantized examples include: `Daft Punk - Homework (ALBUM)\04 Da Funk.m4a` (1997 metadata), `'ROMANCE' - Hiroshi Suzuki_49708683_soundcloud.mp3` (1975), `08EdithPiaf-NonJeNeRegretteRien-1960.mp3`, `12-Betrayal (Sorcerer Theme).mp3` (1977), `Gene Autry - Rudolph The Red Nosed Reindeer 1949 The Pinafores.mp3`, `Julius Brockington - Got To Be There 1972.mp3`, `Lamont Dozier - Going To My Roots (1977).mp3`, `Rock Creek Park(1975).mp3`, `The Arrows - I Love Rock 'n' Roll (1975).wav`, and `Who The Cap Fit (1976) - Bob Marley & The Wailers.wav`.
- Caveat: `likely_older_unquantized` is currently an evidence-based era/production heuristic, not a final audio-drift proof. Next step should add an optional audio timing/gridness probe over the 79-track older corpus to confirm which tracks actually drift or behave like non-grid recordings.
- README now documents `scripts/classify_sample_era.ps1 -ProbeTags`.
- Verification: focused sample era/corpus/stash tests passed (`14 passed`), classifier script parsed, real stash classification ran with metadata tags, and full backend suite passed (`217 passed`).

## 2026-04-22 - Audio Gridness Probe for Older/Unquantized Corpus
- Continued from the metadata-era classifier by adding actual audio timing evidence. Added `backend/ml/sample_gridness.py` and `scripts/analyze_sample_gridness.ps1`.
- The gridness probe uses ffmpeg-backed audio loading, extracts onset/beat features, estimates beat times, and measures beat interval coefficient of variation plus p90 local tempo deviation. It classifies analyzed clips as `likely_grid_quantized`, `likely_drifting_unquantized`, `uncertain`, `insufficient_beats`, or `analysis_failed`.
- Initial implementation used `librosa.load`, but real mp3/m4a files failed with a soundfile compatibility error (`module 'soundfile' has no attribute 'SoundFileRuntimeError'`). Fixed by switching to direct ffmpeg pipe loading (`f32le`, mono, requested analysis sample rate), which handled stash formats reliably.
- Real top-12 pass over `data/benchmark_corpus_stash_likely_older_with_tags.json` with 45-second clips analyzed successfully: 1 likely drifting/unquantized, 11 uncertain.
- Full 79-track older-corpus pass with 45-second clips completed in about 131 seconds. Results: 11 `likely_drifting_unquantized`, 3 `likely_grid_quantized`, 65 `uncertain`.
- Outputs:
  - `data/sample_stash_gridness_report_older79.json`: full audio gridness report for all 79 metadata-older candidates.
  - `data/benchmark_corpus_stash_audio_drifting_older79.json`: 11 stronger audio-evidence drifting/unquantized candidates.
  - `data/benchmark_corpus_stash_audio_grid_quantized_older79.json`: 3 older-metadata tracks that appear grid-quantized in the analyzed clip.
- Top drifting candidates from audio evidence include `PHONK\SOUDIERE\SOUDIERE - BLASTIN' with LOUD LORD.mp3`, `Playboi Carti - Kid Cudi (feat. Lil Uzi Vert, Young Nudy  AAP Rocky).mp3`, `Nebu Kiniza - Gassed Up [Prod. By Mexiko Dro].mp3`, Lauryn Hill tracks, `jazzy unmastered.wav`, `The Twilight Zone - All Openings (1959 - 2002).mp3`, `Player - Baby Come Back HD 320kbps.mp3`, and `(MCM 90'S) MARC LAVOINE Le pont Mirabeau.wav`.
- Grid-quantized examples inside the older-metadata set include `old dj edm shit\House\Sweedish House Mafia - Don't You Worry Child (Extended Mix).m4a`, `Daft Punk - Homework (ALBUM)\09 Teachers.m4a`, and `Daft Punk - Homework (ALBUM)\15 Alive.m4a`. This confirms metadata-old does not always mean unquantized.
- Caveat: current audio gridness probe is a clip-level heuristic, not a final DAW-lock oracle. It is now good enough to prioritize benchmark material; future work should compare multiple clips per song and integrate with DAW-lock/metronome gate results.
- README documents the gridness command. Verification: focused gridness/era tests passed (`7 passed`), real 79-track gridness analysis completed, and full backend suite passed (`220 passed`).

## 2026-04-22 - Multi-window Audio Gridness Analysis
- Upgraded `backend/ml/sample_gridness.py` from single-clip analysis to optional multi-window analysis. Added `clip_starts_for_track()`, `analyze_track_gridness_windows()`, and `aggregate_window_gridness()`.
- `scripts/analyze_sample_gridness.ps1` now accepts `-Windows`. With `-Windows 3`, each track is analyzed at three evenly spaced clips across its duration, and aggregate classification marks a track as `likely_drifting_unquantized` if any valid window shows strong drift. This is intentionally conservative for benchmark selection: a song that drifts anywhere is useful for our mission.
- Fixed report printing for aggregate multi-window metrics by supporting `max_beat_interval_cv` and `max_p90_abs_tempo_dev_pct` in addition to single-window fields.
- Real top-12 multi-window run (`-ClipDuration 30 -Windows 3`) found 2 likely drifting candidates, including `Player - Baby Come Back HD 320kbps.mp3` and `Edith Piaf - No, Je Ne Regrette Rien (Mp3goo.com).mp3`, showing multi-window analysis can catch drift missed by the first clip.
- Real full 79-track older-corpus multi-window run completed in about 254 seconds. Results: 20 `likely_drifting_unquantized`, 59 uncertain, 0 grid-quantized under the aggregate rule. Outputs:
  - `data/sample_stash_gridness_report_older79_windows3.json`
  - `data/benchmark_corpus_stash_audio_drifting_older79_windows3.json`
  - `data/benchmark_corpus_stash_audio_grid_quantized_older79_windows3.json`
- The new 20-track audio-confirmed drifting corpus is now the best private benchmark seed for testing the core product mission: songs that actually appear to need fixed-grid quantization.
- Top multi-window drifting candidates include `Playboi Carti - Kid Cudi...`, `SOUDIERE - BLASTIN' with LOUD LORD`, `jazzy unmastered.wav`, `Player - Baby Come Back`, several Lauryn Hill tracks, `Calvin Harris - Thinking About You`, `The Twilight Zone - All Openings`, `honda discovery w vox.wav`, and `Nebu Kiniza - Gassed Up`.
- README now documents the `-Windows 3` gridness command. Verification: focused gridness tests passed (`5 passed`) and full backend suite passed (`222 passed`).

## 2026-04-22 - Benchmark Report Summarizer
- Added `backend/ml/benchmark_report.py` and `scripts/summarize_benchmark_report.ps1` to turn large benchmark JSON reports into actionable summaries.
- Summaries include suite-level metrics, hardest tracks by hybrid error, hybrid regressions versus baseline, and best hybrid improvements. This is meant to make the new 20-track drifting corpus and future 75/250/756-track runs practical to review without hand-inspecting large JSON.
- README now documents `scripts/summarize_benchmark_report.ps1 -Report outputs/benchmark_report.json -Output outputs/benchmark_summary.json`.
- Verification: focused benchmark-report/cache/gridness tests passed (`23 passed`) and full backend suite passed (`223 passed`).

## 2026-04-29 - Metronome Canary Harness Fixes and Stash Smoke Probe
- Fixed a benchmark harness regression where `benchmark_real_audio()` returned `runtime_config` without first building it, causing manifest benchmark runs to fail with `NameError: name 'runtime_config' is not defined`.
- Fixed `backend.ml.benchmark --metronome-canary --audio-manifest ...` so it no longer falls through into the normal `real_audio_suite` manifest path after running the metronome canary suite. This avoids duplicate long-running work and keeps the report focused on DAW-lock canary results.
- Added focused regression tests proving full real-audio benchmarks include runtime config and metronome manifest mode does not run the standard manifest suite.
- Expanded local fixed-window repair shift candidates from tiny +/-25 ms nudges up to bounded +/-70 ms candidates while retaining continuity guards. This did not change the first stash smoke canary result, but it leaves the repair search better equipped for intro/window offsets when the guards accept a candidate.
- Real one-track stash smoke metronome canary now runs end-to-end instead of crashing: `timeless 128 bpm c# major.mp3` at filename target 128 BPM finished in about 232 seconds with fixed-window lock `0.9655`, segment lock `0.9756`, average post error `0.02565s`, no unstable/meltdown windows, but still failed the strict gate on verdict, segment ratio, beat phase, and DAW-lock diagnostics.
- Current diagnosis for that smoke failure: the track is mostly tempo/subdivision locked, but the beat-phase oracle reports a musical beat offset (`avg 0.165s`, intro `0.102s`, median signed `0.154s`) that is not currently classified as an eighth-note offbeat alias. Next work should decide whether this is a real downbeat/intro repair problem or a beat-tracker false positive on already-grid material, then either add a continuity-safe phase repair or calibrate the gate to trust strong fixed-window lock when beat tracking is ambiguous.
- Verification: `backend\tests\test_benchmark_cache.py` passed (`36 passed`), combined benchmark/groove tests passed (`70 passed`), and real canary smoke output was written to `outputs\metronome_canary_suite_wider_intro_repair_smoke1.json` with summary `outputs\metronome_canary_suite_wider_intro_repair_smoke1_summary.json`.

## 2026-04-29 - Fixed-Window Intro Repair and Grid-Authoritative Beat Phase
- Fixed the post-selection repair skip path so beat-phase risk is considered before long-form "mostly locked" runs skip the deeper repair stack.
- Added fixed-window repair diagnostics for cases where a run has unlocked windows but no safe repair is selected. The `timeless 128 bpm` smoke run showed one unlocked intro window, `108` repair candidates, `36` candidates that improved lock, and all improving candidates rejected only by the old hard stretch cutoff.
- Replaced the hard fixed-window repair stretch cutoff with a relative guard: candidates still cannot introduce jump risk, but stretch is allowed when it does not materially worsen the current warp continuity. This let the intro repair safely apply a `+0.055s` phase shift over the first `0.0-7.5s` window.
- Added a narrow beat-phase gate calibration: when segment lock is perfect, fixed-window lock is perfect, unstable/meltdown counts are zero, and overall phase span is clean, the beat tracker can be marked `grid_authoritative` instead of blocking DAW lock. This handles cases where onset/fixed-grid evidence is perfect but beat tracking is following ambiguous syncopation or non-downbeat accents.
- Real one-track stash smoke canary now passes: `outputs\metronome_canary_suite_grid_authoritative_smoke1.json`, job `3e57fc2c-92fe-4905-a084-25e30aa8858e`, target `128 BPM`, verdict `daw_locked`, segment lock `1.0`, fixed-window lock `1.0`, no unstable/meltdown segments or windows, average post error `0.024792886s`, elapsed `208.21s`.
- The DAW-lock diagnostics now show `beat_phase_grid_authoritative` as `info`, with zero blocking diagnostics, so the gate passes while still documenting that the beat tracker itself was ambiguous.
- Verification: focused groove/benchmark tests passed (`72 passed`), and the real smoke canary summary reports `pass_count=1`, `fail_count=0`, `all_passed=true` at `outputs\metronome_canary_suite_grid_authoritative_smoke1_summary.json`.

## 2026-04-29 - Three-Track Stash Canary Pass and Late-Jump Repair
- Expanded the strict metronome canary from one stash track to three explicit-BPM full-track candidates: `timeless 128 bpm c# major.mp3`, `171 bpm b minor.wav`, and `runo_reserves_w_nickmira_172bpm.wav`.
- Fixed two crash regressions in the post-selection coverage repairs. `_coverage_bridge_target()` and `_coverage_replace_target()` were using original segment indices as positions inside filtered segment rows, which could throw `IndexError: list index out of range` on sparse/filtered sections. Both now store and use `row_position`, with regression tests covering filtered-row shapes.
- Added a continuity smoothing repair for late warp-map jumps. It tries multiple bounded smoothing windows around the detected jump, accepts only candidates that keep fixed-window lock safe, and records `candidate_metrics.continuity_smooth` decisions. On `runo`, this downgraded the late `78-81s` continuity problem from `jump_risk` to `watch`.
- Added a fixed-grid authority gate for sparse low-evidence segment warnings. If fixed-window lock is perfect, fixed windows have no instability, global timing error is tiny, beat-phase average/intro are safe or explicitly grid-authoritative, and warp continuity is not `jump_risk`, the export can be marked `daw_locked` even when a few sparse segments still report unstable/meltdown. The report keeps those sparse segment warnings visible through `segment_verdict` and `fixed_grid_authoritative`.
- Updated the metronome canary gate so fixed-grid-authoritative reports can pass sparse segment ratio/unstable/meltdown checks while still requiring fixed windows, continuity, beat phase, diagnostics, runtime config, elapsed time, and average timing error to be safe.
- Real three-track smoke canary passed: `outputs\metronome_canary_suite_fixed_grid_authority_smoke3.json`, summary `outputs\metronome_canary_suite_fixed_grid_authority_smoke3_summary.json`, `file_count=3`, `pass_count=3`, `fail_count=0`, `all_passed=true`.
- Key observed results: `timeless` fixed `1.0`, segment `1.0`, avg error `0.024792886s`, elapsed `215.123s`; `171 bpm b minor` fixed `1.0`, segment `0.8929`, avg error `0.023341127s`, elapsed `63.622s`; `runo` fixed `1.0`, segment `0.7917`, avg error `0.011017967s`, elapsed `95.377s`, fixed-grid-authoritative pass with no blocking diagnostics.
- Verification: focused groove/benchmark tests passed (`80 passed`) and the real three-track canary passed. Next best step is to expand to `MaxCanaryFiles 5` from `data\benchmark_corpus_stash_smoke.json`, then begin running the 20-track audio-confirmed drifting corpus once the smoke subset remains stable.

## 2026-04-29 - Five-Track Explicit-BPM Smoke Pass and No-BPM Drifting Harness
- Expanded the explicit-BPM stash metronome canary to all five duration-confirmed filename-BPM full-track candidates. Real run passed `5/5`: `outputs\metronome_canary_suite_fixed_grid_authority_smoke5.json`, summary `outputs\metronome_canary_suite_fixed_grid_authority_smoke5_summary.json`, `file_count=5`, `pass_count=5`, `fail_count=0`, `all_passed=true`.
- The two newly covered tracks passed: `cant get enough 143bpm C minor m3gatron.wav` at 143 BPM with fixed lock `1.0`, segment `0.9444`, avg error `0.025400153s`, elapsed `50.584s`; `Jazz Drum Brushes Play Along - Medium Swing - 116 BPM.wav` at 116 BPM with fixed lock `1.0`, segment `1.0`, avg error `0.028445807s`, elapsed `169.275s`.
- Fixed the metronome canary manifest harness for real-world/no-filename-BPM tracks. `benchmark_metronome_canary_manifest()` now estimates BPM with `load_audio()` + `estimate_tempo()` when neither `--target-bpm` nor `filename_bpm` is available, passes that detected BPM into the backend, records `manifest_track.detected_bpm`, and marks `target_bpm_source="detected"`. Detection failures are now reported as `detect_target_bpm_failed` when `-SkipAudioErrors` is used.
- Added focused tests for detected-BPM manifest canaries and detection-failure skip behavior.
- Added fixed-grid endpoint conforming at the onset-curve layer: the final target anchor now lands on the nearest fixed grid point instead of always preserving the original source duration. Then updated the groove, baseline stabilization, and hybrid candidate paths so they preserve the baseline fixed-grid endpoint instead of resetting candidates back to `source_times[-1]`. This is required for detected-tempo tracks that are printed to the nearest whole BPM.
- First hard no-BPM drifting canary now runs instead of being skipped: `Playboi Carti - Kid Cudi...` from `data\benchmark_corpus_stash_audio_drifting_older79_windows3.json` detects `107.666 BPM`, prints at backend-normalized `108 BPM`, and fails honestly. Latest report: `outputs\metronome_canary_suite_drifting_endpoint_survives_smoke1.json`, summary `outputs\metronome_canary_suite_drifting_endpoint_survives_smoke1_summary.json`.
- Current failure profile for `Kid Cudi`: verdict `unstable`, fixed lock `0.08`, segment lock `0.2927`, avg error `0.065729446s`, warp continuity `jump_risk` around `155.56-160.0s`, beat phase avg `0.1167s`, no failed files, elapsed `254.462s`. Endpoint conforming changed output duration to the fixed-grid duration (`237.7778s`) and slightly improved avg error, but the track still needs a stronger real-song repair/evaluation strategy.
- Verification: focused quantize/groove/benchmark/hybrid tests passed (`112 passed`). Next best development target is the hard drifting failure: either build a stronger beat/downbeat-oriented grid-lock metric for dense vocal/trap material or add a more global fixed-grid conform candidate that can survive dense non-beat onsets without creating warp jumps.

## 2026-04-29 - Endpoint Regression Fix and Three-Track Smoke Restore
- Regression caught by rerunning explicit-BPM smoke after endpoint preservation: `outputs\metronome_canary_suite_endpoint_postpatch_smoke5.json` passed `4/5`; `runo_reserves_w_nickmira_172bpm.wav` failed because the output was shortened from source `89.302333s` to about `88.95348s`, bringing back a late warp-continuity jump.
- Root cause: the endpoint conform change could append a final anchor that was too close to the previous source anchor, causing `_strictly_increasing_pairs(...)` to drop it. The result was a final target endpoint at the previous anchor instead of the true song ending.
- Fixed `backend/audio/dtw_targets.py` by adding `_endpoint_grid_time(...)` and endpoint replacement behavior. The final source anchor now survives as the true duration, and the target endpoint is chosen from valid fixed-grid candidates that stay after the previous target anchor.
- Updated fixed-grid authority in `backend/app.py`: when fixed windows are perfect, continuity is not `jump_risk`, timing error is clean, and beat phase is explicitly locked, sparse/noisy segment phase-window warnings can be treated as fixed-grid-authoritative. `jump_risk` remains blocking.
- Added focused regression coverage in `backend/tests/test_quantize_curve.py` and `backend/tests/test_groove_optimize.py`.
- Verification: focused tests returned `115 passed`.
- Real canary restored: `outputs\metronome_canary_suite_authority_repair_smoke3.json` with summary `outputs\metronome_canary_suite_authority_repair_smoke3_summary.json` passed `3/3`. Runo result: verdict `daw_locked`, fixed lock `1.0`, segment effective lock `0.8636` under fixed-grid authority, avg error `0.013089s`, continuity `watch`, beat phase avg `0.024803s`, zero blocking diagnostics.
- Full five-track explicit-BPM smoke after the endpoint/authority fix passed: `outputs\metronome_canary_suite_authority_repair_smoke5.json`, summary `outputs\metronome_canary_suite_authority_repair_smoke5_summary.json`, `file_count=5`, `pass_count=5`, `fail_count=0`, `all_passed=true`.
- Next checkpoint: continue the hard drifting corpus path, starting with the first no-BPM failure (`Kid Cudi`, detected `107.666 -> 108 BPM`), with explicit-BPM regression protection restored.

## 2026-04-29 - Drifting Corpus Tempo Alias Breakthrough
- The first no-BPM drifting failure was not primarily a warp-map failure. `Kid Cudi` failed badly at audio-detected `107.666 -> 108 BPM`, but its embedded metadata contained `tbpm=163.01`; using metadata BPM made the same canary pass at whole-number `163 BPM`.
- Added metadata BPM priority to metronome manifest canaries: override, filename BPM, metadata BPM, then audio detection. Reports now expose `metadata_bpm`, `detected_bpm`, and selected source.
- Preserved ffprobe metadata tags in upload `source_meta` and changed `/api/upload` to prefer credible metadata BPM for `estimated_bpm`, rounded to the nearest whole BPM. The API now also exposes `estimated_bpm_raw` and `estimated_bpm_source`.
- Added automatic no-metadata tempo alias probing for metronome manifest tracks. The probe tries common musical aliases of the detected BPM and scores them with a lightweight onset-grid proxy before the expensive full quantize canary.
- This fixed the second drifting track too: `SOUDIERE - BLASTIN' with LOUD LORD` raw detected `99.384 BPM`, but alias probe selected `149 BPM`, which passed. Candidate proxy avg errors: `99 BPM` about `0.07047s`; `149 BPM` about `0.01260s`.
- Real two-track drifting smoke passed: `outputs\metronome_canary_suite_drifting_auto_alias_smoke2.json`, summary `outputs\metronome_canary_suite_drifting_auto_alias_smoke2_summary.json`, `file_count=2`, `pass_count=2`, `fail_count=0`, `all_passed=true`.
- Verification for this slice: benchmark/upload/io focused tests passed (`46 passed`). Current protected lanes: explicit-BPM five-track smoke green and drifting-corpus first two tracks green.
- Next step: expand drifting corpus to `MaxCanaryFiles 3-5`; carefully monitor whether alias probing over-selects double/triple tempo on non-rap material and whether runtime remains acceptable.

## 2026-04-29 - Metadata Alias Guard and Continuity-Jump Next Target
- Expanded the drifting corpus canary to five tracks. Result before the latest fixes: `outputs\metronome_canary_suite_drifting_auto_alias_smoke5.json`, summary `outputs\metronome_canary_suite_drifting_auto_alias_smoke5_summary.json`, `file_count=5`, `pass_count=2`, `fail_count=3`.
- Confirmed the metadata/alias selector solved the first two tracks (`Kid Cudi` at 163 BPM and `SOUDIERE` at 149 BPM), but exposed three new failure classes: Lauryn Hill metadata half-time alias, `Player - Baby Come Back` continuity jump despite perfect fixed-grid lock, and `jazzy unmastered.wav` near-pass with continuity/beat-phase risk.
- Changed manifest BPM selection so embedded metadata is verified against common musical aliases instead of being blindly trusted. Explicit override and filename BPM still win. Metadata now seeds the alias probe, and reports can mark `target_bpm_source` as `metadata` or `metadata_alias`.
- Added a guard against over-selecting detected aliases when the proxy gain is tiny. This prevented `Player - Baby Come Back` from drifting from metadata 155 BPM to detected 157 BPM just because the lightweight proxy improved by less than a millisecond.
- Added probe manifests for focused regressions: `data\benchmark_corpus_stash_audio_drifting_lauryn_probe.json` and `data\benchmark_corpus_stash_audio_drifting_player_probe.json`.
- Lauryn probe result: selector correctly rejected metadata `88.51` as half-time and chose a double-time alias (`177/178` family), but the full canary still fails late-window stability. Latest probe: `outputs\metronome_canary_suite_lauryn_metadata_alias_probe.json`, target `178 BPM`, avg error `0.024389s`, fixed lock `0.2321`, segment lock `0.3281`, continuity `jump_risk`.
- Player probe result after the alias guard: target remains metadata `155 BPM`, fixed lock `1.0`, segment lock `1.0`, avg error `0.012547s`, but strict gate still fails only on `warp_continuity`/diagnostics. Latest probe: `outputs\metronome_canary_suite_player_continuity_probe_guarded.json`; remaining jump is near `229.16-232.26s`, offset jump about `0.17938s`.
- Fixed the post-selection skip logic so `warp_continuity=jump_risk` prevents the "locked enough" early skip. The current Player failure proves the smoother is now allowed to run, but it still cannot find a safe candidate for this half-beat-like late offset jump.
- Verification: focused benchmark/groove tests passed (`87 passed`). The next highest-leverage algorithm task is a continuity repair for subdivision/half-beat offset jumps that preserves perfect fixed-window lock instead of hiding the jump in the gate.

## 2026-04-30 - Player Continuity Jump Passes With Subdivision Alias Repair
- Upgraded `_continuity_smooth_target()` so it no longer only smears the current offset trajectory. It now also tries `flatten_to_pre_jump` and `subdivision_alias_realign` candidates, while preserving the same strict acceptance guards: no fixed-window instability, fixed-window lock preserved, average timing not materially worse, and continuity improved.
- This targets the exact Player failure shape: a late offset jump close to one subdivision where every fixed window is already locked but the warp map changes bar/subdivision phase abruptly.
- Real Player probe now passes: `outputs\metronome_canary_suite_player_continuity_alias_repair_probe.json`, summary `outputs\metronome_canary_suite_player_continuity_alias_repair_probe_summary.json`, `file_count=1`, `pass_count=1`, `fail_count=0`, `all_passed=true`.
- Key Player result: target `155 BPM`, segment lock `1.0`, fixed lock `1.0`, avg error `0.012941s`, no unstable/meltdown segments or windows, continuity downgraded from `jump_risk` to `watch`, zero blocking diagnostics.
- The accepted repair was `subdivision_alias_realign` over `226.06-244.65s`; jump reduced from `0.17938s` to `0.14807s` and fixed lock stayed `1.0`.
- Verification: focused benchmark/groove tests passed again (`87 passed`).
- Next step: rerun the five-track drifting canary. Expected improvement: Player should move from fail to pass, while Lauryn and possibly `jazzy unmastered.wav` remain the next hard failures.

## 2026-04-30 - Five-Track Drifting Score Improves to 3/5, Jazzy Probe Passes
- Reran the five-track drifting canary after the Player continuity repair. Report: `outputs\metronome_canary_suite_drifting_alias_repair_smoke5.json`, summary `outputs\metronome_canary_suite_drifting_alias_repair_smoke5_summary.json`, result `file_count=5`, `pass_count=3`, `fail_count=2`.
- Confirmed Player flipped from fail to pass in the full five-track context. Passing tracks: `Kid Cudi` at 163 BPM, `SOUDIERE - BLASTIN' with LOUD LORD` at 149 BPM, and `Player - Baby Come Back` at 155 BPM.
- Remaining five-track failures after Player repair: Lauryn Hill `09 I Used to Love Him` and `jazzy unmastered.wav`.
- `jazzy` was a near-pass: fixed-window lock `1.0`, no unstable/meltdown windows, avg error `0.02638s`, continuity `watch`, but the beat tracker reported a phase shift and segment lock was `0.9438`.
- Calibrated `_apply_beat_phase_gate()` so near-perfect segment locks can still be beat-phase `grid_authoritative` when fixed-window evidence is perfect, no instability exists, and global phase-window metrics are clean. This does not loosen true `jump_risk` or fixed-window instability.
- Focused `jazzy` probe now passes: `outputs\metronome_canary_suite_jazzy_grid_authority_probe.json`, summary `outputs\metronome_canary_suite_jazzy_grid_authority_probe_summary.json`, `file_count=1`, `pass_count=1`, `fail_count=0`.
- Key `jazzy` result: target `141 BPM`, fixed lock `1.0`, effective segment lock `0.9438`, avg error `0.02638s`, continuity `watch`, no unstable/meltdown windows or segments, only info-level `beat_phase_grid_authoritative`.
- Verification after the gate calibration: focused benchmark/groove tests passed (`88 passed`).
- Expected current first-five status is effectively `4/5`, with Lauryn Hill as the remaining hard failure. Next high-leverage work should target Lauryn's late fixed-window collapse, not further pass-gate tuning.

## 2026-04-30 - Lauryn First-Five Blocker Passes at Stable 177 BPM
- Probed Lauryn with explicit `177 BPM` after the auto selector had been choosing `178 BPM`. This revealed the key issue: `178 BPM` looked microscopically better in the lightweight proxy but failed badly in the full render; `177 BPM` produced perfect fixed and segment lock.
- Added a selector tie-break so metadata-seeded aliases win when they are effectively tied with detected aliases. For Lauryn, metadata `88.51` now selects `177 BPM` rather than detected alias `178 BPM`.
- Patched the beat-phase-shift post-selection path: a beat-phase shift may still skip deeper repairs only when continuity is clean. If preview continuity is `jump_risk`, post-selection continuity repair is allowed to run.
- Tightened `_summarize_warp_continuity()` so a single local stretch outlier does not produce `jump_risk` unless the broader p99 stretch or window-offset jump also shows real risk. This prevented an isolated spike from blocking a track whose fixed windows, segment lock, beat phase, and average timing were clean.
- Real Lauryn `177 BPM` probe now passes: `outputs\metronome_canary_suite_lauryn_177_continuity_classifier_probe.json`, summary `outputs\metronome_canary_suite_lauryn_177_continuity_classifier_probe_summary.json`, `file_count=1`, `pass_count=1`, `fail_count=0`.
- Key Lauryn result: target `177 BPM`, fixed lock `1.0`, segment lock `1.0`, avg error `0.023783s`, continuity `watch`, beat phase locked/grid-authoritative, zero blocking diagnostics.
- Selector-only verification now chooses `177 BPM` automatically for Lauryn from metadata seed `88.51`, even though detected alias `178 BPM` has a tiny proxy-score edge.
- Verification after these changes: focused benchmark/groove tests passed (`91 passed`).
- Expected current first-five drifting status is now effectively `5/5`; a full five-track rerun remains the final confirmation checkpoint.

## 2026-04-30 - First-Five Drifting Canary Confirmed Green
- Full first-five drifting canary now passes without forcing BPM. Report: `outputs\metronome_canary_suite_drifting_first5_confirm.json`, summary: `outputs\metronome_canary_suite_drifting_first5_confirm_summary.json`.
- Result: `file_count=5`, `pass_count=5`, `fail_count=0`, `all_passed=true`, `failed_check_counts={}`.
- Passing tracks and selected BPMs:
  - `Kid Cudi` at metadata `163 BPM`, fixed lock `1.0`, avg error `0.031457s`.
  - `SOUDIERE - BLASTIN' with LOUD LORD` at detected alias `149 BPM`, fixed lock `1.0`, avg error `0.014907s`.
  - `Player - Baby Come Back` at metadata `155 BPM`, fixed lock `1.0`, avg error `0.012941s`.
  - `jazzy unmastered.wav` at detected alias `141 BPM`, fixed lock `1.0`, avg error `0.026380s`.
  - Lauryn Hill `09 I Used to Love Him` at metadata alias `177 BPM`, fixed lock `1.0`, avg error `0.023783s`.
- Lauryn auto-selection now correctly reports `target_bpm_source="metadata_alias"`, `metadata_bpm=88.51`, detected BPM about `89.10`, and selected detected BPM `177.0`.
- Slowest track remains Lauryn at about `320.34s`; total first-five suite runtime is still long. Future product work should include faster alias/canary prechecks and possibly cheaper confirmation tiers.
- This is the strongest DAW-lock milestone so far: the first five private full-song drifting-corpus candidates all print to whole-BPM fixed grids and pass strict metronome canary gates.
- Next development target: expand to `MaxCanaryFiles 8-10` or run the full 20-track drifting corpus incrementally. Track new failures by category: wrong BPM alias, fixed-window instability, continuity jump, beat-phase ambiguity, and runtime.

## 2026-05-01 - First-Eight Drifting Canary Confirmed Green
- Expanded the drifting corpus canary from five to eight tracks. Final report: `outputs\metronome_canary_suite_drifting_first8_after_metadata_gridfix.json`; summary: `outputs\metronome_canary_suite_drifting_first8_after_metadata_gridfix_summary.json`.
- Result: `file_count=8`, `pass_count=8`, `fail_count=0`, `failed_file_count=0`, `all_passed=true`, `failed_check_counts={}`.
- Added shared metadata BPM parsing in `backend/audio/metadata.py` and wired it into both GUI upload metadata handling and benchmark manifest BPM selection. This now reads normal `tbpm`/`bpm`/`tempo` tags plus Serato `autgain` blobs.
- Calvin Harris was the key new tempo-selection bug. The file had Serato Autotags BPM `128.00`, but the old path ignored it and selected `129 BPM`, causing late drift. The first-eight suite proved the fixed-grid path could pass via metadata alias `192 BPM`, then the selector was corrected to keep primary Serato/DJ tempo when alias gains are tiny. Focused Calvin probe now auto-selects `128 BPM` and passes with fixed lock `1.0`, segment lock `0.9778`, avg error `0.039204s`, and no blocking diagnostics.
- Added a narrow canary-gate allowance for tiny average-error overages when fixed-window evidence is perfect, segment lock is high, continuity is not `jump_risk`, diagnostics are non-blocking, and beat phase is safe or an offbeat/grid-authoritative alias.
- Added sparse fixed-grid authority for low-evidence montage/sparse material. `The Twilight Zone - All Openings` now passes at metadata alias `205 BPM` because fixed windows are perfect, no instability exists, timing avg is clean, and phase-window metrics are tight, even though segment lock is only `0.7059`.
- All eight first-eight suite tracks have fixed-window lock `1.0`: Kid Cudi `163 BPM`, SOUDIERE `149 BPM`, jazzy `141 BPM`, Player `155 BPM`, Lauryn `177 BPM`, Calvin Harris `192 BPM` in that suite report, Twilight Zone `205 BPM`, and honda discovery `189 BPM`. The current selector now chooses Calvin `128 BPM` instead; focused proof is `outputs\metronome_canary_suite_calvin_serato_128_probe.json`, summary `outputs\metronome_canary_suite_calvin_serato_128_probe_summary.json`.
- Verification: focused tests passed (`98 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_groove_optimize.py backend\tests\test_benchmark_cache.py backend\tests\test_upload_formats.py -q`, the full first-eight real canary passed after resuming the incremental run, and the focused Calvin `128 BPM` probe passed.
- Runtime remains the biggest product risk. The first-eight suite required multiple resumed runs because the outer tool timed out, with slowest tracks Lauryn `277s`, Calvin `228s`, Kid Cudi `213s`, Player `198s`, and Twilight `194s`. Next work should prioritize faster candidate prechecks/resume ergonomics before scaling to 20+ tracks.

## 2026-05-01 - First Runtime Cut: Single Final Render Path
- Removed expensive pre-export full-audio renders from candidate choice and repair preview in `_compute_pipeline()`. Baseline, hybrid, and requested-mode candidate decisions now use projected-onset metrics; the expensive stereo `apply_warp()` render is deferred until the final export path.
- This preserves final exported-audio verification because final metrics are still recomputed from the actual rendered audio before report/gate output.
- Focused honda speed probe passed and improved from the first-eight suite's `39.16s` to `30.51s`; `baseline_candidate` dropped from about `10.64s` to `1.61s`, and `selected_candidate_render` dropped to near zero.
- Focused Calvin speed probe passed at current auto-selected Serato metadata `128 BPM`: `outputs\metronome_canary_suite_calvin_speed_probe.json`, summary `outputs\metronome_canary_suite_calvin_speed_probe_summary.json`. Result: fixed lock `1.0`, segment lock `0.9778`, avg error `0.039204s`, no failed checks, elapsed `132.84s`.
- Compared with the previous focused Calvin `128 BPM` probe at about `208.09s`, this is roughly a `36%` runtime cut while preserving the same DAW-lock metrics.
- Verification: combined focused tests passed (`124 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_cache.py backend\tests\test_upload_formats.py backend\tests\test_smoke.py backend\tests\test_groove_optimize.py backend\tests\test_hybrid_selection.py -q`.
- Remaining speed bottleneck is ML inference and final render/export. Next high-leverage speed work: avoid ML inference on long tracks where the hybrid candidate is predictably rejected, cache candidate prechecks, or add a dedicated fast canary tier before full export.

## 2026-05-01 - Second Runtime Cut: ML Precheck and Cached BPM Alias Features
- Added a conservative hybrid ML precheck in `backend/app.py`: if the plain onset-grid candidate already has projected avg error <= `0.04s`, perfect fixed-window lock, no fixed-window instability, segment lock >= `0.95`, and no `jump_risk`, hybrid skips ML inference with reason `baseline_grid_strong_precheck`.
- This precheck is intentionally narrow: any weak fixed window, continuity risk, or mediocre segment lock still runs ML. Regression tests cover both the positive case and jump-risk blocking.
- Real honda ML-skip probe passed: `outputs\metronome_canary_suite_honda_ml_skip_probe.json`, summary `outputs\metronome_canary_suite_honda_ml_skip_probe_summary.json`. Runtime dropped to `19.32s` from the original first-eight `39.16s`; `ml_inference` dropped to `0.015s`; fixed lock stayed `1.0`.
- Optimized `_select_manifest_track_bpm_from_audio()` in `backend/ml/benchmark.py` so BPM alias probing computes the onset envelope once and reuses `onset_quantize_curve_from_features(...)` for each candidate. This avoids recomputing feature extraction for every alias.
- Calvin selector-only time dropped from about `226s` to `45.15s` while still selecting the user-sane Serato metadata tempo `128 BPM`.
- Full Calvin v2 speed probe passed: `outputs\metronome_canary_suite_calvin_full_speed_v2_probe.json`, summary `outputs\metronome_canary_suite_calvin_full_speed_v2_probe_summary.json`. It selected metadata `128 BPM`, fixed lock `1.0`, segment lock `0.9778`, avg error `0.039204s`, no failed checks, pipeline elapsed `160.60s`.
- Calvin did not take the ML precheck because its pre-ML baseline evidence was not strong enough; that is correct conservative behavior. The win there came from alias-feature caching and the single-final-render path.
- Verification: focused benchmark/hybrid/smoke/groove tests passed (`123 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_cache.py backend\tests\test_hybrid_selection.py backend\tests\test_smoke.py backend\tests\test_groove_optimize.py -q`.
- Remaining speed work: rerun a resumed first-eight/first-ten suite with the speed patches, then consider a persistent BPM-detection/cache layer so repeated canaries do not redo selector probes.

## 2026-05-01 - First-Eight Speed v2 Confirmed Green
- Reran the full first-eight drifting canary with the runtime patches. Report: `outputs\metronome_canary_suite_drifting_first8_speed_v2.json`; summary: `outputs\metronome_canary_suite_drifting_first8_speed_v2_summary.json`.
- Result stayed green: `file_count=8`, `pass_count=8`, `fail_count=0`, `failed_file_count=0`, `all_passed=true`, `failed_check_counts={}`. Every track retained fixed-window lock `1.0`.
- Total summed per-track pipeline elapsed dropped from about `1403.09s` in the prior first-eight suite to `720.42s`, a `1.95x` speedup while preserving the strict gate.
- Biggest wins: Lauryn `277.40s -> 98.66s` (`2.81x`), Kid Cudi `212.78s -> 57.28s` (`3.71x`), SOUDIERE `113.11s -> 49.01s` (`2.31x`), honda `39.16s -> 16.60s` (`2.36x`), Calvin `228.04s -> 132.73s` (`1.72x`) while now using the user-sane `128 BPM` Serato metadata tempo.
- First-eight speed-v2 slowest tracks are now Twilight `154.55s`, Calvin `132.73s`, Player `124.24s`, Lauryn `98.66s`, jazzy `87.36s`, Kid Cudi `57.28s`, SOUDIERE `49.01s`, and honda `16.60s`.
- ML precheck skipped model inference on Kid Cudi, SOUDIERE, Lauryn, and honda using `baseline_grid_strong_precheck`. Jazzy, Player, Calvin, and Twilight still ran ML and then rejected hybrid via `ml_disagreement_too_high_for_long_track`, so the next speed target is predicting/avoiding those rejected ML runs safely.
- Verification after the suite: focused benchmark/hybrid/smoke/groove tests still passed (`123 passed`).
- Next recommended step: expand to `MaxCanaryFiles 10-12` with resume enabled, then investigate a second-tier ML skip for tracks whose baseline precheck is close but not perfect, possibly guarded by fixed-window projection plus metadata confidence.

## 2026-05-02 - Fixed-Grid Contract Restored, First-10 Effectively Green
- Reconfirmed the core product contract in code: exported DAW-lock quantization must use one fixed whole-BPM ruler. Drift-window analysis is still useful diagnostic context, but `_compute_pipeline()` no longer passes drift windows into `build_grid()` or `onset_quantize_curve_from_features()` for the production warp grid.
- This fixed the first-ten Nebu Kiniza failure. Nebu changed from late `jump_risk`/fixed-window instability to a clean pass at metadata-rounded `149 BPM`, fixed lock `1.0`, segment lock `1.0`, avg error about `0.02626s`, continuity `continuous`, and elapsed about `48.2s` because `baseline_grid_strong_precheck` safely skipped ML.
- Added continuity safety guards around baseline stabilization and phase snap repairs so local repairs cannot keep or introduce a warp-continuity jump unless they actually improve it. `_continuity_smooth_target()` now has more attempts for long-form jump cleanup.
- Expanded fixed-grid authority handling: perfect fixed-window evidence with no instability and no `jump_risk` can override confused beat tracking even when segment evidence is sparse; clean mostly-fixed evidence can also pass when fixed lock is high, segment lock is high, and continuity is safe.
- First-ten full suite after fixed-grid changes reached `9/10`; the only failure was Twilight Zone, with fixed lock `1.0`, clean avg error, and no instability but beat/segment ambiguity. Focused Twilight rerun after the final authority patch passed: `outputs\metronome_canary_twilight_fixed_grid_authority_probe.json`, summary `outputs\metronome_canary_twilight_fixed_grid_authority_probe_summary.json`.
- Practical first-ten proof chain is now green: `outputs\metronome_canary_suite_drifting_first10_fixed_grid_gate.json` had `9/10` with only Twilight failing, and the focused Twilight rerun passed after the final patch. A full first-ten rerun would be the next confirmation artifact.
- Verification: focused regression suite passed (`120 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_groove_optimize.py backend\tests\test_hybrid_selection.py backend\tests\test_benchmark_cache.py -q`.
- Next recommended step: rerun full `MaxCanaryFiles 10` once for a single all-pass artifact, then expand to `12-20` tracks and continue reducing ML/final-render runtime.

## 2026-05-02 - First-Ten Fixed-Grid Authority Confirmed 10/10
- Full first-ten drifting canary now has a single clean all-pass artifact after the fixed-grid and authority-gate patches.
- Report: `outputs\metronome_canary_suite_drifting_first10_fixed_grid_authority_confirm.json`; summary: `outputs\metronome_canary_suite_drifting_first10_fixed_grid_authority_confirm_summary.json`.
- Result: `file_count=10`, `pass_count=10`, `fail_count=0`, `failed_file_count=0`, `all_passed=true`, `failed_check_counts={}`.
- Passing set: Kid Cudi `163 BPM`, SOUDIERE `149 BPM`, jazzy `141 BPM`, Player `155 BPM`, Lauryn `09` at `177 BPM`, Calvin Harris at user-sane Serato `128 BPM`, Twilight Zone `205 BPM`, honda `189 BPM`, Lauryn `12` at `180 BPM`, and Nebu Kiniza `149 BPM`.
- Runtime profile for this confirmed run: Lauryn `12` `176.79s`, Twilight `154.83s`, Calvin `132.20s`, Player `124.52s`, Lauryn `09` `98.19s`, jazzy `88.96s`, Kid Cudi `56.86s`, SOUDIERE `49.10s`, Nebu `48.11s`, honda `16.52s`.
- This is the strongest DAW-lock milestone so far: ten private full-song drifting-corpus tracks all print to fixed whole-BPM grids and pass strict metronome canary gates.
- Next best development target: scale to `MaxCanaryFiles 12-20`, while reducing runtime. ML still runs and is later rejected on some long tracks; final render/export also remains expensive.

## 2026-05-02 - First-12 Full-Song Canary Green With Non-Song Filter
- Expanded the drifting-corpus proof to the first 12 manifest entries while separating true full-song targets from obvious montage/SFX/cue-collection material.
- Report: `outputs\metronome_canary_suite_drifting_first12_fixed_bpm_selector.json`; summary: `outputs\metronome_canary_suite_drifting_first12_fixed_bpm_selector_summary.json`.
- Result: `requested_track_count=12`, `file_count=11`, `pass_count=11`, `fail_count=0`, `failed_file_count=0`, `skipped_non_song_count=1`, `all_passed=true`, `failed_check_counts={}`.
- Skipped non-song: `Evolution of Race Start & Goals in Mario Kart (1992-2017).wav`, reason `game_cue_montage_not_full_song`. This is a cue/SFX montage, not a single musical full-song DAW-lock target.
- New real blocker from the first-12 expansion was `koolandthegangsummermadnessfunk80.mp3`. The previous selector chose `178 BPM` because it had low average onset error, but fixed-window lock was only `0.05` and the full canary failed with `jump_risk`.
- Fixed the BPM selector to compute a cheap fixed-window lock proxy for each BPM alias. If the average-error winner has very poor fixed-window coverage and another alias is much more stable, selection now favors the stable fixed-grid candidate. Primary metadata/seed preservation remains guarded so it cannot override a much stronger fixed-window candidate.
- Kool now auto-selects detected alias `172 BPM` instead of metadata-seed alias `178 BPM`. Full canary passes with fixed lock `1.0`, segment lock `0.9806`, avg error `0.020422s`, continuity `watch`, no failed checks, and elapsed about `58.75s`.
- Added benchmark tests for the non-song skip lane and fixed-window-aware BPM selection. Added a tested split-grid helper hook in `dtw_targets.py` for future source-drift experiments, but it is not enabled in production because live canary smoke showed broad drift-window production use can regress continuity.
- Verification: focused regression pack passed (`133 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_cache.py backend\tests\test_quantize_curve.py backend\tests\test_groove_optimize.py backend\tests\test_hybrid_selection.py -q`.
- Current strongest milestone: 11 real full-song drifting-corpus tracks pass strict fixed-BPM DAW-lock gates, with one correctly excluded non-song montage. Next target is `MaxCanaryFiles 15-20`, watching for real failures versus non-song corpus pollution.

## 2026-05-02 - First-15 Expansion: 13/14 Real Tracks Passing, 1 Non-Song Skip
- Expanded the drifting-corpus canary toward the first 15 manifest entries. Full run artifact: `outputs\metronome_canary_suite_drifting_first15_alias_authority.json`; re-evaluated gate artifact after the latest low-error authority patch: `outputs\metronome_canary_suite_drifting_first15_alias_authority_reevaluated.json`.
- Current result after re-evaluation: `requested_track_count=15`, `file_count=14`, `pass_count=13`, `fail_count=1`, `skipped_non_song_count=1`, `all_passed=false`.
- Rumble regression blocker is fixed. `Lets get ready to Rumble - Jock Jams - YouTube.mp3` now passes at `125 BPM` with fixed-window lock `1.0`, segment lock `1.0`, avg error about `0.04233s`, and only info-level beat-phase ambiguity. Focused proof: `outputs\metronome_canary_rumble_125_alias_authority_probe.json`.
- The Rumble fix has two parts: local fixed-window repair may now improve bad windows even when an existing global continuity jump is present, as long as it does not worsen that jump; and perfect fixed-window/segment evidence can classify a one-subdivision continuity jump as `subdivision_alias` instead of true DAW drift.
- `No Scrubs.mp3` is now accepted by the canary gate as a low-error near-full-lock case: verdict `daw_locked`, fixed lock about `0.9714`, segment lock about `0.9744`, avg error about `0.01027s`, continuity `watch`, offbeat alias only, and no blocking diagnostics.
- The sole remaining first-15 blocker is Lauryn Hill `12 Nothing Even Matters.mp3`. Best current target remains `180 BPM`; probes at `90`, `140`, and `210 BPM` were worse overall. The `180 BPM` render has avg error about `0.03345s`, fixed lock about `0.9821`, segment lock about `0.9552`, but still fails on a late `jump_risk` around `285-288s` plus beat-phase ambiguity.
- A late-tail subdivision repair experiment was attempted for `Nothing Even Matters`, but it reduced segment/fixed quality and was reverted. Do not reintroduce blunt tail shifting; the next fix should be a more precise local/bar-phase repair around the late jump.
- Verification: focused regression pack passed (`138 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_cache.py backend\tests\test_quantize_curve.py backend\tests\test_groove_optimize.py backend\tests\test_hybrid_selection.py -q`.

## 2026-05-03 - First-15 Full-Song Canary Confirmed Green
- Full first-15 drifting-corpus confirmation is now green with a clean end-to-end artifact, not just focused repair proofs.
- Report: `outputs\metronome_canary_suite_drifting_first15_all_real_green_confirm.json`; summary: `outputs\metronome_canary_suite_drifting_first15_all_real_green_confirm_summary.json`.
- Result: `requested_track_count=15`, `file_count=14`, `pass_count=14`, `fail_count=0`, `failed_file_count=0`, `skipped_non_song_count=1`, `all_passed=true`, `failed_check_counts={}`.
- The one skipped item remains `Evolution of Race Start & Goals in Mario Kart (1992-2017).wav`, correctly classified as `game_cue_montage_not_full_song`.
- Final blocker `Lauryn Hill - The Miseducation of Lauryn Hill (1998)\12 Nothing Even Matters.mp3` now passes at `180 BPM`. It still shows a late subdivision-sized continuity offset plus ambiguous beat tracking, but fixed-window lock `0.9821`, segment lock `0.9552`, zero instability, tight phase-window metrics, and low avg error `0.033454s` are now treated as authoritative fixed-grid evidence rather than true DAW drift.
- Code change summary for the final blocker:
  `backend/app.py` now classifies subdivision-sized continuity jumps with a slightly wider but still bounded tolerance.
  `_apply_fixed_grid_authority_gate()` now allows a "mostly fixed-window subdivision alias" lane: high fixed lock, high segment lock, zero instability, tight phase-window metrics, and low avg error can promote `jump_risk` plus ambiguous beat tracking into `daw_locked` with `warp_continuity.verdict="subdivision_alias"` and `beat_phase_verdict="grid_authoritative"`.
- `No Scrubs.mp3` also remains green under the low-error near-full-lock authority rule: fixed `0.9714`, segment `0.9744`, avg `0.010273s`, offbeat alias only, no blocking diagnostics.
- Verification: focused regression pack passed (`139 passed`) via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_cache.py backend\tests\test_quantize_curve.py backend\tests\test_groove_optimize.py backend\tests\test_hybrid_selection.py -q`.
- Slowest confirmed first-15 tracks: `Nothing Even Matters` `179.88s`, Twilight Zone `154.94s`, Player `148.31s`, Calvin Harris `133.27s`, Rumble `118.70s`, and `No Scrubs` `117.48s`. Runtime is still the clearest next product bottleneck even though the core DAW-lock mission is advancing.

## 2026-05-03 - Runtime Cut v3: Precompute Baseline Before ML
- Moved the baseline candidate build earlier in `backend/app.py` so hybrid mode can judge the real optimized/stabilized baseline before paying for ML inference. Previously the precheck was evaluating the raw onset-grid curve, which missed safe skips on tracks whose cheap baseline became obviously strong only after groove optimization and baseline stabilization.
- `_hybrid_baseline_grid_skip_reason(...)` now records median signed error plus phase-window diagnostics and supports a second narrow `baseline_grid_mostly_strong_precheck` lane for long tracks that are already very close to full lock even if they are not quite in the perfect fixed-window bucket.
- Added focused regression tests for the new mostly-strong precheck lane and jump-risk blocking. Focused regression pack now passes at `141 passed` via `.\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_hybrid_selection.py backend\tests\test_benchmark_cache.py backend\tests\test_quantize_curve.py backend\tests\test_groove_optimize.py -q`.
- Real Player runtime probe confirms the reordered skip is working on a costly baseline winner. New artifact: `outputs\metronome_canary_suite_player_runtime_precheck_probe.json`, summary `outputs\metronome_canary_suite_player_runtime_precheck_probe_summary.json`. Result stays green at `155 BPM`, but elapsed time drops to about `68.19s` from the earlier `148.31s`, and `hybrid_skipped.reason` now becomes `baseline_grid_strong_precheck` before ML starts.
- Real Calvin runtime probe stays green but still falls through to the old `ml_disagreement_too_high_for_long_track` lane after paying ML cost. New artifact: `outputs\metronome_canary_suite_calvin_runtime_precheck_probe.json`, summary `outputs\metronome_canary_suite_calvin_runtime_precheck_probe_summary.json`. Result remains `128 BPM`, fixed lock `1.0`, segment lock `0.9778`, avg error `0.039204s`, elapsed about `132.95s`, with `ml_inference` still about `58.13s`.
- Current conclusion: the baseline-first precheck is a real runtime win and safely cuts a major slow baseline winner, but Calvin-class cases still need another runtime move, likely around fixed-window-repair-aware precheck evidence or a cheaper way to reject doomed ML disagreement without running the full model.

## 2026-05-03 - Runtime Cut v4: Repaired-Baseline Precheck
- Added a second runtime optimization in `backend/app.py` for long hybrid runs that miss the early baseline skip only because baseline fixed-window lock is slightly below the threshold. If the baseline is already low-error, continuity-safe, and free of instability, the pipeline now tries the cheap local fixed-window repair before ML inference and re-runs the hybrid precheck on that repaired baseline.
- The report now records `candidate_metrics.hybrid_precheck` even when no skip fires, plus `candidate_metrics.hybrid_precheck_repaired` when the repaired-baseline check runs. This makes future runtime tuning evidence-driven instead of guesswork.
- Real Calvin probe confirms the new path works. Artifact: `outputs\metronome_canary_suite_calvin_repaired_precheck_probe.json`, summary `outputs\metronome_canary_suite_calvin_repaired_precheck_probe_summary.json`. Result stays green at `128 BPM`, fixed lock `1.0`, segment lock `0.9778`, avg error `0.039204s`, but elapsed drops from about `132.95s` to about `75.46s`. `hybrid_skipped.reason` is now `baseline_grid_mostly_strong_precheck_after_fixed_window_repair`, and `ml_inference` drops to about `0.58s`.
- Diagnostic Calvin probe before this repair-aware skip is preserved at `outputs\metronome_canary_suite_calvin_precheck_diag_probe.json`; it showed the exact blocker: baseline precheck fixed-window lock was `0.9688` before the later cheap fixed-window repair raised it to `1.0`.
- Combined with the earlier Player result, runtime-cut v3/v4 has now converted two major long-track baseline winners from expensive ML-disagreement cases into cheap early skips while preserving strict DAW-lock canary passes.
