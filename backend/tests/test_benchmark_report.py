from __future__ import annotations

import json
from pathlib import Path

from backend.ml.benchmark_report import summarize_benchmark_report


def test_summarize_benchmark_report_ranks_hardest_and_regressions(tmp_path: Path):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "real_audio_suite": {
                    "summary": {"num_files": 2},
                    "files": [
                        {
                            "audio_path": "a.wav",
                            "baseline": {"avg_abs_error_after_sec": 0.03},
                            "ml": {"avg_abs_error_after_sec": 0.04},
                            "hybrid_effective": {"avg_abs_error_after_sec": 0.05},
                            "manifest_track": {"relative_path": "a.wav"},
                            "processing_timing": {"elapsed_sec": 3.0, "stages": {"ml_infer": 2.25, "feature_extract": 0.5}},
                        },
                        {
                            "audio_path": "b.wav",
                            "baseline": {"avg_abs_error_after_sec": 0.08},
                            "ml": {"avg_abs_error_after_sec": 0.07},
                            "hybrid_effective": {"avg_abs_error_after_sec": 0.02},
                            "manifest_track": {"relative_path": "b.wav"},
                            "processing_timing": {"elapsed_sec": 5.0, "stages": {"load_audio": 1.0, "hybrid_score": 3.5}},
                        },
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    summary = summarize_benchmark_report(report, top_n=2)

    assert summary["file_count"] == 2
    assert summary["hardest_tracks"][0]["relative_path"] == "a.wav"
    assert summary["hybrid_regressions"][0]["relative_path"] == "a.wav"
    assert summary["best_hybrid_improvements"][0]["relative_path"] == "b.wav"
    assert summary["slowest_tracks"][0]["relative_path"] == "b.wav"
    assert summary["slowest_tracks"][0]["slowest_stage"] == "hybrid_score"
    assert summary["stage_timing_summary"][0]["stage"] == "hybrid_score"
    assert summary["stage_timing_summary"][0]["max_sec"] == 3.5
