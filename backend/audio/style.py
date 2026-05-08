from __future__ import annotations

from typing import Any


def infer_style_profile(event_summary: dict[str, Any], tempo_drift_summary: dict[str, Any]) -> dict[str, Any]:
    kind_counts = dict(event_summary.get("kind_counts") or {})
    total_events = max(int(event_summary.get("event_count", 0)), 1)
    percussive_ratio = float(kind_counts.get("percussive", 0)) / total_events
    harmonic_ratio = float(kind_counts.get("harmonic", 0)) / total_events
    mixed_ratio = float(kind_counts.get("mixed", 0)) / total_events
    strong_ratio = float(event_summary.get("strong_event_count", 0)) / total_events
    density = float(event_summary.get("event_density_per_sec", 0.0))
    drift_pct = float(tempo_drift_summary.get("mean_abs_drift_pct", 0.0))

    if percussive_ratio >= 0.58 and drift_pct <= 6.0:
        profile = "percussive_tight"
        suggested_groove_preserve = 5
        segmented_hybrid = False
    elif harmonic_ratio >= 0.42 and drift_pct >= 10.0:
        profile = "harmonic_expressive"
        suggested_groove_preserve = 30
        segmented_hybrid = True
    elif mixed_ratio >= 0.35 and drift_pct >= 8.0:
        profile = "mixed_drifting"
        suggested_groove_preserve = 22
        segmented_hybrid = True
    elif density >= 5.0 and strong_ratio >= 0.28:
        profile = "dense_rhythmic"
        suggested_groove_preserve = 10
        segmented_hybrid = False
    else:
        profile = "balanced"
        suggested_groove_preserve = 15
        segmented_hybrid = drift_pct >= 8.0

    confidence = max(
        0.35,
        min(
            0.95,
            0.45
            + 0.25 * abs(percussive_ratio - harmonic_ratio)
            + 0.15 * min(drift_pct / 12.0, 1.0)
            + 0.15 * min(strong_ratio / 0.4, 1.0),
        ),
    )
    return {
        "profile": profile,
        "confidence": round(float(confidence), 6),
        "recommended_groove_preserve": int(suggested_groove_preserve),
        "prefer_segmented_hybrid": bool(segmented_hybrid),
        "features": {
            "percussive_ratio": round(float(percussive_ratio), 6),
            "mixed_ratio": round(float(mixed_ratio), 6),
            "harmonic_ratio": round(float(harmonic_ratio), 6),
            "strong_event_ratio": round(float(strong_ratio), 6),
            "event_density_per_sec": round(float(density), 6),
            "mean_abs_drift_pct": round(float(drift_pct), 6),
        },
    }
