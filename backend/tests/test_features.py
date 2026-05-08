from __future__ import annotations

import warnings

import numpy as np

from backend.audio.features import extract_features


def test_extract_features_handles_low_bandwidth_audio_without_empty_mel_warning():
    sr = 4000
    t = np.linspace(0.0, 2.0, int(sr * 2.0), endpoint=False, dtype=np.float32)
    y = (0.2 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        feat = extract_features(y, sr)

    assert feat["mel"].shape[0] == 80
    assert feat["mel"].shape[1] > 0
    assert not any("Empty filters detected" in str(item.message) for item in caught)
