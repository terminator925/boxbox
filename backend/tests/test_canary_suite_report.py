from __future__ import annotations

import json
from pathlib import Path

from backend.ml.canary_suite_report import summarize_canary_suite_report


def _item(name: str, *, passed: bool, fixed: float, segment: float, avg_error: float, elapsed: float) -> dict:
    failed_checks = [] if passed else ["fixed_locked_ratio", "avg_error_after"]
    return {
        "audio_path": name,
        "target_bpm": 104.0,
        "manifest_track": {"relative_path": name, "target_bpm_source": "filename"},
        "report_path": f"outputs/{name}/report.json",
        "metronome_canary_gate": {
            "passed": passed,
            "failed_checks": failed_checks,
            "failure_summary": "pass" if passed else "fail",
            "observed": {
                "verdict": "daw_locked" if passed else "drifts_late",
                "segment_locked_ratio": segment,
                "fixed_locked_ratio": fixed,
                "raw_fixed_locked_ratio": fixed,
                "avg_error_after_sec": avg_error,
                "elapsed_sec": elapsed,
                "max_window_offset_jump_sec": 0.0,
                "daw_lock_diagnostic_summary": "No DAW-lock diagnostic issues detected.",
            },
        },
    }


def test_summarize_canary_suite_report_ranks_failures_and_slowest(tmp_path: Path):
    report = tmp_path / "suite.json"
    report.write_text(
        json.dumps(
            {
                "metronome_canary_suite": {
                    "summary": {"file_count": 3, "pass_count": 1, "fail_count": 2},
                    "failed_files": [{"audio_path": "missing.wav", "error": "missing_file"}],
                    "files": [
                        _item("good.wav", passed=True, fixed=1.0, segment=1.0, avg_error=0.03, elapsed=80.0),
                        _item("bad_a.wav", passed=False, fixed=0.5, segment=0.7, avg_error=0.08, elapsed=120.0),
                        _item("bad_b.wav", passed=False, fixed=0.75, segment=0.8, avg_error=0.04, elapsed=150.0),
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    summary = summarize_canary_suite_report(report, top_n=2)

    assert summary["file_count"] == 3
    assert summary["pass_count"] == 1
    assert summary["fail_count"] == 2
    assert summary["failed_files"][0]["error"] == "missing_file"
    assert summary["failed_check_counts"] == {"avg_error_after": 2, "fixed_locked_ratio": 2}
    assert summary["hardest_failures"][0]["relative_path"] == "bad_a.wav"
    assert summary["slowest_tracks"][0]["relative_path"] == "bad_b.wav"
    assert summary["best_lock"][0]["relative_path"] == "good.wav"
