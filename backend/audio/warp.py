from __future__ import annotations

import os
import shutil
import numpy as np
from pathlib import Path
from functools import lru_cache


def _candidate_rubberband_paths() -> list[Path]:
    candidates: list[Path] = []
    env_path = (os.getenv("BOXBOX_RUBBERBAND_BIN") or "").strip()
    if env_path:
        candidates.append(Path(env_path))

    repo_root = Path(__file__).resolve().parents[2]
    candidates.extend(
        [
            repo_root / "tools" / "rubberband.exe",
            repo_root / "tools" / "rubberband" / "rubberband.exe",
            repo_root / "tools" / "rubberband-cli" / "rubberband.exe",
        ]
    )
    rubberband_root = repo_root / "tools" / "rubberband"
    if rubberband_root.exists():
        candidates.extend(rubberband_root.rglob("rubberband.exe"))
    return candidates


def _ensure_rubberband_on_path() -> Path | None:
    resolved = shutil.which("rubberband")
    if resolved:
        return Path(resolved)

    for candidate in _candidate_rubberband_paths():
        if not candidate.exists():
            continue
        parent = str(candidate.parent)
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if parent not in path_parts:
            os.environ["PATH"] = parent + os.pathsep + os.environ.get("PATH", "")
        return candidate
    return None


def _prepare_anchors(src_times: np.ndarray, tgt_times: np.ndarray, max_points: int = 2200) -> tuple[np.ndarray, np.ndarray]:
    n = min(len(src_times), len(tgt_times))
    if n < 2:
        return np.array([0.0, 0.01], dtype=np.float32), np.array([0.0, 0.01], dtype=np.float32)

    src = np.asarray(src_times[:n], dtype=np.float32)
    tgt = np.asarray(tgt_times[:n], dtype=np.float32)

    # Reduce anchor count for speed and stability, then force monotonicity.
    step = max(1, n // max_points)
    idx = np.arange(0, n, step, dtype=np.int64)
    if idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    src = src[idx]
    tgt = tgt[idx]
    src = np.maximum.accumulate(src)
    tgt = np.maximum.accumulate(tgt)

    # Keep strictly increasing anchors to avoid zero/negative-duration segments.
    keep = [0]
    for i in range(1, len(src)):
        if src[i] > src[keep[-1]] and tgt[i] > tgt[keep[-1]]:
            keep.append(i)
    if len(keep) < 2:
        return np.array([0.0, 0.01], dtype=np.float32), np.array([0.0, 0.01], dtype=np.float32)
    return src[np.array(keep)], tgt[np.array(keep)]


@lru_cache(maxsize=1)
def _rubberband_available() -> bool:
    try:
        import pyrubberband  # noqa: F401
        return _ensure_rubberband_on_path() is not None
    except Exception:
        return False


def _rubberband_warp(audio: np.ndarray, sr: int, src_times: np.ndarray, tgt_times: np.ndarray) -> np.ndarray:
    import pyrubberband as pyrb

    # Rubberband supports time maps in recent versions; if unavailable, fallback safely.
    if not hasattr(pyrb, "timemap_stretch"):
        raise RuntimeError("pyrubberband timemap API not found")
    src, tgt = _prepare_anchors(src_times, tgt_times)
    src_samples = np.maximum.accumulate(np.round(src * sr).astype(np.int64))
    tgt_samples = np.maximum.accumulate(np.round(tgt * sr).astype(np.int64))
    src_samples[0] = 0
    tgt_samples[0] = 0
    src_samples[-1] = int(audio.shape[0])
    time_map = list(zip(src_samples.tolist(), tgt_samples.tolist()))
    warped = pyrb.timemap_stretch(audio.astype(np.float32, copy=False), sr, time_map)
    warped = np.asarray(warped, dtype=np.float32)
    if warped.ndim == 1:
        warped = warped[:, None]
    return warped


def _interp_time_map_warp(
    audio: np.ndarray,
    sr: int,
    src_times: np.ndarray,
    tgt_times: np.ndarray,
) -> np.ndarray:
    src, tgt = _prepare_anchors(src_times, tgt_times)
    if len(src) < 2 or len(tgt) < 2:
        return audio.astype(np.float32, copy=True)

    out_len = max(int(np.round(float(tgt[-1]) * sr)), 1)
    out_times = np.arange(out_len, dtype=np.float32) / float(sr)
    src_from_tgt = np.interp(out_times, tgt, src).astype(np.float32)
    input_times = np.arange(audio.shape[0], dtype=np.float32) / float(sr)

    warped_channels = []
    for ch in range(audio.shape[1]):
        warped = np.interp(src_from_tgt, input_times, audio[:, ch].astype(np.float32))
        warped_channels.append(warped.astype(np.float32))
    return np.stack(warped_channels, axis=1).astype(np.float32)


def apply_warp(audio: np.ndarray, sr: int, src_times: np.ndarray, tgt_times: np.ndarray) -> tuple[np.ndarray, str]:
    if _rubberband_available():
        try:
            return _rubberband_warp(audio, sr, src_times, tgt_times), "rubberband"
        except Exception:
            pass

    out = _interp_time_map_warp(audio, sr, src_times, tgt_times)

    # Tiny fade in/out to avoid clicks.
    fade_n = min(int(0.01 * sr), len(out) // 2)
    if fade_n > 2:
        fade = np.linspace(0.0, 1.0, fade_n)
        out[:fade_n] *= fade[:, None]
        out[-fade_n:] *= fade[::-1, None]
    return out.astype(np.float32), "interp_time_map"
