from __future__ import annotations

import numpy as np


def _nearest_error(event_times: np.ndarray, grid_times: np.ndarray) -> np.ndarray:
    if len(event_times) == 0 or len(grid_times) == 0:
        return np.array([], dtype=np.float32)
    idx = np.searchsorted(grid_times, event_times)
    idx = np.clip(idx, 0, len(grid_times) - 1)
    prev_idx = np.clip(idx - 1, 0, len(grid_times) - 1)
    d1 = np.abs(event_times - grid_times[idx])
    d0 = np.abs(event_times - grid_times[prev_idx])
    return np.minimum(d0, d1)


def _nearest_signed_error(event_times: np.ndarray, grid_times: np.ndarray) -> np.ndarray:
    if len(event_times) == 0 or len(grid_times) == 0:
        return np.array([], dtype=np.float32)
    idx = np.searchsorted(grid_times, event_times)
    idx = np.clip(idx, 0, len(grid_times) - 1)
    prev_idx = np.clip(idx - 1, 0, len(grid_times) - 1)
    err_next = event_times - grid_times[idx]
    err_prev = event_times - grid_times[prev_idx]
    choose_prev = np.abs(err_prev) <= np.abs(err_next)
    return np.where(choose_prev, err_prev, err_next).astype(np.float32)


def _window_phase_stats(event_times: np.ndarray, signed_errors: np.ndarray, grid_times: np.ndarray) -> tuple[float, float]:
    if len(event_times) < 6 or len(signed_errors) != len(event_times) or len(grid_times) < 2:
        return 0.0, 0.0
    start = float(grid_times[0])
    end = float(grid_times[-1])
    if end - start <= 1e-6:
        return 0.0, 0.0
    window_count = int(np.clip((end - start) / 8.0, 6, 16))
    edges = np.linspace(start, end, window_count + 1, dtype=np.float32)
    medians: list[float] = []
    for left, right in zip(edges[:-1], edges[1:]):
        if right <= left:
            continue
        if right >= end:
            mask = (event_times >= left) & (event_times <= right)
        else:
            mask = (event_times >= left) & (event_times < right)
        if int(np.count_nonzero(mask)) < 3:
            continue
        medians.append(float(np.median(signed_errors[mask])))
    if not medians:
        return 0.0, 0.0
    medians_arr = np.asarray(medians, dtype=np.float32)
    return float(np.max(np.abs(medians_arr))), float(np.max(medians_arr) - np.min(medians_arr))


def timing_metrics(onset_times_before: np.ndarray, onset_times_after: np.ndarray, grid_times: np.ndarray) -> dict:
    err_before = _nearest_error(onset_times_before, grid_times)
    err_after = _nearest_error(onset_times_after, grid_times)
    signed_after = _nearest_signed_error(onset_times_after, grid_times)

    avg_before = float(err_before.mean()) if len(err_before) else 0.0
    avg_after = float(err_after.mean()) if len(err_after) else 0.0

    improvement = avg_before - avg_after
    pct = (improvement / avg_before * 100.0) if avg_before > 1e-8 else 0.0
    phase_abs_max, phase_span = _window_phase_stats(
        np.asarray(onset_times_after, dtype=np.float32),
        signed_after,
        np.asarray(grid_times, dtype=np.float32),
    )

    return {
        "avg_abs_error_before_sec": avg_before,
        "avg_abs_error_after_sec": avg_after,
        "improvement_sec": float(improvement),
        "improvement_pct": float(pct),
        "median_signed_error_after_sec": float(np.median(signed_after)) if len(signed_after) else 0.0,
        "phase_window_abs_max_after_sec": phase_abs_max,
        "phase_window_span_after_sec": phase_span,
        "num_events_before": int(len(onset_times_before)),
        "num_events_after": int(len(onset_times_after)),
    }
