from __future__ import annotations

import warnings

import numpy as np

from backend.audio.tempo import estimate_tempo


def test_estimate_tempo_handles_low_sample_rate_without_empty_mel_warning():
    sr = 4000
    duration = 4.0
    t = np.linspace(0.0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
    y = np.zeros_like(t)
    click_len = max(8, int(sr * 0.01))
    click = np.hanning(click_len).astype(np.float32)
    for onset in np.arange(0.0, duration, 0.5):
        start = int(onset * sr)
        end = min(len(y), start + click_len)
        y[start:end] += click[: end - start]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bpm = estimate_tempo(y, sr)

    assert np.isfinite(bpm)
    assert bpm >= 0.0
    assert not any("Empty filters detected" in str(item.message) for item in caught)
