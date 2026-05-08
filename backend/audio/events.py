from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import find_peaks

from backend.audio.features import extract_features


@dataclass(frozen=True)
class DetectedEvent:
    time_sec: float
    strength: float
    prominence: float
    confidence: float
    kind: str
    accent: bool


def _normalize(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32, copy=False)
    low = float(values.min(initial=0.0))
    high = float(values.max(initial=0.0))
    if high - low <= 1e-8:
        return np.zeros_like(values, dtype=np.float32)
    return ((values - low) / (high - low)).astype(np.float32)


def _event_kind(local_percussive_ratio: float) -> str:
    if local_percussive_ratio >= 0.62:
        return "percussive"
    if local_percussive_ratio >= 0.40:
        return "mixed"
    return "harmonic"


def detect_events(
    y_mono: np.ndarray,
    sr: int,
    *,
    features: dict | None = None,
    max_events: int = 256,
    min_spacing_sec: float = 0.08,
    analysis_window_sec: float = 0.10,
) -> list[dict[str, object]]:
    if len(y_mono) == 0:
        return []
    feat = features or extract_features(y_mono, sr, include_mel=False)
    onset = _normalize(np.asarray(feat["onset"], dtype=np.float32))
    novelty = _normalize(np.asarray(feat["novelty"], dtype=np.float32))
    times = np.asarray(feat["times"], dtype=np.float32)
    if len(times) == 0:
        return []

    combined = (0.65 * onset + 0.35 * novelty).astype(np.float32)
    frame_dt = float(np.median(np.diff(times))) if len(times) > 1 else max(len(y_mono) / sr, 1e-3)
    min_distance = max(1, int(round(min_spacing_sec / max(frame_dt, 1e-6))))
    prominence_threshold = float(np.percentile(combined, 65)) if len(combined) else 0.0
    height_threshold = float(np.percentile(combined, 60)) if len(combined) else 0.0
    peaks, props = find_peaks(combined, distance=min_distance, prominence=prominence_threshold * 0.35, height=height_threshold)
    if len(peaks) == 0:
        return []

    peak_strengths = combined[peaks]
    prominences = np.asarray(props.get("prominences", np.zeros(len(peaks), dtype=np.float32)), dtype=np.float32)
    order = np.argsort(-(peak_strengths + 0.5 * prominences))
    peaks = peaks[order][:max_events]
    peak_strengths = peak_strengths[order][:max_events]
    prominences = prominences[order][:max_events]

    accent_threshold = float(np.percentile(peak_strengths, 80)) if len(peak_strengths) else 1.0
    onset_harmonic = _normalize(np.asarray(feat.get("onset_harmonic", np.zeros_like(onset)), dtype=np.float32))
    onset_percussive = _normalize(np.asarray(feat.get("onset_percussive", np.zeros_like(onset)), dtype=np.float32))
    frame_window = max(1, int(round(analysis_window_sec / max(frame_dt, 1e-6))))

    detected: list[DetectedEvent] = []
    prominence_norm = _normalize(prominences)
    ranked = list(zip(peaks, peak_strengths, prominences, prominence_norm))
    for peak, strength, prominence, prominence_score in sorted(ranked, key=lambda item: float(times[item[0]])):
        time_sec = float(times[int(peak)])
        frame = int(peak)
        left = max(0, frame - frame_window // 2)
        right = min(len(onset_harmonic), frame + frame_window // 2 + 1)
        harm_energy = float(np.mean(onset_harmonic[left:right])) if right > left else 0.0
        perc_energy = float(np.mean(onset_percussive[left:right])) if right > left else 0.0
        local_percussive_ratio = float(np.clip(perc_energy / max(harm_energy + perc_energy, 1e-8), 0.0, 1.0))
        confidence = float(np.clip(0.7 * strength + 0.3 * prominence_score, 0.0, 1.0))
        detected.append(
            DetectedEvent(
                time_sec=time_sec,
                strength=float(np.clip(strength, 0.0, 1.0)),
                prominence=float(max(prominence, 0.0)),
                confidence=confidence,
                kind=_event_kind(local_percussive_ratio),
                accent=bool(strength >= accent_threshold),
            )
        )

    return [
        {
            "time_sec": round(item.time_sec, 6),
            "strength": round(item.strength, 6),
            "prominence": round(item.prominence, 6),
            "confidence": round(item.confidence, 6),
            "kind": item.kind,
            "accent": item.accent,
        }
        for item in detected
    ]


def summarize_events(events: list[dict[str, object]], duration_sec: float) -> dict[str, object]:
    if not events:
        return {
            "event_count": 0,
            "strong_event_count": 0,
            "event_density_per_sec": 0.0,
            "median_ioi_sec": 0.0,
            "kind_counts": {"percussive": 0, "mixed": 0, "harmonic": 0},
        }
    times = np.asarray([float(event["time_sec"]) for event in events], dtype=np.float32)
    strengths = np.asarray([float(event["strength"]) for event in events], dtype=np.float32)
    kind_counts = {"percussive": 0, "mixed": 0, "harmonic": 0}
    for event in events:
        kind = str(event.get("kind") or "mixed")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    ioi = np.diff(times) if len(times) > 1 else np.array([], dtype=np.float32)
    return {
        "event_count": int(len(events)),
        "strong_event_count": int(np.count_nonzero(strengths >= 0.7)),
        "event_density_per_sec": float(len(events) / max(duration_sec, 1e-6)),
        "median_ioi_sec": float(np.median(ioi)) if len(ioi) else 0.0,
        "kind_counts": kind_counts,
    }
