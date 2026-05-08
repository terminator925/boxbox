from __future__ import annotations

import librosa
import numpy as np

from backend.audio.features import extract_features


def estimate_tempo(y_mono: np.ndarray, sr: int, preview_seconds: int = 120, min_analysis_sr: int = 22050) -> float:
    preview = y_mono[: min(len(y_mono), sr * preview_seconds)]
    analysis_sr = sr
    analysis = preview
    if sr < min_analysis_sr and len(preview) > 0:
        analysis = librosa.resample(preview.astype(np.float32), orig_sr=sr, target_sr=min_analysis_sr)
        analysis_sr = min_analysis_sr
    feat = extract_features(analysis.astype(np.float32), analysis_sr, include_mel=False)
    tempo, _ = librosa.beat.beat_track(
        onset_envelope=feat["onset"],
        sr=analysis_sr,
        hop_length=int(feat["hop_length"]),
        units="time",
        trim=False,
    )
    if isinstance(tempo, np.ndarray):
        tempo = float(np.ravel(tempo)[0])
    return float(tempo)
