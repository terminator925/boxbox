from __future__ import annotations

import numpy as np

from backend.audio.drift import estimate_tempo_drift
from backend.audio.dtw_targets import build_grid


def test_estimate_tempo_drift_reports_low_drift_for_regular_events():
    events = [
        {"time_sec": 0.0, "confidence": 0.9},
        {"time_sec": 0.5, "confidence": 0.9},
        {"time_sec": 1.0, "confidence": 0.9},
        {"time_sec": 1.5, "confidence": 0.9},
        {"time_sec": 2.0, "confidence": 0.9},
    ]

    drift = estimate_tempo_drift(events, target_bpm=120.0)

    assert drift["summary"]["window_count"] >= 3
    assert drift["summary"]["median_local_bpm"] == 120.0
    assert drift["summary"]["mean_abs_drift_bpm"] == 0.0


def test_estimate_tempo_drift_captures_slowdown():
    events = [
        {"time_sec": 0.0, "confidence": 0.8},
        {"time_sec": 0.5, "confidence": 0.8},
        {"time_sec": 1.05, "confidence": 0.8},
        {"time_sec": 1.7, "confidence": 0.8},
        {"time_sec": 2.45, "confidence": 0.8},
    ]

    drift = estimate_tempo_drift(events, target_bpm=120.0)

    assert drift["summary"]["window_count"] >= 3
    assert drift["summary"]["median_local_bpm"] < 120.0
    assert drift["summary"]["mean_abs_drift_bpm"] > 0.0


def test_build_grid_can_follow_conservative_tempo_drift():
    drift_windows = [
        {"time_sec": 0.5, "local_bpm": 120.0, "confidence": 0.9},
        {"time_sec": 1.0, "local_bpm": 118.0, "confidence": 0.9},
        {"time_sec": 1.5, "local_bpm": 112.0, "confidence": 0.9},
        {"time_sec": 2.0, "local_bpm": 108.0, "confidence": 0.9},
        {"time_sec": 2.5, "local_bpm": 110.0, "confidence": 0.9},
        {"time_sec": 3.0, "local_bpm": 116.0, "confidence": 0.9},
    ]

    fixed_grid = build_grid(4.0, 120.0, 4)
    adaptive_grid = build_grid(4.0, 120.0, 4, drift_windows=drift_windows)

    fixed_steps = np.diff(fixed_grid)
    adaptive_steps = np.diff(adaptive_grid)

    assert len(adaptive_grid) > 4
    assert not np.allclose(adaptive_steps[: min(len(fixed_steps), len(adaptive_steps))], fixed_steps[: min(len(fixed_steps), len(adaptive_steps))])
    assert float(adaptive_steps.max()) > float(fixed_steps.max())
    assert float(adaptive_steps.min()) < float(fixed_steps.max())


def test_build_grid_rejects_half_time_alias_windows():
    drift_windows = [
        {"time_sec": 0.5, "local_bpm": 104.0, "confidence": 0.8},
        {"time_sec": 1.0, "local_bpm": 104.0, "confidence": 0.8},
        {"time_sec": 1.5, "local_bpm": 52.0, "confidence": 0.78},
        {"time_sec": 2.0, "local_bpm": 52.0, "confidence": 0.81},
        {"time_sec": 2.5, "local_bpm": 104.0, "confidence": 0.82},
        {"time_sec": 3.0, "local_bpm": 104.0, "confidence": 0.79},
    ]

    fixed_grid = build_grid(4.0, 104.0, 8)
    guarded_grid = build_grid(4.0, 104.0, 8, drift_windows=drift_windows)

    assert np.allclose(guarded_grid, fixed_grid)
