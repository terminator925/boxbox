from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks


def find_segments(novelty: np.ndarray, times: np.ndarray, min_segment_sec: float = 1.5) -> list[float]:
    distance_frames = max(1, int(min_segment_sec / np.median(np.diff(times))))
    height = np.percentile(novelty, 70)
    peaks, _ = find_peaks(novelty, distance=distance_frames, height=height)

    boundaries = [float(times[0])]
    for peak in peaks:
        t = float(times[peak])
        if t - boundaries[-1] >= min_segment_sec:
            boundaries.append(t)
    if float(times[-1]) - boundaries[-1] < 0.25:
        boundaries[-1] = float(times[-1])
    elif boundaries[-1] < float(times[-1]):
        boundaries.append(float(times[-1]))
    return boundaries
