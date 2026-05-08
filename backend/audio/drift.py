from __future__ import annotations

import numpy as np


def _rolling_median(values: np.ndarray, window: int) -> np.ndarray:
    if len(values) == 0:
        return np.array([], dtype=np.float32)
    half = max(0, window // 2)
    out = np.empty(len(values), dtype=np.float32)
    for idx in range(len(values)):
        start = max(0, idx - half)
        end = min(len(values), idx + half + 1)
        out[idx] = float(np.median(values[start:end]))
    return out


def estimate_tempo_drift(
    events: list[dict[str, object]],
    target_bpm: float,
    *,
    min_interval_sec: float = 0.08,
    max_interval_sec: float = 1.5,
    smoothing_window: int = 5,
) -> dict[str, object]:
    if len(events) < 3:
        return {
            "summary": {
                "window_count": 0,
                "target_bpm": float(target_bpm),
                "median_local_bpm": 0.0,
                "mean_abs_drift_bpm": 0.0,
                "max_abs_drift_bpm": 0.0,
                "mean_abs_drift_pct": 0.0,
            },
            "windows": [],
        }

    times = np.asarray([float(event["time_sec"]) for event in events], dtype=np.float32)
    conf = np.asarray([float(event.get("confidence", 0.0)) for event in events], dtype=np.float32)
    intervals = np.diff(times)
    valid = (intervals >= float(min_interval_sec)) & (intervals <= float(max_interval_sec))
    if not np.any(valid):
        return {
            "summary": {
                "window_count": 0,
                "target_bpm": float(target_bpm),
                "median_local_bpm": 0.0,
                "mean_abs_drift_bpm": 0.0,
                "max_abs_drift_bpm": 0.0,
                "mean_abs_drift_pct": 0.0,
            },
            "windows": [],
        }

    interval_times = (times[:-1] + times[1:]) * 0.5
    interval_conf = (conf[:-1] + conf[1:]) * 0.5
    kept_intervals = intervals[valid]
    kept_times = interval_times[valid]
    kept_conf = interval_conf[valid]
    local_bpm = 60.0 / np.maximum(kept_intervals, 1e-6)
    smoothed_bpm = _rolling_median(local_bpm.astype(np.float32), smoothing_window)
    drift_bpm = smoothed_bpm - float(target_bpm)
    drift_pct = (drift_bpm / max(float(target_bpm), 1e-6)) * 100.0

    windows = [
        {
            "time_sec": round(float(t), 6),
            "local_bpm": round(float(bpm), 6),
            "drift_bpm": round(float(dbpm), 6),
            "drift_pct": round(float(dpct), 6),
            "confidence": round(float(c), 6),
        }
        for t, bpm, dbpm, dpct, c in zip(kept_times, smoothed_bpm, drift_bpm, drift_pct, kept_conf)
    ]
    summary = {
        "window_count": int(len(windows)),
        "target_bpm": float(target_bpm),
        "median_local_bpm": float(np.median(smoothed_bpm)),
        "mean_abs_drift_bpm": float(np.mean(np.abs(drift_bpm))),
        "max_abs_drift_bpm": float(np.max(np.abs(drift_bpm))),
        "mean_abs_drift_pct": float(np.mean(np.abs(drift_pct))),
    }
    return {
        "summary": summary,
        "windows": windows,
    }
