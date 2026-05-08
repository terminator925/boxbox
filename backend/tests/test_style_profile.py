from __future__ import annotations

from backend.app import _should_prefer_segmented_hybrid, _style_guided_groove_request
from backend.audio.style import infer_style_profile


def test_infer_style_profile_detects_percussive_tight_material():
    event_summary = {
        "event_count": 20,
        "strong_event_count": 8,
        "event_density_per_sec": 6.0,
        "kind_counts": {"percussive": 14, "mixed": 4, "harmonic": 2},
    }
    drift_summary = {"mean_abs_drift_pct": 3.0}

    profile = infer_style_profile(event_summary, drift_summary)

    assert profile["profile"] == "percussive_tight"
    assert profile["recommended_groove_preserve"] <= 10
    assert profile["prefer_segmented_hybrid"] is False


def test_infer_style_profile_detects_harmonic_expressive_material():
    event_summary = {
        "event_count": 20,
        "strong_event_count": 4,
        "event_density_per_sec": 2.5,
        "kind_counts": {"percussive": 3, "mixed": 7, "harmonic": 10},
    }
    drift_summary = {"mean_abs_drift_pct": 12.0}

    profile = infer_style_profile(event_summary, drift_summary)

    assert profile["profile"] == "harmonic_expressive"
    assert profile["recommended_groove_preserve"] >= 20
    assert profile["prefer_segmented_hybrid"] is True


def test_style_guided_groove_request_blends_toward_confident_recommendation():
    guided = _style_guided_groove_request(
        50,
        {
            "recommended_groove_preserve": 10,
            "confidence": 0.9,
        },
    )

    assert guided == 38


def test_style_guided_groove_request_respects_low_confidence_request():
    guided = _style_guided_groove_request(
        50,
        {
            "recommended_groove_preserve": 10,
            "confidence": 0.4,
        },
    )

    assert guided == 50


def test_segmented_hybrid_preference_requires_confidence():
    assert _should_prefer_segmented_hybrid({"prefer_segmented_hybrid": True, "confidence": 0.8}) is True
    assert _should_prefer_segmented_hybrid({"prefer_segmented_hybrid": True, "confidence": 0.4}) is False
