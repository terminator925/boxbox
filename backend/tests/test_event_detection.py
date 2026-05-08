from __future__ import annotations

import numpy as np

from backend.audio.events import detect_events, summarize_events


def _make_click_track(sr: int = 22050, step_sec: float = 0.5, count: int = 12) -> np.ndarray:
    duration = step_sec * (count + 1)
    n_samples = int(round(duration * sr))
    y = np.zeros(n_samples, dtype=np.float32)
    click_len = max(8, int(0.01 * sr))
    click = np.hanning(click_len).astype(np.float32)
    for idx in range(count):
        start = int(round((0.2 + idx * step_sec) * sr))
        end = min(n_samples, start + click_len)
        y[start:end] += click[: end - start]
    return np.clip(y, -1.0, 1.0)


def test_detect_events_finds_click_track_attacks():
    sr = 22050
    y = _make_click_track(sr=sr, step_sec=0.5, count=10)

    events = detect_events(y, sr)

    assert len(events) >= 8
    assert events[0]["time_sec"] >= 0.15
    assert events[0]["time_sec"] <= 0.3
    assert all(event["confidence"] >= 0.0 for event in events)


def test_summarize_events_reports_density_and_counts():
    events = [
        {"time_sec": 0.2, "strength": 0.9, "prominence": 0.4, "confidence": 0.8, "kind": "percussive", "accent": True},
        {"time_sec": 0.7, "strength": 0.6, "prominence": 0.3, "confidence": 0.7, "kind": "mixed", "accent": False},
        {"time_sec": 1.3, "strength": 0.8, "prominence": 0.5, "confidence": 0.9, "kind": "harmonic", "accent": True},
    ]

    summary = summarize_events(events, duration_sec=2.0)

    assert summary["event_count"] == 3
    assert summary["strong_event_count"] == 2
    assert summary["event_density_per_sec"] == 1.5
    assert summary["kind_counts"]["percussive"] == 1
    assert summary["kind_counts"]["mixed"] == 1
    assert summary["kind_counts"]["harmonic"] == 1
