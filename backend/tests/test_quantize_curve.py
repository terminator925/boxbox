from __future__ import annotations

import sys
import types

import numpy as np

from backend.audio.dtw_targets import _anchor_threshold_percentile, _beat_grid_index_for_time, _continuity_limited_grid_index, _endpoint_grid_time, _phase_guided_grid_index, build_grid, onset_quantize_curve, onset_quantize_curve_from_features
from backend.audio.onsets import detect_onsets
from backend.audio import warp as warp_module
from backend.audio.warp import apply_warp
from backend.ml.metrics import timing_metrics


def _make_jittered_click_track(sr: int = 22050, bpm: float = 100.0, resolution: int = 8, bars: int = 16) -> np.ndarray:
    step = (60.0 / bpm) * (4.0 / resolution)
    duration = bars * 4 * step
    n_samples = int(sr * duration)
    y = np.zeros(n_samples, dtype=np.float32)

    rng = np.random.default_rng(7)
    grid = np.arange(0.0, duration, step, dtype=np.float32)
    click_len = max(8, int(0.012 * sr))
    click = np.hanning(click_len).astype(np.float32)

    for idx, target in enumerate(grid):
        jitter = float(rng.uniform(-0.085, 0.085))
        onset = np.clip(target + jitter, 0.0, max(0.0, duration - click_len / sr))
        start = int(round(onset * sr))
        amp = 0.9 if idx % 4 == 0 else 0.55
        end = min(start + click_len, n_samples)
        y[start:end] += amp * click[: end - start]

    tone_t = np.arange(n_samples, dtype=np.float32) / sr
    y += (0.03 * np.sin(2 * np.pi * 220.0 * tone_t)).astype(np.float32)
    return np.clip(y, -1.0, 1.0)


def test_onset_quantize_curve_improves_grid_alignment():
    sr = 22050
    bpm = 100.0
    resolution = 8
    mono = _make_jittered_click_track(sr=sr, bpm=bpm, resolution=resolution)
    stereo = np.stack([mono, mono], axis=1)

    curve = onset_quantize_curve(mono, sr, bpm, resolution)
    warped, method = apply_warp(stereo, sr, curve["source_times"], curve["target_times"])

    assert curve["anchor_count"] > 12
    assert curve["method"] == "onset_grid"
    assert method in {"interp_time_map", "rubberband"}
    assert warped.shape[1] == 2

    before = detect_onsets(mono, sr, units="time")
    after = detect_onsets(warped.mean(axis=1), sr, units="time")
    metrics = timing_metrics(before, after, build_grid(len(mono) / sr, bpm, resolution))

    assert metrics["avg_abs_error_after_sec"] < metrics["avg_abs_error_before_sec"]
    assert metrics["improvement_pct"] > 25.0


def test_onset_quantize_curve_uses_beat_anchors_for_sparse_harmonic_pulses():
    sr = 22050
    bpm = 120.0
    duration = 4.0
    t = np.linspace(0.0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
    y = 0.08 * np.sin(2 * np.pi * 220.0 * t)

    pulse_len = int(0.08 * sr)
    pulse_env = np.hanning(pulse_len).astype(np.float32)
    for onset in np.arange(0.0, duration, 0.5):
        start = int(round(onset * sr))
        end = min(len(y), start + pulse_len)
        y[start:end] += 0.4 * pulse_env[: end - start] * np.sin(2 * np.pi * 440.0 * t[: end - start])

    curve = onset_quantize_curve(y.astype(np.float32), sr, bpm, 8)

    assert curve["anchor_count"] >= 8
    assert len(curve["source_times"]) == len(curve["target_times"])


def test_onset_quantize_curve_conforms_endpoint_to_nearest_fixed_grid():
    sr = 22050
    bpm = 108.0
    resolution = 8
    duration = 10.07
    t = np.linspace(0.0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
    y = np.zeros_like(t)
    click_len = int(0.01 * sr)
    click = np.hanning(click_len).astype(np.float32)
    for onset in np.arange(0.0, duration, 60.0 / bpm):
        start = int(round(onset * sr))
        end = min(len(y), start + click_len)
        y[start:end] += click[: end - start]

    curve = onset_quantize_curve(y, sr, bpm, resolution)
    grid = build_grid(duration, bpm, resolution)
    nearest_end = float(grid[np.argmin(np.abs(grid - duration))])

    assert abs(float(curve["target_times"][-1]) - nearest_end) < 1e-6
    assert abs(float(curve["target_times"][-1]) - duration) > 1e-3


def test_endpoint_grid_time_never_snaps_behind_previous_target_anchor():
    grid = np.arange(0.0, 89.1, 0.1744186, dtype=np.float32)

    endpoint = _endpoint_grid_time(grid, 89.302333, min_target_time=89.0)

    assert endpoint >= 89.0
    assert abs(endpoint - 89.302333) < abs(float(grid[-1]) - 89.302333)


def test_onset_quantize_curve_replaces_too_near_final_anchor_with_endpoint():
    sr = 22050
    hop = 512
    duration = 2.01
    bpm = 120.0
    resolution = 8
    frame_count = int(np.ceil(duration * sr / hop)) + 1
    onset_env = np.zeros(frame_count, dtype=np.float32)
    near_end_frame = int(round(1.99 * sr / hop))
    onset_env[near_end_frame] = 1.0

    curve = onset_quantize_curve_from_features(onset_env, hop, sr, duration, bpm, resolution)

    assert abs(float(curve["source_times"][-1]) - duration) < 1e-6
    assert float(curve["target_times"][-1]) > 1.99


def test_onset_quantize_curve_uses_drift_only_for_source_matching():
    sr = 22050
    hop = 512
    duration = 8.0
    bpm = 120.0
    resolution = 4
    frame_count = int(np.ceil(duration * sr / hop)) + 1
    onset_env = np.zeros(frame_count, dtype=np.float32)
    for onset_sec in (0.56, 1.11, 1.67, 2.22, 2.78, 3.33):
        onset_env[int(round(onset_sec * sr / hop))] = 1.0
    drift_windows = [
        {"time_sec": time_sec, "local_bpm": 108.0, "confidence": 0.8}
        for time_sec in (0.0, 2.0, 4.0, 6.0, 8.0)
    ]

    curve = onset_quantize_curve_from_features(
        onset_env,
        hop,
        sr,
        duration,
        bpm,
        resolution,
        source_drift_windows=drift_windows,
    )

    assert np.any(np.isclose(curve["source_times"], 2.22, atol=0.04))
    target_for_drifted_anchor = float(curve["target_times"][np.argmin(np.abs(curve["source_times"] - 2.22))])
    assert abs(target_for_drifted_anchor - 2.0) < 0.02
    assert np.allclose(curve["grid_times"], build_grid(duration, bpm, resolution))


def test_phase_guided_grid_index_preserves_consistent_anchor_phase():
    grid = np.arange(0.0, 8.0, 0.28846, dtype=np.float32)
    recent_offsets = [0.136, 0.135, 0.137, 0.134]

    plain_idx = _phase_guided_grid_index(
        grid,
        4.760,
        step_sec=0.28846,
        recent_offsets=[],
    )
    guided_idx = _phase_guided_grid_index(
        grid,
        4.760,
        step_sec=0.28846,
        recent_offsets=recent_offsets,
    )

    assert plain_idx == 17
    assert guided_idx == 16


def test_phase_guided_grid_index_corrects_accumulated_whole_step_lag():
    grid = np.arange(0.0, 12.0, 0.28846, dtype=np.float32)
    recent_offsets = [-0.402, -0.408, -0.405, -0.41, -0.404]
    event_time = 10.112

    plain_idx = _phase_guided_grid_index(
        grid,
        event_time,
        step_sec=0.28846,
        recent_offsets=[],
    )
    guided_idx = _phase_guided_grid_index(
        grid,
        event_time,
        step_sec=0.28846,
        recent_offsets=recent_offsets,
    )

    guided_offset = float(event_time - grid[guided_idx])
    assert abs(guided_offset) <= 0.02
    assert abs(guided_offset) < 0.2


def test_beat_grid_index_snaps_to_quarter_note_phase():
    grid = np.arange(0.0, 4.0, 0.14423, dtype=np.float32)

    fine_idx = _phase_guided_grid_index(
        grid,
        1.02,
        step_sec=0.14423,
        recent_offsets=[],
    )
    beat_idx = _beat_grid_index_for_time(grid, 1.02, resolution=16)

    assert fine_idx == 7
    assert beat_idx == 8


def test_continuity_limited_grid_index_rejects_extra_step_skip():
    grid_idx = _continuity_limited_grid_index(
        353,
        last_grid_idx=350,
        source_elapsed_sec=0.586,
        step_sec=0.28846,
    )

    assert grid_idx == 352


def test_anchor_threshold_percentile_is_lower_for_long_form_strict_lock():
    default_pct = _anchor_threshold_percentile(8, 96)
    long_form_pct = _anchor_threshold_percentile(8, 96, long_form_track=True, sparse_onset_coverage=False)
    sparse_long_form_pct = _anchor_threshold_percentile(8, 96, long_form_track=True, sparse_onset_coverage=True)

    assert long_form_pct < default_pct
    assert sparse_long_form_pct < long_form_pct


def test_rubberband_warp_uses_single_multichannel_timemap_call(monkeypatch):
    calls: list[tuple[tuple[int, ...], int, int]] = []

    def fake_timemap_stretch(y, sr, time_map):
        calls.append((tuple(y.shape), sr, len(time_map)))
        return np.asarray(y, dtype=np.float32)

    fake_module = types.SimpleNamespace(timemap_stretch=fake_timemap_stretch)
    monkeypatch.setitem(sys.modules, "pyrubberband", fake_module)
    monkeypatch.setattr(warp_module, "_ensure_rubberband_on_path", lambda: object())

    stereo = np.stack([_make_jittered_click_track(), _make_jittered_click_track()], axis=1)
    source = np.array([0.0, 0.5, 1.0], dtype=np.float32)
    target = np.array([0.0, 0.5, 1.0], dtype=np.float32)

    warped = warp_module._rubberband_warp(stereo, 22050, source, target)

    assert warped.shape[1] == 2
    assert calls == [((stereo.shape[0], 2), 22050, 3)]
