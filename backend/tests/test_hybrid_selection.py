from __future__ import annotations

import numpy as np

from backend import app
from backend.app import (
    _build_segmented_hybrid_target,
    _groove_preserve_candidates,
    _hybrid_alpha_candidates,
    _hybrid_baseline_grid_skip_reason,
    _learned_groove_request,
    _optimize_groove_target,
    _hybrid_search_curves,
    _rank_hybrid_candidates,
    _search_best_hybrid_candidate,
    _segment_blend_weights,
    _select_non_ml_target,
    _select_hybrid_candidate,
    _hybrid_inference_skip_reason,
    _should_skip_hybrid_search,
)


def test_hybrid_selection_reports_guarded_baseline_when_ml_blend_is_zero():
    baseline = {"avg_abs_error_after_sec": 0.03}
    hybrid = {"avg_abs_error_after_sec": 0.02}

    selection, selected_metrics, alpha = _select_hybrid_candidate(baseline, hybrid, 0.0)

    assert selection == "baseline_guarded"
    assert selected_metrics is baseline
    assert alpha == 0.0


def test_hybrid_selection_keeps_true_hybrid_win_when_alpha_is_active():
    baseline = {"avg_abs_error_after_sec": 0.03, "median_signed_error_after_sec": 0.02, "phase_window_abs_max_after_sec": 0.05, "phase_window_span_after_sec": 0.04}
    hybrid = {"avg_abs_error_after_sec": 0.02, "median_signed_error_after_sec": 0.01, "phase_window_abs_max_after_sec": 0.03, "phase_window_span_after_sec": 0.02}

    selection, selected_metrics, alpha = _select_hybrid_candidate(baseline, hybrid, 0.04)

    assert selection == "hybrid_candidate"
    assert selected_metrics is hybrid
    assert alpha == 0.04


def test_hybrid_selection_rejects_better_average_when_phase_lock_is_worse():
    baseline = {"avg_abs_error_after_sec": 0.03, "median_signed_error_after_sec": 0.004, "phase_window_abs_max_after_sec": 0.012, "phase_window_span_after_sec": 0.015}
    hybrid = {"avg_abs_error_after_sec": 0.025, "median_signed_error_after_sec": 0.04, "phase_window_abs_max_after_sec": 0.08, "phase_window_span_after_sec": 0.11}

    selection, selected_metrics, alpha = _select_hybrid_candidate(baseline, hybrid, 0.04)

    assert selection == "baseline"
    assert selected_metrics is baseline
    assert alpha == 0.0


def test_hybrid_alpha_candidates_include_small_search_ladder():
    candidates = _hybrid_alpha_candidates(0.055)

    assert 0.01 in candidates
    assert 0.02 in candidates
    assert 0.03 in candidates
    assert 0.055 in candidates


def test_should_skip_hybrid_search_on_long_track_with_zero_agreement():
    assert _should_skip_hybrid_search(
        240.0,
        0.0,
        {
            "ml_agreement_scale": 0.0,
            "ml_baseline_disagreement_ratio": 1.6,
        },
    ) is True


def test_should_not_skip_hybrid_search_on_short_track():
    assert _should_skip_hybrid_search(
        12.0,
        0.0,
        {
            "ml_agreement_scale": 0.0,
            "ml_baseline_disagreement_ratio": 2.0,
        },
    ) is False


def test_hybrid_inference_skip_reason_for_long_dense_strict_lock_track():
    reason = _hybrid_inference_skip_reason(
        "hybrid",
        297.0,
        {
            "event_density_per_sec": 0.86,
            "strong_event_count": 218,
        },
        {
            "profile": "balanced",
            "confidence": 0.76,
            "prefer_segmented_hybrid": False,
            "features": {
                "strong_event_ratio": 0.85,
                "mixed_ratio": 0.56,
                "harmonic_ratio": 0.05,
                "mean_abs_drift_pct": 6.5,
            },
        },
    )

    assert reason == "strict_lock_dense_long_track"

    feature_derived_reason = _hybrid_inference_skip_reason(
        "hybrid",
        297.0,
        {
            "event_density_per_sec": 0.86,
            "strong_event_count": 218,
        },
        {
            "profile": "balanced",
            "confidence": 0.72,
            "prefer_segmented_hybrid": False,
            "features": {
                "strong_event_ratio": 0.85,
                "mixed_ratio": 0.67,
                "harmonic_ratio": 0.24,
                "mean_abs_drift_pct": 6.6,
            },
        },
    )

    assert feature_derived_reason == "strict_lock_dense_long_track"


def test_hybrid_inference_skip_reason_ignores_short_or_harmonic_tracks():
    assert _hybrid_inference_skip_reason(
        "hybrid",
        60.0,
        {"event_density_per_sec": 0.9, "strong_event_count": 200},
        {
            "profile": "balanced",
            "confidence": 0.8,
            "prefer_segmented_hybrid": False,
            "features": {
                "strong_event_ratio": 0.9,
                "mixed_ratio": 0.6,
                "harmonic_ratio": 0.04,
                "mean_abs_drift_pct": 4.0,
            },
        },
    ) is None

    assert _hybrid_inference_skip_reason(
        "hybrid",
        297.0,
        {"event_density_per_sec": 0.86, "strong_event_count": 218},
        {
            "profile": "balanced",
            "confidence": 0.76,
            "prefer_segmented_hybrid": False,
            "features": {
                "strong_event_ratio": 0.85,
                "mixed_ratio": 0.56,
                "harmonic_ratio": 0.42,
                "mean_abs_drift_pct": 6.5,
            },
        },
    ) is None


def test_hybrid_baseline_grid_skip_reason_allows_strong_projected_grid():
    source_times = np.linspace(0.0, 180.0, 361, dtype=np.float32)
    target_times = source_times.copy()
    grid = np.arange(0.0, 180.5, 0.5, dtype=np.float32)
    onsets_before = np.arange(0.0, 180.0, 0.5, dtype=np.float32)
    segments = [{"start_sec": 0.0, "end_sec": 180.0}]

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        source_times,
        target_times,
        grid,
        onsets_before,
        segments,
        duration_sec=180.0,
        target_bpm=120.0,
        resolution=8,
    )

    assert reason == "baseline_grid_strong_precheck"
    assert diagnostics["fixed_locked_ratio"] == 1.0


def test_hybrid_baseline_grid_skip_reason_blocks_jump_risk():
    source_times = np.linspace(0.0, 180.0, 361, dtype=np.float32)
    target_times = source_times.copy()
    target_times[source_times >= 90.0] += 0.5
    target_times = np.maximum.accumulate(target_times)
    grid = np.arange(0.0, 181.0, 0.5, dtype=np.float32)
    onsets_before = np.arange(0.0, 180.0, 0.5, dtype=np.float32)
    segments = [{"start_sec": 0.0, "end_sec": 180.0}]

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        source_times,
        target_times,
        grid,
        onsets_before,
        segments,
        duration_sec=180.0,
        target_bpm=120.0,
        resolution=8,
    )

    assert reason is None
    assert diagnostics["continuity_verdict"] == "jump_risk"


def test_hybrid_baseline_grid_skip_reason_allows_mostly_strong_long_track(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.0415,
            "median_signed_error_after_sec": 0.012,
            "phase_window_abs_max_after_sec": 0.05,
            "phase_window_span_after_sec": 0.08,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.985,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.975,
        },
    )
    monkeypatch.setattr(app, "_summarize_warp_continuity", lambda *_args, **_kwargs: {"verdict": "watch"})

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 200.0, 401, dtype=np.float32),
        np.linspace(0.0, 200.0, 401, dtype=np.float32),
        np.arange(0.0, 200.5, 0.5, dtype=np.float32),
        np.arange(0.0, 200.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 200.0}],
        duration_sec=200.0,
        target_bpm=120.0,
        resolution=8,
    )

    assert reason == "baseline_grid_mostly_strong_precheck"
    assert diagnostics["fixed_locked_ratio"] == 0.985
    assert diagnostics["segment_locked_ratio"] == 0.975


def test_hybrid_baseline_grid_skip_reason_blocks_mostly_strong_jump_risk(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.0415,
            "median_signed_error_after_sec": 0.012,
            "phase_window_abs_max_after_sec": 0.05,
            "phase_window_span_after_sec": 0.08,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.985,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.975,
        },
    )
    monkeypatch.setattr(app, "_summarize_warp_continuity", lambda *_args, **_kwargs: {"verdict": "jump_risk"})

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 200.0, 401, dtype=np.float32),
        np.linspace(0.0, 200.0, 401, dtype=np.float32),
        np.arange(0.0, 200.5, 0.5, dtype=np.float32),
        np.arange(0.0, 200.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 200.0}],
        duration_sec=200.0,
        target_bpm=120.0,
        resolution=8,
    )

    assert reason is None
    assert diagnostics["continuity_verdict"] == "jump_risk"


def test_hybrid_baseline_grid_skip_reason_allows_sparse_authoritative_long_track(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.033,
            "median_signed_error_after_sec": 0.004,
            "phase_window_abs_max_after_sec": 0.044,
            "phase_window_span_after_sec": 0.06,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 1.0,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.78,
        },
    )
    monkeypatch.setattr(app, "_summarize_warp_continuity", lambda *_args, **_kwargs: {"verdict": "continuous"})

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 220.0, 441, dtype=np.float32),
        np.linspace(0.0, 220.0, 441, dtype=np.float32),
        np.arange(0.0, 220.5, 0.5, dtype=np.float32),
        np.arange(0.0, 220.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 220.0}],
        duration_sec=220.0,
        target_bpm=120.0,
        resolution=8,
    )

    assert reason == "baseline_grid_sparse_authoritative_precheck"
    assert diagnostics["fixed_locked_ratio"] == 1.0


def test_hybrid_baseline_grid_skip_reason_allows_subdivision_alias_long_track(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.011,
            "median_signed_error_after_sec": -0.011,
            "phase_window_abs_max_after_sec": 0.011,
            "phase_window_span_after_sec": 0.011,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.9714,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.9744,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_warp_continuity",
        lambda *_args, **_kwargs: {
            "verdict": "jump_risk",
            "subdivision_alias_jump": True,
            "max_window_offset_jump_sec": 0.167,
            "grid_step_sec": 0.161,
        },
    )

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 216.0, 433, dtype=np.float32),
        np.linspace(0.0, 216.0, 433, dtype=np.float32),
        np.arange(0.0, 216.5, 0.5, dtype=np.float32),
        np.arange(0.0, 216.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 216.0}],
        duration_sec=216.0,
        target_bpm=186.0,
        resolution=8,
    )

    assert reason == "baseline_grid_subdivision_alias_precheck"
    assert diagnostics["continuity_subdivision_alias_jump"] is True


def test_hybrid_baseline_grid_skip_reason_blocks_non_alias_jump_risk(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.011,
            "median_signed_error_after_sec": -0.011,
            "phase_window_abs_max_after_sec": 0.011,
            "phase_window_span_after_sec": 0.011,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.9714,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.9744,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_warp_continuity",
        lambda *_args, **_kwargs: {
            "verdict": "jump_risk",
            "subdivision_alias_jump": False,
            "max_window_offset_jump_sec": 0.167,
            "grid_step_sec": 0.161,
        },
    )

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 216.0, 433, dtype=np.float32),
        np.linspace(0.0, 216.0, 433, dtype=np.float32),
        np.arange(0.0, 216.5, 0.5, dtype=np.float32),
        np.arange(0.0, 216.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 216.0}],
        duration_sec=216.0,
        target_bpm=186.0,
        resolution=8,
    )

    assert reason is None
    assert diagnostics["continuity_verdict"] == "jump_risk"


def test_hybrid_baseline_grid_skip_reason_allows_smoothed_alias_long_track(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.0337,
            "median_signed_error_after_sec": -0.0005,
            "phase_window_abs_max_after_sec": 0.0064,
            "phase_window_span_after_sec": 0.0113,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.9643,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 0.9701,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_warp_continuity",
        lambda *_args, **_kwargs: {
            "verdict": "watch",
            "subdivision_alias_jump": True,
            "max_window_offset_jump_sec": 0.1488,
            "grid_step_sec": 0.1667,
        },
    )

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 350.0, 701, dtype=np.float32),
        np.linspace(0.0, 350.0, 701, dtype=np.float32),
        np.arange(0.0, 350.5, 0.5, dtype=np.float32),
        np.arange(0.0, 350.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 350.0}],
        duration_sec=350.0,
        target_bpm=180.0,
        resolution=8,
    )

    assert reason == "baseline_grid_smoothed_alias_precheck"
    assert diagnostics["continuity_verdict"] == "watch"


def test_hybrid_baseline_grid_skip_reason_allows_repaired_authoritative_long_track(monkeypatch):
    monkeypatch.setattr(
        app,
        "_candidate_metrics_fast",
        lambda *_args, **_kwargs: {
            "avg_abs_error_after_sec": 0.0423,
            "median_signed_error_after_sec": 0.0054,
            "phase_window_abs_max_after_sec": 0.0265,
            "phase_window_span_after_sec": 0.0398,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_fixed_window_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 1.0,
            "unstable_windows": 0,
            "meltdown_windows": 0,
        },
    )
    monkeypatch.setattr(app, "_summarize_segments", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        app,
        "_summarize_metronome_lock",
        lambda *_args, **_kwargs: {
            "effective_locked_ratio": 1.0,
        },
    )
    monkeypatch.setattr(
        app,
        "_summarize_warp_continuity",
        lambda *_args, **_kwargs: {
            "verdict": "subdivision_alias",
            "subdivision_alias_jump": True,
            "max_window_offset_jump_sec": 0.2179,
            "grid_step_sec": 0.24,
        },
    )

    reason, diagnostics = _hybrid_baseline_grid_skip_reason(
        "hybrid",
        np.linspace(0.0, 232.0, 465, dtype=np.float32),
        np.linspace(0.0, 232.0, 465, dtype=np.float32),
        np.arange(0.0, 232.5, 0.5, dtype=np.float32),
        np.arange(0.0, 232.0, 0.5, dtype=np.float32),
        [{"start_sec": 0.0, "end_sec": 232.0}],
        duration_sec=232.0,
        target_bpm=125.0,
        resolution=8,
    )

    assert reason == "baseline_grid_repaired_authoritative_precheck"
    assert diagnostics["fixed_locked_ratio"] == 1.0


def test_select_non_ml_target_uses_optimized_baseline_for_hybrid_skip():
    baseline_target = np.array([0.0, 0.6, 1.2], dtype=np.float32)
    onset_grid_target = np.array([0.0, 0.5, 1.0], dtype=np.float32)
    baseline_metrics = {"avg_abs_error_after_sec": 0.03}

    final_target, metrics, warp_selection, groove_preserve = _select_non_ml_target(
        "hybrid",
        False,
        False,
        "baseline_grid_strong_precheck",
        baseline_target,
        baseline_metrics,
        onset_grid_target,
        onset_grid_target.copy(),
        np.array([0.0, 1.0, 2.0], dtype=np.float32),
        np.array([0.0, 0.5, 1.0], dtype=np.float32),
        np.array([0.1, 0.6], dtype=np.float32),
        {},
        20,
    )

    assert np.array_equal(final_target, baseline_target)
    assert metrics is baseline_metrics
    assert warp_selection == "baseline"
    assert groove_preserve == 20


def test_select_non_ml_target_preserves_strict_lock_onset_grid(monkeypatch):
    requested_metrics = {"avg_abs_error_after_sec": 0.02}
    candidate_metrics: dict[str, dict] = {}
    monkeypatch.setattr(app, "_candidate_metrics_fast", lambda *_args, **_kwargs: requested_metrics)

    final_target, metrics, warp_selection, groove_preserve = _select_non_ml_target(
        "hybrid",
        False,
        True,
        "strict_lock_dense_long_track",
        np.array([0.0, 0.6, 1.2], dtype=np.float32),
        {"avg_abs_error_after_sec": 0.03},
        np.array([0.0, 0.5, 1.0], dtype=np.float32),
        np.array([0.0, 0.55, 1.1], dtype=np.float32),
        np.array([0.0, 1.0, 2.0], dtype=np.float32),
        np.array([0.0, 0.5, 1.0], dtype=np.float32),
        np.array([0.1, 0.6], dtype=np.float32),
        candidate_metrics,
        20,
    )

    assert np.array_equal(final_target, np.array([0.0, 0.5, 1.0], dtype=np.float32))
    assert metrics is requested_metrics
    assert warp_selection == "strict_onset_grid"
    assert groove_preserve == 20
    assert candidate_metrics["requested_mode_projected"] is requested_metrics


def test_select_non_ml_target_preserves_requested_target_after_ml_disagreement(monkeypatch):
    requested_metrics = {"avg_abs_error_after_sec": 0.045}
    candidate_metrics: dict[str, dict] = {}
    monkeypatch.setattr(app, "_candidate_metrics_fast", lambda *_args, **_kwargs: requested_metrics)

    requested_target = np.array([0.0, 0.55, 1.1], dtype=np.float32)
    final_target, metrics, warp_selection, groove_preserve = _select_non_ml_target(
        "hybrid",
        False,
        False,
        "ml_disagreement_too_high_for_long_track",
        np.array([0.0, 0.6, 1.2], dtype=np.float32),
        {"avg_abs_error_after_sec": 0.03},
        np.array([0.0, 0.5, 1.0], dtype=np.float32),
        requested_target,
        np.array([0.0, 1.0, 2.0], dtype=np.float32),
        np.array([0.0, 0.5, 1.0], dtype=np.float32),
        np.array([0.1, 0.6], dtype=np.float32),
        candidate_metrics,
        20,
    )

    assert np.array_equal(final_target, requested_target)
    assert metrics is requested_metrics
    assert warp_selection == "baseline"
    assert groove_preserve == 20
    assert candidate_metrics["requested_mode"] is requested_metrics


def test_groove_preserve_candidates_include_safer_values_above_request():
    candidates = _groove_preserve_candidates(20)

    assert 20 in candidates
    assert 50 in candidates
    assert 100 in candidates


def test_groove_preserve_candidates_respect_cap():
    candidates = _groove_preserve_candidates(50, max_preserve=50)

    assert 50 in candidates
    assert 60 not in candidates
    assert 100 not in candidates


def test_learned_groove_request_ignores_low_confidence_memory():
    guided = _learned_groove_request(
        50,
        {
            "guided_groove_preserve": 20,
            "confidence": 0.0,
            "evidence_count": 28,
        },
    )

    assert guided == 50


def test_search_best_hybrid_candidate_picks_lowest_error_curve(monkeypatch):
    def fake_search(source_times, baseline_target, ml_curve, grid, onsets_before, base_alpha):
        metrics = {"avg_abs_error_after_sec": float(ml_curve["candidate_error"])}
        return np.linspace(0.0, 1.0, 4, dtype=np.float32), metrics, 0.03

    monkeypatch.setattr(app, "_search_hybrid_candidate", fake_search)

    _, metrics, alpha, info = _search_best_hybrid_candidate(
        np.linspace(0.0, 1.0, 4, dtype=np.float32),
        np.linspace(0.0, 1.0, 4, dtype=np.float32),
        [
            {"candidate_name": "mixed", "model_file": "mixed.pt", "candidate_error": 0.03, "confidence": 0.6},
            {"candidate_name": "routed", "model_file": "routed", "candidate_error": 0.02, "confidence": 0.7},
        ],
        np.linspace(0.0, 1.0, 4, dtype=np.float32),
        np.array([0.1, 0.2], dtype=np.float32),
        0.04,
    )

    assert metrics["avg_abs_error_after_sec"] == 0.02
    assert alpha == 0.03
    assert info["candidate_name"] == "routed"


def test_hybrid_search_curves_defaults_to_core4(monkeypatch):
    monkeypatch.delenv("BOXBOX_HYBRID_SEARCH_STRATEGY", raising=False)
    curves = [
        {"candidate_name": "boxbox_latest", "model_file": "boxbox_latest.pt", "confidence": 0.6},
        {"candidate_name": "boxbox_before_groove_860", "model_file": "boxbox_before_groove_860.pt", "confidence": 0.5},
        {"candidate_name": "boxbox_mixed_legacy_candidate_r1884", "model_file": "boxbox_mixed_legacy_candidate_r1884.pt", "confidence": 0.8},
        {"candidate_name": "boxbox_legacy_specialist_r1730", "model_file": "boxbox_legacy_specialist_r1730.pt", "confidence": 0.9},
        {
            "candidate_name": "routed",
            "model_file": "boxbox_latest.pt,boxbox_before_groove_860.pt",
            "confidence": 0.7,
            "routing_weights": {
                "boxbox_latest.pt": 0.3,
                "boxbox_before_groove_860.pt": 0.7,
            },
        },
    ]

    selected = _hybrid_search_curves(curves)

    names = [curve["candidate_name"] for curve in selected]
    assert names == ["boxbox_latest", "boxbox_before_groove_860", "boxbox_mixed_legacy_candidate_r1884", "routed"]


def test_hybrid_search_curves_keeps_all_candidates_when_requested(monkeypatch):
    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "all")
    curves = [
        {"candidate_name": "boxbox_latest", "model_file": "boxbox_latest.pt", "confidence": 0.6},
        {"candidate_name": "boxbox_before_groove_860", "model_file": "boxbox_before_groove_860.pt", "confidence": 0.5},
        {
            "candidate_name": "routed",
            "model_file": "boxbox_latest.pt,boxbox_before_groove_860.pt",
            "confidence": 0.7,
            "routing_weights": {
                "boxbox_latest.pt": 0.3,
                "boxbox_before_groove_860.pt": 0.7,
            },
        },
    ]

    selected = _hybrid_search_curves(curves)

    names = [curve["candidate_name"] for curve in selected]
    assert names == ["boxbox_latest", "boxbox_before_groove_860", "routed"]


def test_hybrid_search_curves_can_limit_to_routed_plus_top_confidence(monkeypatch):
    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "routed_top1")
    curves = [
        {"candidate_name": "boxbox_latest", "model_file": "boxbox_latest.pt", "confidence": 0.6},
        {"candidate_name": "boxbox_before_groove_860", "model_file": "boxbox_before_groove_860.pt", "confidence": 0.5},
        {"candidate_name": "boxbox_mixed_legacy_candidate_r1884", "model_file": "boxbox_mixed_legacy_candidate_r1884.pt", "confidence": 0.8},
        {
            "candidate_name": "routed",
            "model_file": "boxbox_latest.pt,boxbox_before_groove_860.pt",
            "confidence": 0.7,
            "routing_weights": {
                "boxbox_latest.pt": 0.3,
                "boxbox_before_groove_860.pt": 0.7,
            },
        },
    ]

    selected = _hybrid_search_curves(curves)

    names = [curve["candidate_name"] for curve in selected]
    assert names == ["routed", "boxbox_mixed_legacy_candidate_r1884"]


def test_hybrid_search_curves_can_limit_to_core_winners(monkeypatch):
    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "core4")
    curves = [
        {"candidate_name": "boxbox_latest", "model_file": "boxbox_latest.pt", "confidence": 0.6},
        {"candidate_name": "boxbox_legacy_specialist_r1730", "model_file": "boxbox_legacy_specialist_r1730.pt", "confidence": 0.9},
        {"candidate_name": "boxbox_before_groove_860", "model_file": "boxbox_before_groove_860.pt", "confidence": 0.5},
        {"candidate_name": "boxbox_mixed_legacy_candidate_r1884", "model_file": "boxbox_mixed_legacy_candidate_r1884.pt", "confidence": 0.8},
        {"candidate_name": "routed", "model_file": "ensemble", "confidence": 0.7},
    ]

    selected = _hybrid_search_curves(curves)

    names = [curve["candidate_name"] for curve in selected]
    assert names == ["boxbox_latest", "boxbox_before_groove_860", "boxbox_mixed_legacy_candidate_r1884", "routed"]


def test_hybrid_search_curves_can_add_legacy_specialist_to_core5(monkeypatch):
    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "core5")
    curves = [
        {"candidate_name": "boxbox_latest", "model_file": "boxbox_latest.pt", "confidence": 0.6},
        {"candidate_name": "boxbox_legacy_specialist_r1730", "model_file": "boxbox_legacy_specialist_r1730.pt", "confidence": 0.9},
        {"candidate_name": "boxbox_before_groove_860", "model_file": "boxbox_before_groove_860.pt", "confidence": 0.5},
        {"candidate_name": "boxbox_mixed_legacy_candidate_r1884", "model_file": "boxbox_mixed_legacy_candidate_r1884.pt", "confidence": 0.8},
        {"candidate_name": "boxbox_legacy_specialist_r1884_qf", "model_file": "boxbox_legacy_specialist_r1884_qf.pt", "confidence": 0.7},
        {"candidate_name": "routed", "model_file": "ensemble", "confidence": 0.7},
    ]

    selected = _hybrid_search_curves(curves)

    names = [curve["candidate_name"] for curve in selected]
    assert names == [
        "boxbox_latest",
        "boxbox_legacy_specialist_r1730",
        "boxbox_before_groove_860",
        "boxbox_mixed_legacy_candidate_r1884",
        "routed",
    ]


def test_rank_hybrid_candidates_keeps_curve_seed_candidate(monkeypatch):
    monkeypatch.setattr(app, "_blend_ml_with_baseline", lambda *args, **kwargs: (np.array([0.0, 1.0], dtype=np.float32), 0.055, {}))
    monkeypatch.setattr(app, "_candidate_metrics_fast", lambda source_times, target_times, grid, onsets_before: {"avg_abs_error_after_sec": float(target_times[0])})
    monkeypatch.setattr(app, "_hybrid_alpha_candidates", lambda alpha: [0.01, 0.055, 0.08])

    ranked = _rank_hybrid_candidates(
        np.array([0.0, 1.0], dtype=np.float32),
        np.array([0.2, 1.0], dtype=np.float32),
        [
            {
                "candidate_name": "curve_a",
                "model_file": "a.pt",
                "confidence": 0.5,
                "source_times": np.array([0.0, 1.0], dtype=np.float32),
                "target_times": np.array([0.0, 1.0], dtype=np.float32),
            }
        ],
        np.array([0.0, 1.0], dtype=np.float32),
        np.array([0.1], dtype=np.float32),
        0.02,
        top_k=1,
    )

    alphas = [round(float(entry[2]), 3) for entry in ranked]
    assert 0.055 in alphas


def test_rank_hybrid_candidates_includes_direct_ml_candidate(monkeypatch):
    monkeypatch.setattr(app, "_blend_ml_with_baseline", lambda *args, **kwargs: (np.array([0.0, 1.0], dtype=np.float32), 0.04, {}))
    monkeypatch.setattr(app, "_candidate_metrics_fast", lambda source_times, target_times, grid, onsets_before: {"avg_abs_error_after_sec": float(target_times[0])})
    monkeypatch.setattr(app, "_hybrid_alpha_candidates", lambda alpha: [0.01, 0.04])

    ranked = _rank_hybrid_candidates(
        np.array([0.0, 1.0], dtype=np.float32),
        np.array([0.2, 1.0], dtype=np.float32),
        [
            {
                "candidate_name": "curve_a",
                "model_file": "a.pt",
                "confidence": 0.7,
                "source_times": np.array([0.0, 1.0], dtype=np.float32),
                "target_times": np.array([0.0, 1.0], dtype=np.float32),
            }
        ],
        np.array([0.0, 1.0], dtype=np.float32),
        np.array([0.1], dtype=np.float32),
        0.02,
        top_k=1,
    )

    assert any(float(entry[2]) == 1.0 for entry in ranked)


def test_segment_blend_weights_peak_inside_segment():
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)

    weights = _segment_blend_weights(source_times, 2.0, 6.0, transition_sec=1.0)

    assert weights[0] == 0.0
    assert weights[-1] == 0.0
    assert weights[4] == 1.0
    assert weights[2] == 0.0
    assert weights[6] == 0.0
    assert weights[3] > 0.0
    assert weights[5] > 0.0


def test_build_segmented_hybrid_target_uses_local_best_candidate(monkeypatch):
    source_times = np.linspace(0.0, 10.0, 11, dtype=np.float32)
    baseline_target = source_times.copy()
    grid = source_times.copy()
    onsets_before = np.array([1.9, 3.1, 7.6, 8.9], dtype=np.float32)
    segments = [
        {"start_sec": 0.0, "end_sec": 5.0},
        {"start_sec": 5.0, "end_sec": 10.0},
    ]

    early_candidate = baseline_target.copy()
    early_candidate[1:5] -= 0.2
    late_candidate = baseline_target.copy()
    late_candidate[6:10] -= 0.3

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if np.allclose(target_times_arg, baseline_target):
            score = 0.05
        elif end_sec <= 5.0 and np.allclose(target_times_arg, early_candidate):
            score = 0.02
        elif start_sec >= 5.0 and np.allclose(target_times_arg, late_candidate):
            score = 0.01
        else:
            score = 0.07
        return {
            "avg_abs_error_before_sec": 0.08,
            "avg_abs_error_after_sec": score,
            "improvement_sec": 0.08 - score,
            "improvement_pct": 0.0,
            "num_events_before": 2,
            "num_events_after": 2,
        }

    monkeypatch.setattr(app, "_segment_timing_metrics", fake_segment_metrics)

    segmented_target, decisions = _build_segmented_hybrid_target(
        source_times,
        baseline_target,
        [
            (
                early_candidate,
                {"avg_abs_error_after_sec": 0.01},
                0.02,
                {"candidate_name": "early", "model_file": "early.pt"},
            ),
            (
                late_candidate,
                {"avg_abs_error_after_sec": 0.01},
                0.03,
                {"candidate_name": "late", "model_file": "late.pt"},
            ),
        ],
        grid,
        onsets_before,
        segments,
    )

    assert len(decisions) == 2
    assert decisions[0]["candidate_name"] == "early"
    assert decisions[1]["candidate_name"] == "late"
    assert segmented_target[3] < baseline_target[3]
    assert segmented_target[8] < baseline_target[8]


def test_optimize_groove_target_prefers_safer_candidate_when_local_regressions_are_catastrophic(monkeypatch):
    source_times = np.array([0.0, 1.0, 2.0], dtype=np.float32)
    target_times = np.array([0.0, 0.5, 2.0], dtype=np.float32)
    grid = source_times.copy()
    onsets_before = np.array([0.5, 1.5], dtype=np.float32)
    segments = [{"start_sec": 0.0, "end_sec": 2.0}]

    def fake_candidate_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg):
        if float(target_times_arg[1]) < 0.65:
            return {"avg_abs_error_after_sec": 0.01}
        if float(target_times_arg[1]) < 0.9:
            return {"avg_abs_error_after_sec": 0.02}
        return {"avg_abs_error_after_sec": 0.03}

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        if float(target_times_arg[1]) < 0.65:
            after = 1.2
        elif float(target_times_arg[1]) < 0.9:
            after = 0.28
        else:
            after = 0.11
        return {
            "avg_abs_error_before_sec": 0.1,
            "avg_abs_error_after_sec": after,
            "improvement_sec": 0.1 - after,
            "improvement_pct": 0.0,
            "num_events_before": 2,
            "num_events_after": 2,
        }

    monkeypatch.setattr(app, "_candidate_metrics_fast", fake_candidate_metrics)
    monkeypatch.setattr(app, "_segment_timing_metrics", fake_segment_metrics)

    _, groove_preserve, metrics = _optimize_groove_target(
        source_times,
        target_times,
        20,
        grid,
        onsets_before,
        segments,
    )

    assert groove_preserve == 100
    assert metrics["avg_abs_error_after_sec"] == 0.03


def test_optimize_groove_target_respects_requested_cap_when_safer_values_disabled(monkeypatch):
    source_times = np.array([0.0, 1.0, 2.0], dtype=np.float32)
    target_times = np.array([0.0, 0.5, 2.0], dtype=np.float32)
    grid = source_times.copy()
    onsets_before = np.array([0.5, 1.5], dtype=np.float32)
    segments = [{"start_sec": 0.0, "end_sec": 2.0}]

    def fake_candidate_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg):
        return {"avg_abs_error_after_sec": float(target_times_arg[1])}

    def fake_segment_metrics(source_times_arg, target_times_arg, grid_arg, onsets_before_arg, start_sec, end_sec):
        return {
            "avg_abs_error_before_sec": 0.1,
            "avg_abs_error_after_sec": float(target_times_arg[1]),
            "improvement_sec": 0.1 - float(target_times_arg[1]),
            "improvement_pct": 0.0,
            "num_events_before": 2,
            "num_events_after": 2,
        }

    monkeypatch.setattr(app, "_candidate_metrics_fast", fake_candidate_metrics)
    monkeypatch.setattr(app, "_segment_timing_metrics", fake_segment_metrics)

    _, groove_preserve, _ = _optimize_groove_target(
        source_times,
        target_times,
        50,
        grid,
        onsets_before,
        segments,
        allow_safer=False,
        max_preserve=50,
    )

    assert groove_preserve <= 50
