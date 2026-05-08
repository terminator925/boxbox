from __future__ import annotations

import warnings

import numpy as np
import librosa


def _resize_feature_rows(feature: np.ndarray, target_rows: int) -> np.ndarray:
    if feature.shape[0] == target_rows:
        return feature
    src = np.arange(feature.shape[0], dtype=np.float32)
    dst = np.linspace(0, max(feature.shape[0] - 1, 1), target_rows, dtype=np.float32)
    out = np.empty((target_rows, feature.shape[1]), dtype=np.float32)
    for idx in range(feature.shape[1]):
        out[:, idx] = np.interp(dst, src, feature[:, idx]).astype(np.float32)
    return out


def _safe_melspectrogram(y_mono: np.ndarray, sr: int, n_fft: int, hop: int, fmax: float) -> np.ndarray:
    freq_bin_hz = sr / max(n_fft, 1)
    usable_bins = max(8, int(np.floor(fmax / max(freq_bin_hz, 1e-6))))
    n_mels = max(8, min(80, usable_bins // 2, int(max(fmax, 2400.0) // 120)))

    while True:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            mel = librosa.feature.melspectrogram(
                y=y_mono,
                sr=sr,
                n_fft=n_fft,
                hop_length=hop,
                n_mels=n_mels,
                fmax=fmax,
            )
        has_empty_filter_warning = any("Empty filters detected" in str(item.message) for item in caught)
        if not has_empty_filter_warning or n_mels <= 8:
            return mel.astype(np.float32)
        n_mels = max(8, n_mels // 2)


def compute_mel_feature(y_mono: np.ndarray, sr: int, *, n_fft: int | None = None, hop: int = 512) -> np.ndarray:
    fft_size = n_fft or min(2048, max(512, 2 ** int(np.floor(np.log2(max(len(y_mono) // 4, 512))))))
    fmax = min(8000.0, sr * 0.48)
    mel = _safe_melspectrogram(y_mono, sr, fft_size, hop, fmax)
    mel = _resize_feature_rows(mel, 80)
    return librosa.power_to_db(mel + 1e-9).astype(np.float32)


def extract_features(y_mono: np.ndarray, sr: int, *, include_mel: bool = True) -> dict:
    hop = 512
    n_fft = min(2048, max(512, 2 ** int(np.floor(np.log2(max(len(y_mono) // 4, 512))))))

    stft = np.abs(librosa.stft(y_mono, n_fft=n_fft, hop_length=hop))
    onset_env = librosa.onset.onset_strength(sr=sr, S=stft, hop_length=hop)
    y_harm, y_perc = librosa.effects.hpss(y_mono)
    stft_h = np.abs(librosa.stft(y_harm, n_fft=n_fft, hop_length=hop))
    stft_p = np.abs(librosa.stft(y_perc, n_fft=n_fft, hop_length=hop))
    onset_h = librosa.onset.onset_strength(sr=sr, S=stft_h, hop_length=hop)
    onset_p = librosa.onset.onset_strength(sr=sr, S=stft_p, hop_length=hop)

    flux = np.maximum(0.0, np.diff(stft, axis=1)).mean(axis=0)
    flux = np.pad(flux, (1, 0), mode="edge")

    novelty = 0.4 * onset_env + 0.3 * onset_h + 0.3 * onset_p + 0.4 * flux
    novelty = (novelty - novelty.min()) / (novelty.max() - novelty.min() + 1e-8)

    times = librosa.frames_to_time(np.arange(len(novelty)), sr=sr, hop_length=hop)
    result = {
        "onset": onset_env.astype(np.float32),
        "onset_harmonic": onset_h.astype(np.float32),
        "onset_percussive": onset_p.astype(np.float32),
        "novelty": novelty.astype(np.float32),
        "times": times.astype(np.float32),
        "hop_length": hop,
    }
    if include_mel:
        result["mel"] = compute_mel_feature(y_mono, sr, n_fft=n_fft, hop=hop)
    return result
