from __future__ import annotations

import librosa
import numpy as np

from backend.audio.features import extract_features


def onset_envelope(y_mono: np.ndarray, sr: int) -> tuple[np.ndarray, int]:
    feat = extract_features(y_mono.astype(np.float32), sr, include_mel=False)
    return feat["onset"], int(feat["hop_length"])


def detect_onsets(y_mono: np.ndarray, sr: int, units: str = "time") -> np.ndarray:
    envelope, hop = onset_envelope(y_mono, sr)
    return detect_onsets_from_envelope(envelope, hop, sr, units=units)


def detect_onsets_from_envelope(envelope: np.ndarray, hop_length: int, sr: int, units: str = "time") -> np.ndarray:
    return librosa.onset.onset_detect(
        onset_envelope=np.asarray(envelope, dtype=np.float32),
        sr=sr,
        hop_length=int(hop_length),
        units=units,
        backtrack=False,
    )
