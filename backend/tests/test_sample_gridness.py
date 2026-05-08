from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.ml.sample_gridness import (
    aggregate_window_gridness,
    analyze_manifest_gridness,
    clip_starts_for_track,
    summarize_beat_gridness,
)


def test_summarize_beat_gridness_marks_regular_beats_as_grid_quantized():
    beat_times = np.arange(0.0, 20.0, 0.5, dtype=np.float32)

    summary = summarize_beat_gridness(beat_times)

    assert summary["gridness_class"] == "likely_grid_quantized"
    assert summary["median_bpm"] == 120.0


def test_summarize_beat_gridness_marks_irregular_beats_as_drifting():
    intervals = np.array([0.48, 0.55, 0.44, 0.62, 0.51, 0.71, 0.46, 0.58], dtype=np.float32)
    beat_times = np.concatenate([[0.0], np.cumsum(intervals)])

    summary = summarize_beat_gridness(beat_times)

    assert summary["gridness_class"] == "likely_drifting_unquantized"
    assert summary["p90_abs_tempo_dev_pct"] >= 7.0


def test_analyze_manifest_gridness_reports_failed_audio_without_crashing(tmp_path: Path):
    audio = tmp_path / "not_audio.wav"
    audio.write_bytes(b"x")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [{"path": str(audio), "relative_path": audio.name}]}), encoding="utf-8")

    report = analyze_manifest_gridness(manifest, limit=1, clip_duration=1.0)

    assert report["analyzed_count"] == 1
    assert report["gridness_counts"]["analysis_failed"] == 1


def test_clip_starts_for_track_spreads_windows_across_duration():
    starts = clip_starts_for_track({"duration_sec": 120.0}, clip_duration=30.0, windows=3)

    assert starts == [0.0, 45.0, 90.0]


def test_aggregate_window_gridness_uses_drifting_if_any_window_drifts():
    aggregate = aggregate_window_gridness(
        [
            {"gridness_class": "likely_grid_quantized", "gridness_score": 0.9, "p90_abs_tempo_dev_pct": 1.0, "beat_interval_cv": 0.01, "median_bpm": 120.0},
            {"gridness_class": "likely_drifting_unquantized", "gridness_score": 0.1, "p90_abs_tempo_dev_pct": 10.0, "beat_interval_cv": 0.08, "median_bpm": 118.0},
        ]
    )

    assert aggregate["gridness_class"] == "likely_drifting_unquantized"
    assert aggregate["window_class_counts"]["likely_drifting_unquantized"] == 1
