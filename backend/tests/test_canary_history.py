from __future__ import annotations

import json
import os
from pathlib import Path

from backend.ml.canary_history import summarize_canary_history


def _write_gate(path: Path, *, passed: bool, fixed: float, segment: float, avg_error: float, elapsed: float) -> None:
    path.write_text(
        json.dumps(
            {
                "metronome_canary_gate": {
                    "passed": passed,
                    "failed_checks": [] if passed else ["fixed_locked_ratio"],
                    "failure_summary": "Metronome canary gate passed." if passed else "failed",
                    "observed": {
                        "target_bpm": 104.0,
                        "verdict": "daw_locked" if passed else "drifts_late",
                        "segment_locked_ratio": segment,
                        "fixed_locked_ratio": fixed,
                        "raw_fixed_locked_ratio": fixed,
                        "avg_error_after_sec": avg_error,
                        "elapsed_sec": elapsed,
                        "max_window_offset_jump_sec": 0.0,
                        "runtime_config": {
                            "inference_accelerator": "torch",
                            "inference_candidate_strategy": "core4_adaptive_plus",
                            "hybrid_search_strategy": "core4",
                            "hybrid_verify_top_k": 3,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )


def test_summarize_canary_history_tracks_latest_trend_and_best_lock(tmp_path: Path):
    older = tmp_path / "older_gate.json"
    newer = tmp_path / "newer_gate.json"
    _write_gate(older, passed=False, fixed=0.75, segment=0.8, avg_error=0.05, elapsed=150.0)
    _write_gate(newer, passed=True, fixed=1.0, segment=1.0, avg_error=0.03, elapsed=100.0)
    os.utime(older, (100.0, 100.0))
    os.utime(newer, (200.0, 200.0))

    summary = summarize_canary_history([older, newer], top_n=2)

    assert summary["file_count"] == 2
    assert summary["input_file_count"] == 2
    assert summary["skipped_non_gate_count"] == 0
    assert summary["pass_count"] == 1
    assert summary["fail_count"] == 1
    assert summary["latest"]["name"] == "newer_gate.json"
    assert summary["previous"]["name"] == "older_gate.json"
    assert summary["trend"]["avg_error_delta_latest_minus_previous_sec"] == -0.020000000000000004
    assert summary["trend"]["elapsed_delta_latest_minus_previous_sec"] == -50.0
    assert summary["trend"]["fixed_lock_delta_latest_minus_previous"] == 0.25
    assert summary["best_lock"][0]["name"] == "newer_gate.json"
    assert summary["fastest"][0]["name"] == "newer_gate.json"


def test_summarize_canary_history_skips_non_gate_canary_reports(tmp_path: Path):
    gate = tmp_path / "canary_gate.json"
    report_only = tmp_path / "canary_report.json"
    _write_gate(gate, passed=True, fixed=1.0, segment=1.0, avg_error=0.03, elapsed=100.0)
    report_only.write_text(json.dumps({"metronome_canary": {"target_bpm": 104.0}}), encoding="utf-8")

    summary = summarize_canary_history([gate, report_only], top_n=2)

    assert summary["input_file_count"] == 2
    assert summary["file_count"] == 1
    assert summary["skipped_non_gate_count"] == 1
    assert summary["latest"]["name"] == "canary_gate.json"
