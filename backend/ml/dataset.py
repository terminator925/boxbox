from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path

import librosa
import numpy as np
import torch
from torch.utils.data import Dataset

from backend.audio.dtw_targets import onset_quantize_curve_from_features
from backend.audio.features import extract_features
from backend.audio.tempo import estimate_tempo

DATASET_CACHE_VERSION = "v2"


def normalize_examples_roots(examples_root: Path | str | list[Path] | tuple[Path, ...]) -> list[Path]:
    if isinstance(examples_root, (list, tuple)):
        roots = [Path(item) for item in examples_root]
    else:
        raw = str(examples_root)
        parts = [part for part in raw.split(os.pathsep) if part]
        roots = [Path(part) for part in (parts or [raw])]
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
    return out


def normalize_dataset_filters(dataset_filters: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if dataset_filters is None:
        return []
    if isinstance(dataset_filters, (list, tuple)):
        values = [str(item).strip() for item in dataset_filters]
    else:
        values = [part.strip() for part in str(dataset_filters).split(",")]
    return [value for value in values if value]


def _meta_quality_ok(
    meta: dict,
    *,
    min_improvement_pct: float | None = None,
    max_after_sec: float | None = None,
    min_event_count: int | None = None,
) -> bool:
    timing = meta.get("timing_metrics") or {}
    if min_improvement_pct is not None:
        try:
            improvement_pct = float(timing.get("improvement_pct", 0.0))
        except Exception:
            return False
        if improvement_pct < float(min_improvement_pct):
            return False
    if max_after_sec is not None:
        try:
            after_sec = float(timing.get("avg_abs_error_after_sec", 1e9))
        except Exception:
            return False
        if after_sec > float(max_after_sec):
            return False
    if min_event_count is not None:
        try:
            event_count = int(meta.get("event_count", 0))
        except Exception:
            return False
        if event_count < int(min_event_count):
            return False
    return True


def parse_dataset_weight_overrides(
    weight_overrides: str | dict[str, float] | None,
) -> dict[str, float]:
    if weight_overrides is None:
        return {}
    if isinstance(weight_overrides, dict):
        out: dict[str, float] = {}
        for key, value in weight_overrides.items():
            try:
                parsed = float(value)
            except Exception:
                continue
            if parsed > 0:
                out[str(key).strip()] = parsed
        return out
    out: dict[str, float] = {}
    for part in str(weight_overrides).split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        key = key.strip()
        try:
            parsed = float(raw_value.strip())
        except Exception:
            continue
        if key and parsed > 0:
            out[key] = parsed
    return out



def dataset_family_from_dir(example_dir: Path) -> str:
    name = example_dir.name
    parts = name.split("_")
    if len(parts) >= 2 and parts[-1].isdigit():
        return "_".join(parts[:-1])
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return name


def limit_items_by_family(
    items: list[tuple[Path, Path]],
    families: list[str],
    limit: int,
) -> tuple[list[tuple[Path, Path]], list[str]]:
    if limit <= 0 or len(items) <= limit:
        return items, families

    grouped: dict[str, list[tuple[Path, Path]]] = {}
    for item, family in zip(items, families):
        grouped.setdefault(family, []).append(item)

    selected_items: list[tuple[Path, Path]] = []
    selected_families: list[str] = []
    family_names = sorted(grouped)
    cursor = 0
    while len(selected_items) < limit and family_names:
        family = family_names[cursor % len(family_names)]
        bucket = grouped[family]
        if bucket:
            selected_items.append(bucket.pop(0))
            selected_families.append(family)
        if not bucket:
            family_names.remove(family)
            if family_names:
                cursor %= len(family_names)
            continue
        cursor += 1

    return selected_items, selected_families


def _load_feature_bundle(audio_path: Path, sr: int = 22050) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int, float]:
    y, sr = librosa.load(str(audio_path), sr=sr, mono=True)
    y = y.astype(np.float32)
    feat = extract_features(y.astype(np.float32), sr)
    mel_db = feat["mel"].T
    onset = feat["onset"][:, None]
    onset = (onset - onset.mean()) / (onset.std() + 1e-8)
    features = np.concatenate([mel_db, onset], axis=1).astype(np.float32)
    duration = len(y) / sr
    return y, features, feat["onset"].astype(np.float32), int(feat["hop_length"]), sr, duration


def _read_example_meta(example_dir: Path) -> dict:
    meta_path = example_dir / "meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resample_curve(curve: np.ndarray, target_len: int) -> np.ndarray:
    if len(curve) == target_len:
        return curve.astype(np.float32)
    src = np.arange(len(curve), dtype=np.float32)
    dst = np.linspace(0, max(len(curve) - 1, 1), target_len, dtype=np.float32)
    return np.interp(dst, src, curve).astype(np.float32)


def _resample_feature_rows(feature: np.ndarray, target_len: int) -> np.ndarray:
    if len(feature) == target_len:
        return feature.astype(np.float32)
    src = np.arange(len(feature), dtype=np.float32)
    dst = np.linspace(0, max(len(feature) - 1, 1), target_len, dtype=np.float32)
    out = np.empty((target_len, feature.shape[1]), dtype=np.float32)
    for idx in range(feature.shape[1]):
        out[:, idx] = np.interp(dst, src, feature[:, idx]).astype(np.float32)
    return out


def _build_baseline_target(
    y: np.ndarray,
    onset_env: np.ndarray,
    hop: int,
    sr: int,
    target_len: int,
    bpm: float | None = None,
    resolution: int | None = None,
) -> np.ndarray:
    duration = max(len(y) / sr, 1e-6)
    bpm = float(bpm or estimate_tempo(y, sr))
    resolution = int(resolution or 8)
    curve = onset_quantize_curve_from_features(onset_env, hop, sr, duration, bpm, resolution)
    frame_times = np.linspace(0.0, duration, target_len, dtype=np.float32)
    baseline = np.interp(frame_times, curve["source_times"], curve["target_times"]).astype(np.float32)
    baseline = np.maximum.accumulate(np.clip(baseline / duration, 0.0, 1.0))
    baseline[-1] = 1.0
    return baseline


def _file_signature(path: Path) -> str:
    stat = path.stat()
    payload = f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_file_for_example(original_path: Path, warped_path: Path, max_frames: int, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = "|".join(
        [
            DATASET_CACHE_VERSION,
            _file_signature(original_path),
            _file_signature(warped_path),
            str(max_frames),
        ]
    )
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return cache_dir / f"{key}.npz"


@functools.lru_cache(maxsize=4096)
def _build_warp_target_cached(original_key: str, warped_key: str, max_frames: int) -> tuple[np.ndarray, np.ndarray]:
    original_path = Path(original_key)
    warped_path = Path(warped_key)
    x_audio, x_feat, x_onset_env, x_hop, x_sr, _ = _load_feature_bundle(original_path)
    _, y_feat, _, _, _, _ = _load_feature_bundle(warped_path)
    meta = _read_example_meta(original_path.parent)
    bpm = meta.get("bpm")
    resolution = meta.get("resolution", meta.get("subdivision", 8))

    dtw_frames = 128
    x_dtw = _resample_feature_rows(x_feat, min(len(x_feat), dtw_frames))
    y_dtw = _resample_feature_rows(y_feat, min(len(y_feat), dtw_frames))

    x = x_dtw.T
    y = y_dtw.T
    _, wp = librosa.sequence.dtw(X=x, Y=y, metric="cosine")
    wp = np.array(wp)[::-1]

    src_idx = wp[:, 0]
    tgt_idx = wp[:, 1]
    uniq_src, uniq_pos = np.unique(src_idx, return_index=True)
    tgt = tgt_idx[uniq_pos]

    coarse_target_idx = np.interp(np.arange(x_dtw.shape[0]), uniq_src, tgt)
    target_idx = _resample_curve(coarse_target_idx, x_feat.shape[0])
    target_norm = target_idx / max(y_dtw.shape[0] - 1, 1)
    target_norm = np.maximum.accumulate(np.clip(target_norm, 0.0, 1.0)).astype(np.float32)
    baseline_norm = _build_baseline_target(
        x_audio,
        x_onset_env,
        x_hop,
        x_sr,
        x_feat.shape[0],
        bpm=bpm,
        resolution=resolution,
    )

    if x_feat.shape[0] > max_frames:
        idx = np.linspace(0, x_feat.shape[0] - 1, max_frames).astype(int)
        x_feat = x_feat[idx]
        target_norm = target_norm[idx]
        baseline_norm = baseline_norm[idx]

    baseline_norm = _resample_curve(baseline_norm, len(target_norm))
    features = np.concatenate([x_feat, baseline_norm[:, None]], axis=1).astype(np.float32)

    return features, target_norm


def build_warp_target(original_path: Path, warped_path: Path, max_frames: int = 2000) -> tuple[np.ndarray, np.ndarray]:
    return _build_warp_target_cached(str(original_path.resolve()), str(warped_path.resolve()), max_frames)


def build_warp_target_cached_to_disk(
    original_path: Path,
    warped_path: Path,
    max_frames: int = 2000,
    cache_dir: Path | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    if cache_dir is None:
        return build_warp_target(original_path, warped_path, max_frames=max_frames)
    cache_file = _cache_file_for_example(original_path, warped_path, max_frames, cache_dir)
    if cache_file.exists():
        payload = np.load(cache_file)
        return payload["features"].astype(np.float32), payload["target"].astype(np.float32)

    features, target = build_warp_target(original_path, warped_path, max_frames=max_frames)
    temp_file = cache_file.with_suffix(".tmp.npz")
    np.savez_compressed(temp_file, features=features.astype(np.float32), target=target.astype(np.float32))
    temp_file.replace(cache_file)
    return features, target


class WarpDataset(Dataset):
    def __init__(
        self,
        examples_root: Path | str | list[Path] | tuple[Path, ...],
        split: str = "all",
        val_ratio: float = 0.15,
        max_frames: int = 2000,
        cache_dir: Path | None = None,
        dataset_filters: str | list[str] | tuple[str, ...] | None = None,
        min_improvement_pct: float | None = None,
        max_after_sec: float | None = None,
        min_event_count: int | None = None,
        max_examples: int | None = None,
    ):
        if split not in {"all", "train", "val"}:
            raise ValueError("split must be one of: all, train, val")
        self.items: list[tuple[Path, Path]] = []
        self.dataset_families: list[str] = []
        self.max_frames = int(max_frames)
        self.cache_dir = cache_dir
        self.roots = normalize_examples_roots(examples_root)
        self.dataset_filters = normalize_dataset_filters(dataset_filters)
        self.min_improvement_pct = min_improvement_pct
        self.max_after_sec = max_after_sec
        self.min_event_count = min_event_count
        for root in self.roots:
            if not root.exists():
                continue
            for sub in sorted(root.glob("*")):
                if not sub.is_dir():
                    continue
                if self.dataset_filters and not any(sub.name.startswith(prefix) for prefix in self.dataset_filters):
                    continue
                o = sub / "original.wav"
                w = sub / "warped.wav"
                if not (o.exists() and w.exists()):
                    continue
                meta = _read_example_meta(sub)
                if not _meta_quality_ok(
                    meta,
                    min_improvement_pct=self.min_improvement_pct,
                    max_after_sec=self.max_after_sec,
                    min_event_count=self.min_event_count,
                ):
                    continue

                if split != "all":
                    digest_key = f"{root.name}:{sub.name}"
                    digest = hashlib.md5(digest_key.encode("utf-8")).digest()[0] / 255.0
                    is_val = digest < val_ratio
                    if split == "train" and is_val:
                        continue
                    if split == "val" and not is_val:
                        continue

                self.items.append((o, w))
                self.dataset_families.append(dataset_family_from_dir(sub))

        if max_examples is not None and int(max_examples) > 0 and len(self.items) > int(max_examples):
            self.items, self.dataset_families = limit_items_by_family(
                self.items,
                self.dataset_families,
                int(max_examples),
            )

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        original, warped = self.items[idx]
        feat, target = build_warp_target_cached_to_disk(
            original,
            warped,
            max_frames=self.max_frames,
            cache_dir=self.cache_dir,
        )
        return {
            "features": torch.tensor(feat, dtype=torch.float32),
            "target": torch.tensor(target, dtype=torch.float32),
        }


def warm_dataset_cache(dataset: WarpDataset) -> None:
    for idx in range(len(dataset)):
        dataset[idx]


def collate_batch(batch: list[dict[str, torch.Tensor]]) -> dict[str, torch.Tensor]:
    max_t = max(item["features"].shape[0] for item in batch)
    feat_dim = batch[0]["features"].shape[1]

    feats = torch.zeros((len(batch), max_t, feat_dim), dtype=torch.float32)
    targets = torch.zeros((len(batch), max_t), dtype=torch.float32)
    mask = torch.zeros((len(batch), max_t), dtype=torch.float32)

    for i, item in enumerate(batch):
        t = item["features"].shape[0]
        feats[i, :t] = item["features"]
        targets[i, :t] = item["target"]
        mask[i, :t] = 1.0

    return {"features": feats, "target": targets, "mask": mask}


def dataset_sample_weights(
    dataset: WarpDataset,
    mode: str = "inverse",
    weight_overrides: str | dict[str, float] | None = None,
) -> np.ndarray:
    if mode not in {"inverse", "uniform"}:
        raise ValueError("mode must be one of: inverse, uniform")
    if not dataset.dataset_families:
        return np.array([], dtype=np.float32)
    overrides = parse_dataset_weight_overrides(weight_overrides)
    if mode == "uniform":
        weights = np.ones(len(dataset.dataset_families), dtype=np.float32)
        if overrides:
            weights = np.asarray(
                [float(overrides.get(family, 1.0)) for family in dataset.dataset_families],
                dtype=np.float32,
            )
        return weights
    counts: dict[str, int] = {}
    for family in dataset.dataset_families:
        counts[family] = counts.get(family, 0) + 1
    weights = [
        (1.0 / max(counts[family], 1)) * float(overrides.get(family, 1.0))
        for family in dataset.dataset_families
    ]
    return np.asarray(weights, dtype=np.float32)



