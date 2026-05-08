from __future__ import annotations

import numpy as np
import librosa

from backend.audio.onsets import onset_envelope


RESOLUTION_MAP = {
    4: "quarter",
    8: "eighth",
    16: "sixteenth",
}


def _base_step(bpm: float, resolution: int) -> float:
    step = (60.0 / bpm) * (4.0 / float(resolution))
    if step <= 0:
        raise ValueError("Invalid BPM/resolution")
    return float(step)


def _sanitize_drift_windows(
    duration_sec: float,
    bpm: float,
    drift_windows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if duration_sec <= 0 or len(drift_windows) < 4:
        return []

    cleaned: list[dict[str, object]] = []
    strong_total = 0
    strong_stable = 0
    alias_like = 0
    for item in drift_windows:
        time_sec = float(item.get("time_sec", 0.0))
        local_bpm = float(item.get("local_bpm", bpm))
        confidence = float(item.get("confidence", 0.0))
        if not np.isfinite(time_sec) or not np.isfinite(local_bpm) or not np.isfinite(confidence):
            continue
        if time_sec < 0.0 or time_sec > duration_sec or local_bpm <= 0.0:
            continue

        ratio = local_bpm / max(float(bpm), 1e-6)
        strong = confidence >= 0.55
        is_alias = min(abs(ratio - 0.5), abs(ratio - 2.0), abs(ratio - 0.25), abs(ratio - 4.0)) <= 0.12
        if strong:
            strong_total += 1
            if 0.88 <= ratio <= 1.12:
                strong_stable += 1
            if is_alias:
                alias_like += 1
        if is_alias and confidence >= 0.45:
            continue
        cleaned.append(
            {
                "time_sec": time_sec,
                "local_bpm": local_bpm,
                "confidence": confidence,
            }
        )

    if len(cleaned) < 4:
        return []
    if strong_total >= 4:
        stable_ratio = strong_stable / max(strong_total, 1)
        alias_ratio = alias_like / max(strong_total, 1)
        if stable_ratio < 0.7 or alias_ratio > 0.08:
            return []
    return cleaned


def _adaptive_grid(
    duration_sec: float,
    bpm: float,
    resolution: int,
    drift_windows: list[dict[str, object]],
    *,
    max_drift_pct: float = 12.0,
) -> np.ndarray:
    if duration_sec <= 0:
        return np.array([0.0], dtype=np.float32)
    drift_windows = _sanitize_drift_windows(duration_sec, bpm, drift_windows)
    if len(drift_windows) < 4:
        return np.array([], dtype=np.float32)

    times = np.asarray([float(item.get("time_sec", 0.0)) for item in drift_windows], dtype=np.float32)
    local_bpm = np.asarray([float(item.get("local_bpm", bpm)) for item in drift_windows], dtype=np.float32)
    confidence = np.asarray([float(item.get("confidence", 0.0)) for item in drift_windows], dtype=np.float32)
    valid = np.isfinite(times) & np.isfinite(local_bpm) & (times >= 0.0) & (times <= duration_sec)
    if int(np.count_nonzero(valid)) < 4:
        return np.array([], dtype=np.float32)

    times = times[valid]
    local_bpm = local_bpm[valid]
    confidence = confidence[valid]
    order = np.argsort(times)
    times = times[order]
    local_bpm = local_bpm[order]
    confidence = confidence[order]

    lower = float(bpm) * (1.0 - (max_drift_pct / 100.0))
    upper = float(bpm) * (1.0 + (max_drift_pct / 100.0))
    local_bpm = np.clip(local_bpm, lower, upper)
    if len(local_bpm) >= 5:
        kernel = np.array([1.0, 2.0, 3.0, 2.0, 1.0], dtype=np.float32)
        kernel = kernel / kernel.sum()
        padded = np.pad(local_bpm, (2, 2), mode="edge")
        local_bpm = np.convolve(padded, kernel, mode="same")[2:-2]

    sample_times = np.linspace(0.0, duration_sec, max(16, int(duration_sec / 0.25)), dtype=np.float32)
    weighted_bpm = np.interp(sample_times, times, local_bpm)
    if len(confidence):
        weighted_conf = np.interp(sample_times, times, np.clip(confidence, 0.0, 1.0))
        drift_mix = np.clip((weighted_conf - 0.35) / 0.55, 0.0, 1.0)
        weighted_bpm = (1.0 - drift_mix) * float(bpm) + drift_mix * weighted_bpm

    grid = [0.0]
    current = 0.0
    while current < duration_sec:
        local_step = (60.0 / max(float(np.interp(current, sample_times, weighted_bpm)), 1e-6)) * (4.0 / float(resolution))
        local_step = float(np.clip(local_step, _base_step(bpm, resolution) * 0.88, _base_step(bpm, resolution) * 1.12))
        current += local_step
        grid.append(min(current, duration_sec))

    return np.asarray(grid, dtype=np.float32)


def build_grid(
    duration_sec: float,
    bpm: float,
    resolution: int,
    drift_windows: list[dict[str, object]] | None = None,
) -> np.ndarray:
    adaptive = _adaptive_grid(duration_sec, bpm, resolution, list(drift_windows or []))
    if adaptive.size:
        return adaptive
    step = _base_step(bpm, resolution)
    grid = np.arange(0.0, duration_sec + step, step, dtype=np.float32)
    return grid


def _interp_monotonic(x: np.ndarray, y: np.ndarray, x_new: np.ndarray) -> np.ndarray:
    y_new = np.interp(x_new, x, y)
    return np.maximum.accumulate(y_new)


def _strictly_increasing_pairs(
    source_times: np.ndarray,
    target_times: np.ndarray,
    min_spacing_sec: float,
) -> tuple[np.ndarray, np.ndarray]:
    src = np.asarray(source_times, dtype=np.float32)
    tgt = np.asarray(target_times, dtype=np.float32)
    if len(src) != len(tgt):
        raise ValueError("source_times and target_times must have the same length")

    keep = [0]
    for idx in range(1, len(src)):
        prev = keep[-1]
        if src[idx] - src[prev] < min_spacing_sec:
            continue
        if tgt[idx] - tgt[prev] < min_spacing_sec:
            continue
        keep.append(idx)
    return src[np.array(keep)], tgt[np.array(keep)]


def _anchor_threshold_percentile(
    resolution: int,
    onset_count: int,
    *,
    long_form_track: bool = False,
    sparse_onset_coverage: bool = False,
) -> float:
    if onset_count < 32:
        return 0.0
    if long_form_track:
        if sparse_onset_coverage:
            return 32.0
        if resolution >= 16:
            return 38.0
        if resolution >= 8:
            return 42.0
        return 48.0
    if resolution >= 16:
        return 50.0
    if resolution >= 8:
        return 55.0
    return 65.0


def _merge_anchor_pair(
    source_anchors: list[float],
    target_anchors: list[float],
    *,
    source_time: float,
    target_time: float,
    min_spacing_sec: float,
) -> None:
    if source_time - source_anchors[-1] < min_spacing_sec:
        return
    if target_time - target_anchors[-1] < min_spacing_sec:
        return
    source_anchors.append(float(source_time))
    target_anchors.append(float(target_time))


def _grid_index_for_time(grid_times: np.ndarray, event_time: float) -> int:
    grid_idx = int(np.searchsorted(grid_times, event_time))
    if 0 < grid_idx < len(grid_times):
        prev_time = float(grid_times[grid_idx - 1])
        next_time = float(grid_times[grid_idx])
        if abs(event_time - prev_time) <= abs(next_time - event_time):
            grid_idx -= 1
    return int(np.clip(grid_idx, 0, max(len(grid_times) - 1, 0)))


def _endpoint_grid_time(
    grid_times: np.ndarray,
    duration_sec: float,
    *,
    min_target_time: float,
) -> float:
    if len(grid_times) == 0:
        return max(float(duration_sec), float(min_target_time))

    step = float(np.median(np.diff(grid_times))) if len(grid_times) > 1 else 0.0
    if not np.isfinite(step) or step <= 0.0:
        step = max(float(duration_sec) - float(grid_times[-1]), 0.0)

    insert_idx = int(np.searchsorted(grid_times, duration_sec))
    candidates: list[float] = []
    for idx in range(max(0, insert_idx - 2), min(len(grid_times), insert_idx + 3)):
        candidates.append(float(grid_times[idx]))

    if step > 0.0:
        current = float(grid_times[-1])
        while current < float(duration_sec) + step * 2.0:
            current += step
            candidates.append(float(current))

    valid = [time_sec for time_sec in candidates if time_sec >= float(min_target_time)]
    if not valid:
        return max(float(duration_sec), float(min_target_time))
    return min(valid, key=lambda time_sec: (abs(time_sec - float(duration_sec)), time_sec))


def _beat_grid_index_for_time(grid_times: np.ndarray, event_time: float, resolution: int) -> int:
    """Snap beat-tracker anchors to the DAW quarter-note phase, not any subdivision."""
    if len(grid_times) == 0:
        return 0
    subdivisions_per_beat = max(1, int(round(float(resolution) / 4.0)))
    plain_idx = _grid_index_for_time(grid_times, event_time)
    base_idx = int(round(plain_idx / subdivisions_per_beat) * subdivisions_per_beat)
    candidate_indexes = {int(np.clip(base_idx, 0, len(grid_times) - 1))}
    for step in (-1, 1):
        candidate_indexes.add(int(np.clip(base_idx + (step * subdivisions_per_beat), 0, len(grid_times) - 1)))
    return min(candidate_indexes, key=lambda idx: abs(float(event_time) - float(grid_times[idx])))


def _phase_guided_grid_index(
    grid_times: np.ndarray,
    event_time: float,
    *,
    step_sec: float,
    recent_offsets: list[float],
) -> int:
    plain_idx = _grid_index_for_time(grid_times, event_time)
    if len(recent_offsets) < 4:
        return plain_idx

    recent = np.asarray(recent_offsets[-8:], dtype=np.float32)
    if not np.all(np.isfinite(recent)):
        return plain_idx
    spread = float(np.std(recent))
    if spread > max(step_sec * 0.18, 0.028):
        return plain_idx

    median_offset = float(np.median(recent))
    whole_step_shift = int(np.round(median_offset / max(step_sec, 1e-6)))
    canonical_offset = median_offset - (whole_step_shift * step_sec)
    plain_offset = float(event_time) - float(grid_times[plain_idx])
    tolerance = max(step_sec * 0.16, 0.02)
    corrected_idx = int(np.clip(plain_idx + whole_step_shift, 0, len(grid_times) - 1))
    corrected_offset = float(event_time) - float(grid_times[corrected_idx])
    if abs(corrected_offset - canonical_offset) <= tolerance:
        return corrected_idx
    if abs(plain_offset - canonical_offset) <= tolerance:
        return plain_idx

    candidate_indexes = {plain_idx, corrected_idx}
    for base_idx in (plain_idx, corrected_idx):
        if base_idx > 0:
            candidate_indexes.add(base_idx - 1)
        if base_idx + 1 < len(grid_times):
            candidate_indexes.add(base_idx + 1)

    return min(
        candidate_indexes,
        key=lambda idx: (
            abs((float(event_time) - float(grid_times[idx])) - canonical_offset),
            abs(float(event_time) - float(grid_times[idx])),
        ),
    )


def _continuity_limited_grid_index(
    grid_idx: int,
    *,
    last_grid_idx: int,
    source_elapsed_sec: float,
    step_sec: float,
) -> int:
    if source_elapsed_sec <= 0.0 or step_sec <= 0.0:
        return max(int(grid_idx), int(last_grid_idx))
    expected_steps = max(1, int(round(float(source_elapsed_sec) / max(float(step_sec), 1e-6))))
    min_idx = int(last_grid_idx) + 1
    max_idx = int(last_grid_idx) + expected_steps
    return int(np.clip(int(grid_idx), min_idx, max_idx))


def onset_quantize_curve_from_features(
    onset_env: np.ndarray,
    hop: int,
    sr: int,
    duration_sec: float,
    bpm: float,
    resolution: int,
    drift_windows: list[dict[str, object]] | None = None,
    source_drift_windows: list[dict[str, object]] | None = None,
) -> dict:
    grid_times = build_grid(duration_sec, bpm, resolution, drift_windows=drift_windows)
    source_grid_times = build_grid(duration_sec, bpm, resolution, drift_windows=source_drift_windows)
    if len(source_grid_times) < 2:
        source_grid_times = grid_times
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop,
        units="frames",
        backtrack=False,
    )
    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=hop).astype(np.float32)
    if len(onset_times) == 0:
        times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr, hop_length=hop)
        return {
            "source_times": times.astype(np.float32),
            "target_times": times.astype(np.float32),
            "grid_times": grid_times.astype(np.float32),
            "anchor_count": 0,
            "method": "identity",
        }

    step = float(np.median(np.diff(source_grid_times))) if len(source_grid_times) > 1 else _base_step(bpm, resolution)
    min_spacing_sec = max(0.06, min(step * 0.45, 0.25))

    source_anchors = [0.0]
    target_anchors = [0.0]
    last_grid_idx = 0
    recent_offsets: list[float] = []

    sparse_onset_coverage = len(onset_times) < max(12, int(len(grid_times) * 0.45))
    long_form_track = duration_sec >= 45.0
    onset_strengths = onset_env[np.clip(onset_frames, 0, len(onset_env) - 1)]
    percentile = _anchor_threshold_percentile(
        resolution,
        len(onset_times),
        long_form_track=long_form_track,
        sparse_onset_coverage=sparse_onset_coverage,
    )
    strength_threshold = np.percentile(onset_strengths, percentile) if percentile > 0 else float(onset_strengths.min())
    if sparse_onset_coverage or long_form_track:
        try:
            beat_tempo, beat_frames = librosa.beat.beat_track(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=hop,
                start_bpm=float(bpm),
                tightness=240.0,
                trim=False,
                units="frames",
            )
            if isinstance(beat_tempo, np.ndarray):
                beat_tempo = float(np.ravel(beat_tempo)[0])
            beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop).astype(np.float32)
        except Exception:
            beat_times = np.array([], dtype=np.float32)

        beat_min_spacing_sec = max(min_spacing_sec, min(step * 1.6, 0.45)) if long_form_track else min_spacing_sec
        for beat_time in beat_times:
            grid_idx = _phase_guided_grid_index(
                source_grid_times,
                float(beat_time),
                step_sec=step,
                recent_offsets=recent_offsets,
            )
            if float(beat_time) > source_anchors[-1]:
                grid_idx = _continuity_limited_grid_index(
                    grid_idx,
                    last_grid_idx=last_grid_idx,
                    source_elapsed_sec=float(beat_time) - float(source_anchors[-1]),
                    step_sec=step,
                )
            else:
                grid_idx = max(grid_idx, last_grid_idx)
            if grid_idx >= len(grid_times) or grid_idx >= len(source_grid_times):
                break
            target_time = float(grid_times[grid_idx])
            prev_len = len(source_anchors)
            _merge_anchor_pair(
                source_anchors,
                target_anchors,
                source_time=float(beat_time),
                target_time=target_time,
                min_spacing_sec=beat_min_spacing_sec,
            )
            if len(source_anchors) > prev_len:
                last_grid_idx = grid_idx
                recent_offsets.append(float(beat_time) - float(source_grid_times[grid_idx]))

    for onset_time, strength in zip(onset_times, onset_strengths):
        if strength < strength_threshold:
            continue

        grid_idx = _phase_guided_grid_index(
            source_grid_times,
            float(onset_time),
            step_sec=step,
            recent_offsets=recent_offsets,
        )
        if onset_time > source_anchors[-1]:
            grid_idx = _continuity_limited_grid_index(
                grid_idx,
                last_grid_idx=last_grid_idx,
                source_elapsed_sec=float(onset_time) - float(source_anchors[-1]),
                step_sec=step,
            )
        else:
            grid_idx = max(grid_idx, last_grid_idx)
        if grid_idx >= len(grid_times) or grid_idx >= len(source_grid_times):
            break

        target_time = float(grid_times[grid_idx])
        prev_len = len(source_anchors)
        _merge_anchor_pair(
            source_anchors,
            target_anchors,
            source_time=float(onset_time),
            target_time=target_time,
            min_spacing_sec=min_spacing_sec,
        )
        if len(source_anchors) > prev_len:
            last_grid_idx = grid_idx
            recent_offsets.append(float(onset_time) - float(source_grid_times[grid_idx]))

    endpoint_spacing = min_spacing_sec * 0.5
    if len(source_anchors) > 1 and float(duration_sec) - float(source_anchors[-1]) < endpoint_spacing:
        min_target_time = float(target_anchors[-2]) + endpoint_spacing
        source_anchors[-1] = float(duration_sec)
        target_anchors[-1] = _endpoint_grid_time(
            grid_times,
            duration_sec,
            min_target_time=min_target_time,
        )
    else:
        source_anchors.append(duration_sec)
        target_anchors.append(
            _endpoint_grid_time(
                grid_times,
                duration_sec,
                min_target_time=float(target_anchors[-1]) + endpoint_spacing,
            )
        )

    src, tgt = _strictly_increasing_pairs(
        np.asarray(source_anchors, dtype=np.float32),
        np.asarray(target_anchors, dtype=np.float32),
        min_spacing_sec=min_spacing_sec * 0.5,
    )

    return {
        "source_times": src,
        "target_times": tgt,
        "grid_times": grid_times.astype(np.float32),
        "anchor_count": int(len(src)),
        "method": "onset_grid",
    }


def onset_quantize_curve(y_mono: np.ndarray, sr: int, bpm: float, resolution: int) -> dict:
    duration_sec = float(len(y_mono) / sr)
    onset_env, hop = onset_envelope(y_mono, sr)
    return onset_quantize_curve_from_features(onset_env, hop, sr, duration_sec, bpm, resolution)


def dtw_warp_curve(novelty: np.ndarray, times: np.ndarray, grid_times: np.ndarray) -> dict:
    frame_dt = float(np.median(np.diff(times)))
    target = np.zeros_like(novelty)
    idx = np.clip(np.searchsorted(times, grid_times), 0, len(target) - 1)
    target[idx] = 1.0
    kernel = np.hanning(9)
    kernel = kernel / kernel.sum()
    target = np.convolve(target, kernel, mode="same")

    nov_norm = (novelty - novelty.mean()) / (novelty.std() + 1e-8)
    tgt_norm = (target - target.mean()) / (target.std() + 1e-8)

    x = nov_norm[np.newaxis, :]
    y = tgt_norm[np.newaxis, :]
    _, wp = librosa.sequence.dtw(X=x, Y=y, metric="euclidean", subseq=False, backtrack=True)

    wp = np.array(wp)[::-1]
    src_idx = wp[:, 0]
    tgt_idx = wp[:, 1]

    unique_src, uniq_pos = np.unique(src_idx, return_index=True)
    mapped_tgt = tgt_idx[uniq_pos]

    src_times = times[unique_src]
    tgt_times = times[np.clip(mapped_tgt, 0, len(times) - 1)]

    mapped_full = _interp_monotonic(src_times, tgt_times, times)

    # Smooth mapping to avoid audible jitter.
    win = 17
    if len(mapped_full) >= win:
        pad = win // 2
        padded = np.pad(mapped_full, (pad, pad), mode="edge")
        kernel = np.ones(win) / win
        mapped_full = np.convolve(padded, kernel, mode="same")[pad:-pad]
        mapped_full = np.maximum.accumulate(mapped_full)

    mapped_full = np.clip(mapped_full, 0.0, times[-1])
    mapped_full[-1] = times[-1]
    return {
        "source_times": times,
        "target_times": mapped_full,
        "frame_dt": frame_dt,
    }
