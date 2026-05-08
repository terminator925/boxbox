from __future__ import annotations

import numpy as np

from backend.app import _apply_beat_phase_gate, _apply_fixed_grid_authority_gate, _apply_warp_continuity_gate, _beat_phase_shift_target, _continuity_smooth_target, _coverage_rebuild_target, _coverage_snap_target, _coverage_tighten_target, _optimize_groove_target, _phase_snap_target, _should_skip_post_selection_repairs, _strict_lock_groove_request, _stabilize_baseline_target, _summarize_beat_phase_lock, _summarize_metronome_lock, _summarize_warp_continuity


def test_optimize_groove_target_prefers_stronger_quantization_when_better():
    source_times = np.array([0.0, 0.8, 1.8, 3.0], dtype=np.float32)
    target_times = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    grid = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    onsets_before = np.array([0.8, 1.8], dtype=np.float32)

    optimized_target, optimized_preserve, metrics = _optimize_groove_target(
        source_times,
        target_times,
        groove_preserve=20,
        grid=grid,
        onsets_before=onsets_before,
    )

    assert optimized_preserve == 0
    assert np.allclose(optimized_target, target_times)
    assert float(metrics["avg_abs_error_after_sec"]) == 0.0


def test_optimize_groove_target_respects_min_preserve_floor():
    source_times = np.array([0.0, 0.8, 1.8, 3.0], dtype=np.float32)
    target_times = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    grid = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    onsets_before = np.array([0.8, 1.8], dtype=np.float32)

    _, optimized_preserve, _ = _optimize_groove_target(
        source_times,
        target_times,
        groove_preserve=20,
        grid=grid,
        onsets_before=onsets_before,
        min_preserve=20,
    )

    assert optimized_preserve == 20


def test_optimize_groove_target_prefers_better_phase_lock_when_average_error_ties(monkeypatch):
    source_times = np.array([0.0, 0.8, 1.8, 3.0], dtype=np.float32)
    target_times = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    grid = np.array([0.0, 1.0, 2.0, 3.0], dtype=np.float32)
    onsets_before = np.array([0.8, 1.8], dtype=np.float32)

    def fake_candidate_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg):
        if np.allclose(target_times_arg, target_times):
            return {
                "avg_abs_error_after_sec": 0.02,
                "median_signed_error_after_sec": 0.004,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.015,
            }
        return {
            "avg_abs_error_after_sec": 0.02,
            "median_signed_error_after_sec": 0.04,
            "phase_window_abs_max_after_sec": 0.08,
            "phase_window_span_after_sec": 0.12,
        }

    monkeypatch.setattr("backend.app._candidate_metrics_fast", fake_candidate_metrics)

    optimized_target, optimized_preserve, metrics = _optimize_groove_target(
        source_times,
        target_times,
        groove_preserve=20,
        grid=grid,
        onsets_before=onsets_before,
    )

    assert optimized_preserve == 0
    assert np.allclose(optimized_target, target_times)
    assert float(metrics["phase_window_abs_max_after_sec"]) == 0.01


def test_strict_lock_groove_request_caps_long_song_to_tighter_range():
    guided = _strict_lock_groove_request(
        50,
        40,
        duration_sec=240.0,
        event_summary={"event_density_per_sec": 0.85, "strong_event_count": 160},
    )

    assert guided == 20


def test_strict_lock_groove_request_leaves_short_song_unchanged():
    guided = _strict_lock_groove_request(
        50,
        40,
        duration_sec=20.0,
        event_summary={"event_density_per_sec": 0.85, "strong_event_count": 160},
    )

    assert guided == 40


def test_beat_phase_lock_detects_intro_metronome_phase_shift():
    source_times = np.array([0.0, 10.0], dtype=np.float32)
    target_times = np.array([0.12, 10.12], dtype=np.float32)
    beat_times = np.arange(0.0, 10.0, 0.5, dtype=np.float32)

    summary = _summarize_beat_phase_lock(
        source_times,
        target_times,
        beat_times,
        target_bpm=120.0,
        duration_sec=10.0,
    )
    gated = _apply_beat_phase_gate({"verdict": "daw_locked"}, summary)

    assert summary["intro_locked"] is False
    assert gated["verdict"] == "mostly_locked"


def test_beat_phase_gate_treats_half_beat_as_offbeat_alias():
    source_times = np.array([0.0, 10.0], dtype=np.float32)
    target_times = np.array([0.0, 10.0], dtype=np.float32)
    beat_times = np.arange(0.25, 10.0, 0.5, dtype=np.float32)

    summary = _summarize_beat_phase_lock(
        source_times,
        target_times,
        beat_times,
        target_bpm=120.0,
        duration_sec=10.0,
    )
    gated = _apply_beat_phase_gate({"verdict": "daw_locked"}, summary)

    assert summary["offbeat_alias"] is True
    assert gated["verdict"] == "daw_locked"
    assert gated["beat_phase_verdict"] == "offbeat_alias"


def test_beat_phase_gate_trusts_perfect_fixed_grid_when_tracker_is_ambiguous():
    summary = {
        "beat_count": 64,
        "avg_abs_error_after_sec": 0.16,
        "intro_avg_abs_error_after_sec": 0.09,
        "median_signed_error_after_sec": 0.15,
        "offbeat_alias": False,
        "locked": False,
        "intro_locked": False,
    }

    gated = _apply_beat_phase_gate(
        {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "overall_phase_window_abs_max_after_sec": 0.02,
            "overall_phase_window_span_after_sec": 0.04,
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        summary,
    )

    assert gated["verdict"] == "daw_locked"
    assert gated["beat_phase_verdict"] == "grid_authoritative"
    assert gated["beat_phase"]["grid_authoritative"] is True


def test_beat_phase_gate_trusts_near_perfect_segments_with_perfect_fixed_windows():
    gated = _apply_beat_phase_gate(
        {
            "verdict": "mostly_locked",
            "locked_ratio": 0.9468,
            "effective_locked_ratio": 0.9438,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "overall_phase_window_abs_max_after_sec": 0.0122,
            "overall_phase_window_span_after_sec": 0.0234,
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        {
            "beat_count": 418,
            "avg_abs_error_after_sec": 0.128,
            "intro_avg_abs_error_after_sec": 0.096,
            "locked": False,
            "intro_locked": False,
            "offbeat_alias": False,
        },
    )

    assert gated["verdict"] == "mostly_locked"
    assert gated["beat_phase_verdict"] == "grid_authoritative"
    assert gated["beat_phase"]["grid_authoritative"] is True


def test_beat_phase_gate_trusts_sparse_segments_with_tight_perfect_fixed_windows():
    gated = _apply_beat_phase_gate(
        {
            "verdict": "mostly_locked",
            "locked_ratio": 0.8,
            "effective_locked_ratio": 0.7059,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "overall_phase_window_abs_max_after_sec": 0.0132,
            "overall_phase_window_span_after_sec": 0.0161,
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "effective_strong_locked_ratio": 0.7857,
                "eligible_window_count": 14,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        {
            "beat_count": 121,
            "avg_abs_error_after_sec": 0.072,
            "intro_avg_abs_error_after_sec": 0.066,
            "locked": False,
            "intro_locked": False,
            "offbeat_alias": False,
        },
    )

    assert gated["beat_phase_verdict"] == "grid_authoritative"
    assert gated["beat_phase"]["grid_authoritative"] is True


def test_beat_phase_shift_corrects_one_subdivision_offset():
    source_times = np.linspace(0.0, 8.0, 17, dtype=np.float32)
    target_times = source_times.copy()
    beat_times = np.arange(0.25, 8.0, 0.5, dtype=np.float32)
    grid = np.arange(0.0, 8.5, 0.25, dtype=np.float32)
    onsets_before = source_times.copy()

    shifted, decision = _beat_phase_shift_target(
        source_times,
        target_times,
        grid,
        onsets_before,
        beat_times,
        duration_sec=8.0,
        target_bpm=120.0,
    )

    assert decision is not None
    assert decision["shift_steps"] == 1
    assert shifted[1] < target_times[1]
    assert decision["after"]["intro_locked"] is True


def test_stabilize_baseline_target_reduces_local_overwarp(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    baseline_target = source_times.copy()
    baseline_target[5:8] -= 0.4
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5, 7.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec == 4.0:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.12 if float(target_times_arg[5]) < 4.8 else 0.07,
                "improvement_sec": -0.04 if float(target_times_arg[5]) < 4.8 else 0.01,
                "improvement_pct": -10.0 if float(target_times_arg[5]) < 4.8 else 12.0,
                "median_signed_error_after_sec": -0.06 if float(target_times_arg[5]) < 4.8 else -0.02,
                "phase_window_abs_max_after_sec": 0.09 if float(target_times_arg[5]) < 4.8 else 0.02,
                "phase_window_span_after_sec": 0.11 if float(target_times_arg[5]) < 4.8 else 0.03,
                "num_events_before": 4,
                "num_events_after": 4,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 4,
            "num_events_after": 4,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    stabilized, decisions = _stabilize_baseline_target(
        source_times,
        baseline_target,
        grid,
        onsets_before,
        segments,
        120.0,
        8,
    )

    assert len(decisions) == 1
    assert decisions[0]["start_sec"] == 4.0
    assert stabilized[5] > baseline_target[5]
    assert decisions[0]["retreat_strength"] >= 0.72
    assert decisions[0]["transition_sec"] >= 0.4


def test_stabilize_baseline_target_clusters_bridge_segment(monkeypatch):
    source_times = np.linspace(0.0, 12.0, 13, dtype=np.float32)
    baseline_target = source_times.copy()
    baseline_target[5:10] -= 0.35
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5, 7.5, 8.5, 9.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
        {"start_sec": 6.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 12.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec == 4.0:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.104,
                "improvement_sec": -0.024,
                "improvement_pct": 2.0,
                "median_signed_error_after_sec": -0.04,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 4,
                "num_events_after": 4,
            }
        if start_sec == 6.0:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.055,
                "improvement_sec": 0.025,
                "improvement_pct": 40.0,
                "median_signed_error_after_sec": -0.03,
                "phase_window_abs_max_after_sec": 0.06,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 4,
                "num_events_after": 4,
            }
        if start_sec == 8.0:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.11,
                "improvement_sec": -0.03,
                "improvement_pct": -5.0,
                "median_signed_error_after_sec": -0.05,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 4,
                "num_events_after": 4,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 4,
            "num_events_after": 4,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    stabilized, decisions = _stabilize_baseline_target(
        source_times,
        baseline_target,
        grid,
        onsets_before,
        segments,
        120.0,
        8,
    )

    assert len(decisions) == 1
    assert decisions[0]["segment_count"] == 3
    assert decisions[0]["start_sec"] == 4.0
    assert decisions[0]["end_sec"] == 10.0
    assert decisions[0]["reanchored_cluster"] is True
    assert stabilized[7] > baseline_target[7]


def test_phase_snap_target_corrects_isolated_phase_coherent_segment(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    target_times = source_times.copy()
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec == 4.0:
            shifted = float(target_times_arg[5] - source_times_arg[5])
            if shifted > 0.01:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.06,
                    "improvement_sec": 0.02,
                    "improvement_pct": 10.0,
                    "median_signed_error_after_sec": -0.005,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 3,
                    "num_events_after": 3,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.09,
                "improvement_sec": -0.01,
                "improvement_pct": 1.0,
                "median_signed_error_after_sec": -0.03,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    snapped, decisions = _phase_snap_target(
        source_times,
        target_times,
        grid,
        onsets_before,
        segments,
        120.0,
        8,
    )

    assert len(decisions) == 1
    assert decisions[0]["start_sec"] == 4.0
    assert decisions[0]["snapped_error_sec"] < decisions[0]["after_error_sec"]
    assert snapped[5] > target_times[5]


def test_coverage_snap_target_improves_near_locked_segment(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    target_times = source_times.copy()
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec == 4.0:
            shifted = float(target_times_arg[5] - source_times_arg[5])
            if shifted > 0.005:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.058,
                    "improvement_sec": 0.022,
                    "improvement_pct": 8.0,
                    "median_signed_error_after_sec": -0.004,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 3,
                    "num_events_after": 3,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.068,
                "improvement_sec": 0.012,
                "improvement_pct": 2.0,
                "median_signed_error_after_sec": -0.012,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    snapped, decisions = _coverage_snap_target(
        source_times,
        target_times,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["start_sec"] == 4.0
    assert decisions[0]["snapped_error_sec"] < decisions[0]["after_error_sec"]
    assert snapped[5] > target_times[5]


def test_coverage_tighten_target_pulls_near_lock_segment_toward_strict_grid(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[5] += 0.2
    strict_target[6] += 0.2
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec == 4.0:
            tightened = float(target_times_arg[5] - target_times[5])
            if tightened > 0.02:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.058,
                    "improvement_sec": 0.022,
                    "improvement_pct": 10.0,
                    "median_signed_error_after_sec": -0.004,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 3,
                    "num_events_after": 3,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.067,
                "improvement_sec": 0.013,
                "improvement_pct": 4.0,
                "median_signed_error_after_sec": -0.006,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    tightened, decisions = _coverage_tighten_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["start_sec"] == 4.0
    assert decisions[0]["tightened_error_sec"] < decisions[0]["after_error_sec"]
    assert tightened[5] > target_times[5]


def test_coverage_rebuild_target_improves_adjacent_medium_quality_cluster(monkeypatch):
    source_times = np.linspace(0.0, 14.0, 15, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[5:10] += 0.25
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5, 7.5, 8.5, 9.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
        {"start_sec": 6.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 14.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[6] - target_times[6])
        if start_sec == 4.0 and end_sec == 10.0:
            if shifted > 0.05:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.058,
                    "improvement_sec": 0.022,
                    "improvement_pct": 12.0,
                    "median_signed_error_after_sec": -0.004,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 6,
                    "num_events_after": 6,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.074,
                "improvement_sec": 0.006,
                "improvement_pct": 4.0,
                "median_signed_error_after_sec": -0.006,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 6,
                "num_events_after": 6,
            }
        if start_sec in {4.0, 6.0, 8.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.07,
                "improvement_sec": 0.01,
                "improvement_pct": 8.0,
                "median_signed_error_after_sec": -0.006,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    rebuilt, decisions = _coverage_rebuild_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["segment_count"] == 3
    assert decisions[0]["rebuilt_error_sec"] < decisions[0]["after_error_sec"]
    assert rebuilt[6] > target_times[6]


def test_coverage_rebuild_target_improves_single_phase_stable_segment(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[5:8] += 0.25
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[6] - target_times[6])
        if start_sec == 4.0 and end_sec == 8.0:
            if shifted > 0.09:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.057,
                    "improvement_sec": 0.023,
                    "improvement_pct": 11.0,
                    "median_signed_error_after_sec": -0.01,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 3,
                    "num_events_after": 3,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.078,
                "improvement_sec": 0.002,
                "improvement_pct": 3.0,
                "median_signed_error_after_sec": -0.01,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    rebuilt, decisions = _coverage_rebuild_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["segment_count"] == 1
    assert decisions[0]["rebuilt_error_sec"] < decisions[0]["after_error_sec"]
    assert rebuilt[6] > target_times[6]


def test_coverage_commit_target_hard_commits_near_lock_segment(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[5:8] += 0.25
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[6] - target_times[6])
        if start_sec == 4.0 and end_sec == 8.0:
            if shifted > 0.20:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.059,
                    "improvement_sec": 0.021,
                    "improvement_pct": 9.0,
                    "median_signed_error_after_sec": -0.008,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 3,
                    "num_events_after": 3,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.067,
                "improvement_sec": 0.013,
                "improvement_pct": 6.0,
                "median_signed_error_after_sec": -0.008,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    from backend.app import _coverage_commit_target

    committed, decisions = _coverage_commit_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["committed_error_sec"] < 0.0605
    assert committed[6] > target_times[6]


def test_coverage_reanchor_target_improves_medium_cluster(monkeypatch):
    source_times = np.linspace(0.0, 14.0, 15, dtype=np.float32)
    target_times = source_times.copy()
    grid = source_times.copy()
    onsets_before = np.array([4.5, 5.5, 6.5, 7.5, 8.5, 9.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
        {"start_sec": 6.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 14.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[6] - target_times[6])
        if start_sec == 4.0 and end_sec == 10.0:
            if shifted > 0.05:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.057,
                    "improvement_sec": 0.023,
                    "improvement_pct": 16.0,
                    "median_signed_error_after_sec": -0.004,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 6,
                    "num_events_after": 6,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.072,
                "improvement_sec": 0.008,
                "improvement_pct": 10.0,
                "median_signed_error_after_sec": -0.004,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 6,
                "num_events_after": 6,
            }
        if start_sec in {4.0, 6.0, 8.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.07,
                "improvement_sec": 0.01,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.004,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    def fake_reanchor(*args, **kwargs):
        candidate = target_times.copy()
        candidate[5:10] += 0.2
        return candidate.astype(np.float32)

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)
    monkeypatch.setattr("backend.app._reanchor_cluster_target", fake_reanchor)

    from backend.app import _coverage_reanchor_target

    rebuilt, decisions = _coverage_reanchor_target(
        source_times,
        target_times,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["segment_count"] == 3
    assert decisions[0]["reanchored_error_sec"] < decisions[0]["after_error_sec"]
    assert rebuilt[6] > target_times[6]


def test_coverage_bridge_target_promotes_bounded_medium_cluster(monkeypatch):
    source_times = np.linspace(0.0, 12.0, 13, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[4:9] += 0.25
    grid = source_times.copy()
    onsets_before = np.array([3.5, 4.5, 5.5, 6.5, 7.5, 8.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 2.0},
        {"start_sec": 2.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
        {"start_sec": 6.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 12.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[5] - target_times[5])
        if start_sec in {0.0, 10.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.05,
                "improvement_sec": 0.03,
                "improvement_pct": 20.0,
                "median_signed_error_after_sec": -0.002,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        if start_sec == 4.0 and end_sec == 8.0:
            if shifted > 0.08:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.058,
                    "improvement_sec": 0.022,
                    "improvement_pct": 14.0,
                    "median_signed_error_after_sec": -0.004,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 6,
                    "num_events_after": 6,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.07,
                "improvement_sec": 0.01,
                "improvement_pct": 10.0,
                "median_signed_error_after_sec": -0.004,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 6,
                "num_events_after": 6,
            }
        if start_sec in {4.0, 6.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.068,
                "improvement_sec": 0.012,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.004,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    from backend.app import _coverage_bridge_target

    bridged, decisions = _coverage_bridge_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["segment_count"] == 2
    assert decisions[0]["bridged_error_sec"] < decisions[0]["after_error_sec"]
    assert bridged[5] > target_times[5]


def test_coverage_bridge_target_handles_filtered_segment_rows(monkeypatch):
    source_times = np.linspace(0.0, 14.0, 15, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[10:12] += 0.1
    grid = source_times.copy()
    onsets_before = np.array([8.5, 9.5, 10.5, 11.5, 12.5], dtype=np.float32)
    segments = [{"start_sec": float(i * 2), "end_sec": float(i * 2 + 2)} for i in range(7)]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec < 8.0:
            return None
        shifted = float(target_times_arg[10] - target_times[10])
        if start_sec == 8.0:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.04,
                "improvement_pct": 40.0,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.01,
                "median_signed_error_after_sec": -0.004,
                "num_events_after": 3,
            }
        if start_sec == 10.0:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.055 if shifted > 0.05 else 0.07,
                "improvement_pct": 12.0,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.01,
                "median_signed_error_after_sec": -0.004,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_pct": 40.0,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "median_signed_error_after_sec": -0.004,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    from backend.app import _coverage_bridge_target

    bridged, decisions = _coverage_bridge_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert isinstance(decisions, list)
    assert bridged.shape == target_times.shape


def test_coverage_context_reanchor_target_uses_locked_neighbors(monkeypatch):
    source_times = np.linspace(0.0, 14.0, 15, dtype=np.float32)
    target_times = source_times.copy()
    grid = source_times.copy()
    onsets_before = np.array([2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 2.0},
        {"start_sec": 2.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
        {"start_sec": 6.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 12.0},
        {"start_sec": 12.0, "end_sec": 14.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[6] - target_times[6])
        if start_sec in {2.0, 10.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.05,
                "improvement_sec": 0.03,
                "improvement_pct": 18.0,
                "median_signed_error_after_sec": -0.002,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        if start_sec == 4.0 and end_sec == 8.0:
            if shifted > 0.05:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.057,
                    "improvement_sec": 0.023,
                    "improvement_pct": 14.0,
                    "median_signed_error_after_sec": -0.004,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 6,
                    "num_events_after": 6,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.071,
                "improvement_sec": 0.009,
                "improvement_pct": 10.0,
                "median_signed_error_after_sec": -0.004,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 6,
                "num_events_after": 6,
            }
        if start_sec in {4.0, 6.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.068,
                "improvement_sec": 0.012,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.004,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        if start_sec == 2.0 and end_sec == 10.0:
            if shifted > 0.05:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.053,
                    "improvement_sec": 0.027,
                    "improvement_pct": 17.0,
                    "median_signed_error_after_sec": -0.003,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 9,
                    "num_events_after": 9,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.06,
                "improvement_sec": 0.02,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.003,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 9,
                "num_events_after": 9,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    def fake_reanchor(*args, **kwargs):
        candidate = target_times.copy()
        candidate[4:9] += 0.18
        return candidate.astype(np.float32)

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)
    monkeypatch.setattr("backend.app._reanchor_cluster_target", fake_reanchor)

    from backend.app import _coverage_context_reanchor_target

    refined, decisions = _coverage_context_reanchor_target(
        source_times,
        target_times,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["segment_count"] == 2
    assert decisions[0]["refined_core_error_sec"] < decisions[0]["core_error_sec"]
    assert refined[6] > target_times[6]


def test_coverage_replace_target_hard_replaces_bounded_region(monkeypatch):
    source_times = np.linspace(0.0, 12.0, 13, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[4:9] += 0.2
    grid = source_times.copy()
    onsets_before = np.array([3.5, 4.5, 5.5, 6.5, 7.5, 8.5], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 2.0},
        {"start_sec": 2.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
        {"start_sec": 6.0, "end_sec": 8.0},
        {"start_sec": 8.0, "end_sec": 10.0},
        {"start_sec": 10.0, "end_sec": 12.0},
    ]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        shifted = float(target_times_arg[5] - target_times[5])
        if start_sec in {2.0, 8.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.05,
                "improvement_sec": 0.03,
                "improvement_pct": 20.0,
                "median_signed_error_after_sec": -0.002,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        if start_sec == 4.0 and end_sec == 8.0:
            if shifted > 0.12:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.058,
                    "improvement_sec": 0.022,
                    "improvement_pct": 14.0,
                    "median_signed_error_after_sec": -0.003,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 6,
                    "num_events_after": 6,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.07,
                "improvement_sec": 0.01,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.003,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 6,
                "num_events_after": 6,
            }
        if start_sec in {4.0, 6.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.068,
                "improvement_sec": 0.012,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.003,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        if start_sec == 2.0 and end_sec == 10.0:
            if shifted > 0.12:
                return {
                    "avg_abs_error_before_sec": 0.08,
                    "avg_abs_error_after_sec": 0.053,
                    "improvement_sec": 0.027,
                    "improvement_pct": 17.0,
                    "median_signed_error_after_sec": -0.003,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_before": 9,
                    "num_events_after": 9,
                }
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.06,
                "improvement_sec": 0.02,
                "improvement_pct": 12.0,
                "median_signed_error_after_sec": -0.003,
                "phase_window_abs_max_after_sec": 0.0,
                "phase_window_span_after_sec": 0.0,
                "num_events_before": 9,
                "num_events_after": 9,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.04,
            "improvement_sec": 0.04,
            "improvement_pct": 50.0,
            "median_signed_error_after_sec": -0.01,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 3,
            "num_events_after": 3,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    from backend.app import _coverage_replace_target

    replaced, decisions = _coverage_replace_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 1
    assert decisions[0]["replaced_core_error_sec"] < 0.0605
    assert replaced[5] > target_times[5]


def test_coverage_replace_target_handles_filtered_segment_rows(monkeypatch):
    source_times = np.linspace(0.0, 14.0, 15, dtype=np.float32)
    target_times = source_times.copy()
    strict_target = source_times.copy()
    strict_target[10:12] += 0.1
    grid = source_times.copy()
    onsets_before = np.array([8.5, 9.5, 10.5, 11.5, 12.5], dtype=np.float32)
    segments = [{"start_sec": float(i * 2), "end_sec": float(i * 2 + 2)} for i in range(7)]

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if start_sec in {0.0, 2.0, 4.0}:
            return None
        if start_sec in {6.0, 12.0}:
            return {
                "avg_abs_error_before_sec": 0.08,
                "avg_abs_error_after_sec": 0.04,
                "improvement_sec": 0.04,
                "improvement_pct": 25.0,
                "median_signed_error_after_sec": 0.0,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.01,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        if start_sec in {8.0, 10.0}:
            return {
                "avg_abs_error_before_sec": 0.09,
                "avg_abs_error_after_sec": 0.07,
                "improvement_sec": 0.02,
                "improvement_pct": 15.0,
                "median_signed_error_after_sec": -0.002,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.01,
                "num_events_before": 3,
                "num_events_after": 3,
            }
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": 0.055,
            "improvement_sec": 0.025,
            "improvement_pct": 20.0,
            "median_signed_error_after_sec": -0.002,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
            "num_events_before": 5,
            "num_events_after": 5,
        }

    monkeypatch.setattr("backend.app._segment_timing_metrics", fake_segment_metrics)

    from backend.app import _coverage_replace_target

    replaced, decisions = _coverage_replace_target(
        source_times,
        target_times,
        strict_target,
        grid,
        onsets_before,
        segments,
    )

    assert isinstance(decisions, list)
    assert replaced.shape == target_times.shape


def test_summarize_metronome_lock_flags_late_meltdown():
    segments = [
        {
            "start_sec": 0.0,
            "timing_metrics": {
                "avg_abs_error_after_sec": 0.04,
                "improvement_pct": 12.0,
                "phase_window_abs_max_after_sec": 0.02,
                "phase_window_span_after_sec": 0.03,
            },
        },
        {
            "start_sec": 70.0,
            "timing_metrics": {
                "avg_abs_error_after_sec": 0.11,
                "improvement_pct": -5.0,
                "phase_window_abs_max_after_sec": 0.09,
                "phase_window_span_after_sec": 0.11,
            },
        },
    ]

    summary = _summarize_metronome_lock(
        {
            "phase_window_abs_max_after_sec": 0.05,
            "phase_window_span_after_sec": 0.09,
        },
        segments,
    )

    assert summary["verdict"] == "drifts_late"
    assert summary["meltdown_segments"] == 1
    assert summary["first_meltdown_sec"] == 70.0


def test_summarize_metronome_lock_counts_absolute_low_error_segments():
    segments = [
        {
            "start_sec": 0.0,
            "timing_metrics": {
                "avg_abs_error_after_sec": 0.049,
                "improvement_pct": -12.0,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.01,
            },
        },
        {
            "start_sec": 10.0,
            "timing_metrics": {
                "avg_abs_error_after_sec": 0.039,
                "improvement_pct": -4.0,
                "phase_window_abs_max_after_sec": 0.01,
                "phase_window_span_after_sec": 0.01,
            },
        },
    ]

    summary = _summarize_metronome_lock(
        {
            "phase_window_abs_max_after_sec": 0.02,
            "phase_window_span_after_sec": 0.02,
        },
        segments,
    )

    assert summary["locked_ratio"] == 1.0
    assert summary["strong_locked_ratio"] == 0.5


def test_summarize_metronome_lock_tolerates_short_microsegment_phase_edges():
    segments = [
        {
            "start_sec": 24.0,
            "timing_metrics": {
                "avg_abs_error_after_sec": 0.0495,
                "improvement_pct": -3.5,
                "phase_window_abs_max_after_sec": 0.062,
                "phase_window_span_after_sec": 0.061,
            },
        },
        {
            "start_sec": 78.0,
            "timing_metrics": {
                "avg_abs_error_after_sec": 0.0614,
                "improvement_pct": 8.5,
                "phase_window_abs_max_after_sec": 0.055,
                "phase_window_span_after_sec": 0.064,
            },
        },
    ]

    summary = _summarize_metronome_lock(
        {
            "phase_window_abs_max_after_sec": 0.02,
            "phase_window_span_after_sec": 0.02,
        },
        segments,
    )

    assert summary["locked_ratio"] == 1.0
    assert summary["meltdown_segments"] == 0


def test_summarize_metronome_lock_counts_phase_coherent_near_lock_segments():
    summary = _summarize_metronome_lock(
        {
            "phase_window_abs_max_after_sec": 0.02,
            "phase_window_span_after_sec": 0.02,
        },
        [
            {
                "start_sec": 78.75,
                "timing_metrics": {
                    "avg_abs_error_after_sec": 0.0618,
                    "improvement_pct": 11.8,
                    "phase_window_abs_max_after_sec": 0.055,
                    "phase_window_span_after_sec": 0.064,
                },
            },
            {
                "start_sec": 266.25,
                "timing_metrics": {
                    "avg_abs_error_after_sec": 0.0627,
                    "improvement_pct": -13.7,
                    "phase_window_abs_max_after_sec": 0.022,
                    "phase_window_span_after_sec": 0.017,
                },
            },
            {
                "start_sec": 253.56,
                "timing_metrics": {
                    "avg_abs_error_after_sec": 0.0687,
                    "improvement_pct": 10.4,
                    "phase_window_abs_max_after_sec": 0.016,
                    "phase_window_span_after_sec": 0.011,
                },
            },
        ],
    )

    assert summary["locked_ratio"] == 1.0
    assert summary["unstable_segments"] == 0


def test_summarize_metronome_lock_excludes_sparse_low_evidence_segments_from_effective_ratio():
    summary = _summarize_metronome_lock(
        {
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.01,
        },
        [
            {
                "start_sec": 0.0,
                "timing_metrics": {
                    "avg_abs_error_after_sec": 0.03,
                    "improvement_pct": 40.0,
                    "phase_window_abs_max_after_sec": 0.01,
                    "phase_window_span_after_sec": 0.01,
                    "num_events_after": 24,
                },
            },
            {
                "start_sec": 100.0,
                "timing_metrics": {
                    "avg_abs_error_after_sec": 0.067,
                    "improvement_pct": -55.0,
                    "phase_window_abs_max_after_sec": 0.0,
                    "phase_window_span_after_sec": 0.0,
                    "num_events_after": 3,
                },
            },
        ],
    )

    assert summary["locked_ratio"] == 0.5
    assert summary["effective_locked_ratio"] == 1.0
    assert summary["excluded_segments"] == 1


def test_refine_segments_for_strict_lock_merges_short_long_form_regions():
    from backend.app import _refine_segments_for_strict_lock

    segments = [
        {"start_sec": 0.0, "end_sec": 1.7},
        {"start_sec": 1.7, "end_sec": 3.4},
        {"start_sec": 3.4, "end_sec": 5.1},
        {"start_sec": 5.1, "end_sec": 6.8},
        {"start_sec": 6.8, "end_sec": 8.5},
    ]
    grid = np.arange(0.0, 8.6, 0.5, dtype=np.float32)
    event_summary = {"event_density_per_sec": 0.8, "strong_event_count": 64}

    refined = _refine_segments_for_strict_lock(segments, grid, 8.5, event_summary)
    assert refined == segments

    long_refined = _refine_segments_for_strict_lock(segments, grid, 60.0, event_summary)
    assert len(long_refined) < len(segments)
    assert long_refined[0]["start_sec"] == 0.0
    assert long_refined[-1]["end_sec"] == 60.0 or long_refined[-1]["end_sec"] == 8.5


def test_refine_segments_for_strict_lock_requires_dense_event_summary():
    from backend.app import _refine_segments_for_strict_lock

    segments = [
        {"start_sec": 0.0, "end_sec": 2.0},
        {"start_sec": 2.0, "end_sec": 4.0},
        {"start_sec": 4.0, "end_sec": 6.0},
    ]
    grid = np.arange(0.0, 6.1, 0.5, dtype=np.float32)

    refined = _refine_segments_for_strict_lock(
        segments,
        grid,
        60.0,
        {"event_density_per_sec": 0.2, "strong_event_count": 10},
    )

    assert refined == segments


def test_should_skip_post_selection_repairs_for_locked_long_form_run():
    from backend.app import _should_skip_post_selection_repairs

    assert _should_skip_post_selection_repairs(
        120.0,
        {
            "verdict": "daw_locked",
            "meltdown_segments": 0,
            "unstable_segments": 0,
        },
    ) is True
    assert _should_skip_post_selection_repairs(
        120.0,
        {
            "verdict": "mostly_locked",
            "meltdown_segments": 0,
            "unstable_segments": 0,
        },
    ) is True
    assert _should_skip_post_selection_repairs(
        120.0,
        {
            "verdict": "mostly_locked",
            "meltdown_segments": 0,
            "unstable_segments": 0,
            "beat_phase_verdict": "risk",
            "beat_phase": {"beat_count": 32, "locked": False, "intro_locked": False},
        },
    ) is False
    assert _should_skip_post_selection_repairs(
        120.0,
        {
            "verdict": "mostly_locked",
            "meltdown_segments": 0,
            "unstable_segments": 0,
            "beat_phase_verdict": "offbeat_alias",
            "beat_phase": {"beat_count": 32, "locked": False, "intro_locked": False, "offbeat_alias": True},
        },
    ) is True
    assert _should_skip_post_selection_repairs(
        120.0,
        {
            "verdict": "unstable",
            "meltdown_segments": 0,
            "unstable_segments": 0,
        },
    ) is False


def test_apply_fixed_window_gate_downgrades_daw_locked_when_fixed_windows_fail():
    from backend.app import _apply_fixed_window_gate

    gated = _apply_fixed_window_gate(
        {"verdict": "daw_locked", "locked_ratio": 0.83, "meltdown_segments": 0, "unstable_segments": 0},
        {"locked_ratio": 0.6, "meltdown_windows": 0, "unstable_windows": 0, "window_count": 12},
    )

    assert gated["verdict"] == "unstable"
    assert gated["segment_verdict"] == "daw_locked"
    assert gated["fixed_windows"]["locked_ratio"] == 0.6


def test_apply_fixed_window_gate_preserves_daw_locked_when_fixed_windows_pass():
    from backend.app import _apply_fixed_window_gate

    gated = _apply_fixed_window_gate(
        {"verdict": "daw_locked", "locked_ratio": 0.83, "meltdown_segments": 0, "unstable_segments": 0},
        {"locked_ratio": 0.85, "meltdown_windows": 0, "unstable_windows": 0, "window_count": 12},
    )

    assert gated["verdict"] == "daw_locked"
    assert "segment_verdict" not in gated


def test_summarize_fixed_window_lock_excludes_sparse_tail_window():
    from backend.app import _summarize_fixed_window_lock

    source_times = np.arange(0.0, 16.0, 0.5, dtype=np.float32)
    target_times = source_times.copy()
    grid = np.arange(0.0, 18.0, 0.5, dtype=np.float32)
    onsets_before = np.concatenate(
        [
            np.arange(0.0, 8.0, 0.5, dtype=np.float32),
            np.array([10.0, 10.5, 11.0, 11.5, 12.0], dtype=np.float32),
        ]
    )
    target_times[source_times >= 9.5] += 0.08

    summary = _summarize_fixed_window_lock(
        source_times,
        target_times,
        grid,
        onsets_before,
        duration_sec=13.5,
        target_bpm=104.0,
    )

    assert summary["locked_ratio"] < 1.0
    assert summary["effective_locked_ratio"] == 1.0
    assert summary["excluded_windows"] == 1
    assert summary["windows"][-1]["exclusion_reason"] == "sparse_tail"


def test_summarize_warp_continuity_flags_bar_phase_jump():
    source_times = np.arange(0.0, 96.0, 0.5, dtype=np.float32)
    target_times = source_times.copy()
    target_times[source_times >= 48.0] += 0.35

    continuity = _summarize_warp_continuity(source_times, target_times, target_bpm=104.0, resolution=8)

    assert continuity["verdict"] == "jump_risk"
    assert float(continuity["max_window_offset_jump_sec"]) > 0.3
    assert continuity["max_window_offset_jump_from_sec"] is not None
    assert continuity["max_window_offset_jump_to_sec"] is not None
    assert float(continuity["max_window_offset_before_sec"]) < 0.05
    assert float(continuity["max_window_offset_after_sec"]) > 0.3
    assert 40.0 <= float(continuity["max_window_offset_jump_from_sec"]) <= 52.0
    assert 44.0 <= float(continuity["max_window_offset_jump_to_sec"]) <= 56.0


def test_summarize_warp_continuity_does_not_jump_risk_single_local_outlier():
    source_times = np.arange(0.0, 96.0, 0.5, dtype=np.float32)
    target_times = source_times.copy()
    target_times[40] += 0.36

    continuity = _summarize_warp_continuity(source_times, target_times, target_bpm=120.0, resolution=8)

    assert continuity["verdict"] != "jump_risk"
    assert float(continuity["max_local_stretch_delta"]) >= 0.5
    assert float(continuity["p99_local_stretch_delta"]) < 0.12


def test_apply_warp_continuity_gate_downgrades_jump_risk():
    gated = _apply_warp_continuity_gate(
        {"verdict": "daw_locked", "locked_ratio": 1.0},
        {"verdict": "jump_risk", "max_window_offset_jump_sec": 0.35},
    )

    assert gated["verdict"] == "mostly_locked"
    assert gated["continuity_verdict"] == "jump_risk"


def test_post_selection_repairs_do_not_skip_warp_jump_risk():
    assert not _should_skip_post_selection_repairs(
        180.0,
        {
            "verdict": "mostly_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {"verdict": "jump_risk"},
            "beat_phase": {"locked": True, "intro_locked": True},
            "fixed_windows": {"effective_locked_ratio": 1.0},
        },
    )


def test_post_selection_repairs_can_skip_beat_phase_shift_when_continuity_is_clean():
    assert _should_skip_post_selection_repairs(
        180.0,
        {
            "verdict": "mostly_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {"verdict": "watch"},
            "beat_phase": {"locked": True, "intro_locked": True},
            "fixed_windows": {"effective_locked_ratio": 1.0},
        },
    )


def test_continuity_smooth_target_reduces_late_offset_jump(monkeypatch):
    source_times = np.arange(0.0, 96.5, 0.5, dtype=np.float32)
    target_times = source_times.copy()
    target_times[source_times >= 78.0] -= 0.6
    grid = np.arange(0.0, 97.0, 0.5, dtype=np.float32)
    onsets_before = np.arange(0.0, 96.0, 0.5, dtype=np.float32)
    monkeypatch.setattr(
        "backend.app._candidate_metrics_fast",
        lambda *args, **kwargs: {"avg_abs_error_after_sec": 0.01},
    )
    monkeypatch.setattr(
        "backend.app._summarize_fixed_window_lock",
        lambda *args, **kwargs: {
            "effective_locked_ratio": 1.0,
            "locked_ratio": 1.0,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )

    before = _summarize_warp_continuity(source_times, target_times, target_bpm=120.0, resolution=8)
    smoothed, decisions = _continuity_smooth_target(
        source_times,
        target_times,
        grid,
        onsets_before,
        duration_sec=96.0,
        target_bpm=120.0,
        resolution=8,
    )
    after = _summarize_warp_continuity(source_times, smoothed, target_bpm=120.0, resolution=8)

    assert decisions
    assert before["verdict"] == "jump_risk"
    assert after["verdict"] != "jump_risk"
    assert smoothed.shape == target_times.shape


def test_summarize_warp_continuity_labels_subdivision_alias_jump():
    source_times = np.arange(0.0, 96.5, 0.5, dtype=np.float32)
    target_times = source_times.copy()
    target_times[source_times >= 48.0] += 0.24

    summary = _summarize_warp_continuity(source_times, target_times, target_bpm=125.0, resolution=8)

    assert summary["verdict"] == "jump_risk"
    assert summary["subdivision_alias_jump"] is True
    assert summary["nearest_subdivision_steps"] == 1
    assert summary["grid_step_sec"] == 0.24


def test_fixed_grid_authority_accepts_perfect_subdivision_alias_jump():
    gated = _apply_fixed_grid_authority_gate(
        {
            "verdict": "mostly_locked",
            "locked_ratio": 1.0,
            "effective_locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "overall_phase_window_abs_max_after_sec": 0.022,
            "overall_phase_window_span_after_sec": 0.024,
            "fixed_windows": {
                "locked_ratio": 1.0,
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
            "beat_phase": {
                "beat_count": 64,
                "locked": False,
                "intro_locked": True,
                "grid_authoritative": True,
                "avg_abs_error_after_sec": 0.09,
                "intro_avg_abs_error_after_sec": 0.02,
            },
            "warp_continuity": {
                "verdict": "jump_risk",
                "subdivision_alias_jump": True,
                "max_window_offset_jump_sec": 0.218,
                "grid_step_sec": 0.24,
            },
        },
        {"avg_abs_error_after_sec": 0.042},
    )

    assert gated["verdict"] == "daw_locked"
    assert gated["fixed_grid_authoritative"] is True
    assert gated["warp_continuity"]["verdict"] == "subdivision_alias"


def test_fixed_grid_authority_accepts_mostly_locked_subdivision_like_jump():
    gated = _apply_fixed_grid_authority_gate(
        {
            "verdict": "mostly_locked",
            "locked_ratio": 0.956,
            "effective_locked_ratio": 0.955,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "overall_phase_window_abs_max_after_sec": 0.01,
            "overall_phase_window_span_after_sec": 0.014,
            "fixed_windows": {
                "locked_ratio": 0.982,
                "effective_locked_ratio": 0.982,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
            "beat_phase": {
                "beat_count": 64,
                "locked": False,
                "intro_locked": False,
                "grid_authoritative": True,
                "avg_abs_error_after_sec": 0.084,
                "intro_avg_abs_error_after_sec": 0.084,
            },
            "warp_continuity": {
                "verdict": "jump_risk",
                "subdivision_alias_jump": True,
                "max_window_offset_jump_sec": 0.202,
                "grid_step_sec": 0.1667,
            },
        },
        {"avg_abs_error_after_sec": 0.0335},
    )

    assert gated["verdict"] == "daw_locked"
    assert gated["fixed_grid_authoritative"] is True
    assert gated["warp_continuity"]["verdict"] == "subdivision_alias"


def test_build_daw_lock_diagnostics_reports_warp_jump():
    from backend.app import _build_daw_lock_diagnostics

    diagnostics = _build_daw_lock_diagnostics(
        {
            "verdict": "mostly_locked",
            "warp_continuity": {
                "verdict": "jump_risk",
                "max_window_offset_jump_sec": 0.35,
                "max_window_offset_jump_from_sec": 46.1,
                "max_window_offset_jump_to_sec": 50.7,
                "max_window_offset_before_sec": 0.0,
                "max_window_offset_after_sec": 0.35,
            },
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        }
    )

    assert diagnostics["issue_count"] == 1
    assert diagnostics["summary"].startswith("DAW-lock risk")
    assert diagnostics["items"][0]["type"] == "warp_continuity_jump"
    assert diagnostics["items"][0]["severity"] == "high"
    assert diagnostics["items"][0]["start_sec"] == 46.1
    assert diagnostics["items"][0]["offset_jump_sec"] == 0.35


def test_fixed_grid_authority_promotes_sparse_segment_warning_to_daw_locked():
    gated = _apply_fixed_grid_authority_gate(
        {
            "verdict": "mostly_locked",
            "effective_locked_ratio": 0.79,
            "overall_phase_window_abs_max_after_sec": 0.006,
            "overall_phase_window_span_after_sec": 0.009,
            "unstable_segments": 1,
            "meltdown_segments": 1,
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
            "warp_continuity": {"verdict": "watch"},
            "beat_phase": {
                "beat_count": 64,
                "avg_abs_error_after_sec": 0.044,
                "intro_avg_abs_error_after_sec": 0.014,
            },
            "beat_phase_verdict": "risk",
        },
        {"avg_abs_error_after_sec": 0.011},
    )

    assert gated["verdict"] == "daw_locked"
    assert gated["fixed_grid_authoritative"] is True
    assert gated["beat_phase"]["grid_authoritative"] is True


def test_fixed_grid_authority_accepts_clean_beat_phase_when_segment_phase_window_is_noisy():
    gated = _apply_fixed_grid_authority_gate(
        {
            "verdict": "mostly_locked",
            "locked_ratio": 0.88,
            "effective_locked_ratio": 0.8636,
            "overall_phase_window_abs_max_after_sec": 0.0415,
            "overall_phase_window_span_after_sec": 0.0478,
            "unstable_segments": 1,
            "meltdown_segments": 0,
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
            "warp_continuity": {"verdict": "watch"},
            "beat_phase": {
                "beat_count": 256,
                "avg_abs_error_after_sec": 0.0248,
                "intro_avg_abs_error_after_sec": 0.0125,
                "locked": True,
                "intro_locked": True,
                "offbeat_alias": False,
            },
        },
        {"avg_abs_error_after_sec": 0.0131},
    )

    assert gated["verdict"] == "daw_locked"
    assert gated["fixed_grid_authoritative"] is True


def test_build_daw_lock_diagnostics_stays_quiet_for_clean_lock():
    from backend.app import _build_daw_lock_diagnostics

    diagnostics = _build_daw_lock_diagnostics(
        {
            "verdict": "daw_locked",
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        }
    )

    assert diagnostics["issue_count"] == 0
    assert diagnostics["summary"] == "No DAW-lock diagnostic issues detected."
    assert diagnostics["items"] == []
