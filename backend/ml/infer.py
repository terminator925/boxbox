from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

import librosa
import numpy as np
import torch

from backend.audio.dtw_targets import onset_quantize_curve
from backend.ml.device import autocast_context, resolve_device
from backend.ml.model import BoxBoxWarpNet
from backend.ml.model_registry import ranked_model_paths
from backend.ml.openvino_backend import infer_with_openvino


def preferred_inference_accelerator() -> str:
    value = (os.getenv("BOXBOX_INFER_ACCELERATOR") or "torch").strip().lower()
    allowed = {"auto", "npu", "gpu", "cpu", "torch", "cuda"}
    return value if value in allowed else "torch"


def preferred_inference_candidate_strategy() -> str:
    value = (os.getenv("BOXBOX_INFER_CANDIDATE_STRATEGY") or "core4_adaptive_plus").strip().lower()
    allowed = {"all", "core4", "core4_adaptive", "core4_adaptive_plus", "core4_adaptive_plus_qf", "core5"}
    return value if value in allowed else "all"


def model_path(models_dir: Path) -> Path:
    return models_dir / "boxbox_latest.pt"


def candidate_model_paths(models_dir: Path) -> list[Path]:
    candidates = [
        *ranked_model_paths(models_dir, limit=3),
        model_path(models_dir),
        models_dir / "boxbox_mixed_legacy_candidate_r1884.pt",
        models_dir / "boxbox_before_groove_860.pt",
        models_dir / "boxbox_legacy_specialist_r1730.pt",
        models_dir / "boxbox_legacy_specialist_r1884_qm.pt",
        models_dir / "boxbox_legacy_specialist_r1884_qf.pt",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen or not path.exists():
            continue
        seen.add(key)
        out.append(path)
    return out


def _filter_model_paths_for_strategy(model_files: list[Path], strategy: str) -> list[Path]:
    strategy = (strategy or "all").strip().lower()
    if strategy in {"", "all"}:
        return list(model_files)

    allowed_names = {
        "core4": {
            "boxbox_latest.pt",
            "boxbox_mixed_legacy_candidate_r1884.pt",
            "boxbox_before_groove_860.pt",
        },
        "core5": {
            "boxbox_latest.pt",
            "boxbox_mixed_legacy_candidate_r1884.pt",
            "boxbox_before_groove_860.pt",
            "boxbox_legacy_specialist_r1730.pt",
        },
    }.get(strategy)
    if not allowed_names:
        return list(model_files)
    selected = [path for path in model_files if path.name.lower() in allowed_names]
    return selected or list(model_files)


def _adaptive_core4_model_paths(model_files: list[Path], profile: dict[str, float]) -> list[Path]:
    core4_names = {
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    }
    include_legacy_specialist = (
        float(profile.get("legacy", 0.0)) >= 0.33
        or (
            float(profile.get("mixed", 0.0)) >= 0.95
            and float(profile.get("legacy", 0.0)) >= 0.20
        )
    )
    if include_legacy_specialist:
        core4_names.add("boxbox_legacy_specialist_r1730.pt")
    selected = [path for path in model_files if path.name.lower() in core4_names]
    return selected or list(model_files)


def _adaptive_core4_plus_model_paths(model_files: list[Path], profile: dict[str, float]) -> list[Path]:
    core_names = {
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    }
    include_full_legacy_trio = (
        float(profile.get("legacy", 0.0)) >= 0.33
        or (
            float(profile.get("mixed", 0.0)) >= 0.95
            and float(profile.get("legacy", 0.0)) >= 0.20
        )
        or (
            float(profile.get("mixed", 0.0)) >= 0.65
            and float(profile.get("percussive", 0.5)) <= 0.18
        )
    )
    if include_full_legacy_trio:
        core_names.update(
            {
                "boxbox_legacy_specialist_r1730.pt",
                "boxbox_legacy_specialist_r1884_qm.pt",
                "boxbox_legacy_specialist_r1884_qf.pt",
            }
        )
    selected = [path for path in model_files if path.name.lower() in core_names]
    return selected or list(model_files)


def _adaptive_core4_plus_qf_model_paths(model_files: list[Path], profile: dict[str, float]) -> list[Path]:
    core_names = {
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    }
    include_legacy_pair = (
        float(profile.get("legacy", 0.0)) >= 0.33
        or (
            float(profile.get("mixed", 0.0)) >= 0.95
            and float(profile.get("legacy", 0.0)) >= 0.20
        )
        or (
            float(profile.get("mixed", 0.0)) >= 0.65
            and float(profile.get("percussive", 0.5)) <= 0.18
        )
    )
    if include_legacy_pair:
        core_names.update(
            {
                "boxbox_legacy_specialist_r1730.pt",
                "boxbox_legacy_specialist_r1884_qf.pt",
            }
        )
    selected = [path for path in model_files if path.name.lower() in core_names]
    return selected or list(model_files)


def _model_role(model_file: Path) -> str:
    name = model_file.name.lower()
    if "mixed_legacy" in name:
        return "mixed_legacy"
    if "legacy" in name:
        return "legacy"
    if "groove" in name:
        return "percussive"
    return "mixed"


def model_exists(models_dir: Path) -> bool:
    return any(path.exists() for path in candidate_model_paths(models_dir))


def _baseline_feature(
    y_mono: np.ndarray,
    sr: int,
    duration_sec: float,
    bpm: float,
    resolution: int,
    frame_count: int,
) -> np.ndarray:
    curve = onset_quantize_curve(y_mono, sr, bpm, resolution)
    frame_times = np.linspace(0.0, duration_sec, frame_count, dtype=np.float32)
    baseline = np.interp(frame_times, curve["source_times"], curve["target_times"]).astype(np.float32)
    baseline = np.maximum.accumulate(np.clip(baseline / max(duration_sec, 1e-6), 0.0, 1.0))
    baseline[-1] = 1.0
    return baseline


def _prepare_input_features(
    mel: np.ndarray,
    onset: np.ndarray,
    duration_sec: float,
    feature_dim: int,
    *,
    y_mono: np.ndarray | None = None,
    sr: int | None = None,
    bpm: float | None = None,
    resolution: int = 8,
) -> np.ndarray:
    mel_t = mel.T.astype(np.float32)
    target_mel_dim = max(feature_dim - 2, 1)
    if mel_t.shape[1] != target_mel_dim:
        src = np.arange(mel_t.shape[1], dtype=np.float32)
        dst = np.linspace(0, max(mel_t.shape[1] - 1, 1), target_mel_dim, dtype=np.float32)
        resized = np.empty((mel_t.shape[0], target_mel_dim), dtype=np.float32)
        for idx in range(mel_t.shape[0]):
            resized[idx] = np.interp(dst, src, mel_t[idx]).astype(np.float32)
        mel_t = resized
    onset_col = onset[:, None].astype(np.float32)
    onset_col = (onset_col - onset_col.mean()) / (onset_col.std() + 1e-8)
    mel_norm = mel_t.astype(np.float32)
    mel_norm = (mel_norm - mel_norm.mean()) / (mel_norm.std() + 1e-8)
    if y_mono is None or sr is None or bpm is None:
        baseline = np.linspace(0.0, 1.0, mel_t.shape[0], dtype=np.float32)
    else:
        baseline = _baseline_feature(y_mono, sr, duration_sec, bpm, resolution, mel_t.shape[0])
    return np.concatenate([mel_norm, onset_col, baseline[:, None]], axis=1).astype(np.float32)


def _curve_confidence(pred: np.ndarray, baseline_norm: np.ndarray, duration_sec: float) -> float:
    pred = np.maximum.accumulate(np.clip(pred.astype(np.float32, copy=False), 0.0, 1.0))
    baseline_norm = np.clip(baseline_norm.astype(np.float32, copy=False), 0.0, 1.0)
    if len(pred) <= 1:
        return 0.0
    pred_sec = pred * max(duration_sec, 1e-6)
    baseline_sec = baseline_norm * max(duration_sec, 1e-6)
    deriv = np.diff(pred_sec)
    mean_step = float(np.mean(np.abs(deriv)) + 1e-8)
    smoothness = float(np.clip(1.0 - (np.std(deriv) / mean_step), 0.0, 1.0))
    diff = np.abs(pred_sec - baseline_sec)
    p90 = float(np.percentile(diff, 90)) if len(diff) else 0.0
    agreement = float(np.clip(1.0 - (p90 / max(duration_sec * 0.18, 1e-6)), 0.0, 1.0))
    endpoint_error = float(abs(pred[-1] - 1.0))
    endpoint = float(np.clip(1.0 - endpoint_error * 8.0, 0.0, 1.0))
    return float(np.clip(0.45 * smoothness + 0.45 * agreement + 0.10 * endpoint, 0.0, 1.0))


def _prepare_inference_payload(
    feature_dim: int,
    mel: np.ndarray,
    onset: np.ndarray,
    duration_sec: float,
    *,
    device: str,
    y_mono: np.ndarray | None = None,
    sr: int | None = None,
    bpm: float | None = None,
    resolution: int = 8,
) -> dict[str, object]:
    feat = _prepare_input_features(
        mel,
        onset,
        duration_sec,
        feature_dim,
        y_mono=y_mono,
        sr=sr,
        bpm=bpm,
        resolution=resolution,
    )
    payload: dict[str, object] = {
        "feat": feat,
        "mask": np.ones((1, feat.shape[0]), dtype=np.float32),
    }
    if device.startswith("cuda"):
        payload["torch_inp"] = torch.tensor(feat, dtype=torch.float32, device=device).unsqueeze(0)
    return payload


def _infer_curve_from_model_file(
    model_file: Path,
    mel: np.ndarray,
    onset: np.ndarray,
    duration_sec: float,
    device: str = "auto",
    prefer_openvino: bool = True,
    *,
    y_mono: np.ndarray | None = None,
    sr: int | None = None,
    bpm: float | None = None,
    resolution: int = 8,
    prepared_payloads: dict[int, dict[str, object]] | None = None,
) -> dict | None:
    if not model_file.exists():
        return None
    device = resolve_device(device)
    _, feature_dim, model = _load_model_bundle(str(model_file.resolve()), device)
    payload = None if prepared_payloads is None else prepared_payloads.get(feature_dim)
    if payload is None:
        payload = _prepare_inference_payload(
            feature_dim,
            mel,
            onset,
            duration_sec,
            device=device,
            y_mono=y_mono,
            sr=sr,
            bpm=bpm,
            resolution=resolution,
        )
        if prepared_payloads is not None:
            prepared_payloads[feature_dim] = payload
    feat = np.asarray(payload["feat"], dtype=np.float32)
    mask = np.asarray(payload["mask"], dtype=np.float32)
    preferred = preferred_inference_accelerator()
    ov_device = None if preferred == "auto" else preferred.upper()
    ov_allowed = prefer_openvino or preferred != "auto"
    ov_result = None
    if ov_allowed and preferred not in {"torch", "cuda"}:
        ov_result = infer_with_openvino(model_file, feat[None, :, :], mask, ov_device=ov_device)
    if ov_result is not None:
        pred, accelerator = ov_result
        pred = pred.squeeze(0)
    else:
        accelerator = "cuda" if device.startswith("cuda") else "torch"
        with torch.inference_mode():
            inp = payload.get("torch_inp")
            if inp is None:
                inp = torch.tensor(feat, dtype=torch.float32, device=device).unsqueeze(0)
            with autocast_context(device):
                pred = model(inp).squeeze(0).float().cpu().numpy()

    pred = np.maximum.accumulate(np.clip(pred, 0.0, 1.0))
    src_times = np.linspace(0.0, duration_sec, len(pred), dtype=np.float32)
    tgt_times = pred * duration_sec
    tgt_times = np.maximum.accumulate(tgt_times)
    tgt_times[-1] = duration_sec

    conf = _curve_confidence(pred, feat[:, -1], duration_sec)
    return {
        "source_times": src_times,
        "target_times": tgt_times,
        "confidence": conf,
        "model_file": str(model_file.name),
        "model_role": _model_role(model_file),
        "accelerator": accelerator,
    }


def _percussive_ratio(y_mono: np.ndarray) -> float:
    y_harm, y_perc = librosa.effects.hpss(y_mono.astype(np.float32))
    harm_energy = float(np.sqrt(np.mean(np.square(y_harm))) + 1e-8)
    perc_energy = float(np.sqrt(np.mean(np.square(y_perc))) + 1e-8)
    return float(np.clip(perc_energy / (harm_energy + perc_energy), 0.0, 1.0))


def _legacy_score(y_mono: np.ndarray, sr: int) -> float:
    mono = y_mono.astype(np.float32, copy=False)
    flatness = float(np.mean(librosa.feature.spectral_flatness(y=mono)))
    rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=mono, sr=sr)))
    low_percussive = float(np.clip(1.0 - _percussive_ratio(mono), 0.0, 1.0))
    flatness_score = float(np.clip((flatness - 0.02) / 0.16, 0.0, 1.0))
    rolloff_score = float(np.clip(1.0 - (rolloff / 4500.0), 0.0, 1.0))
    return float(np.clip(0.45 * low_percussive + 0.35 * flatness_score + 0.20 * rolloff_score, 0.0, 1.0))


def _routing_profile(y_mono: np.ndarray, sr: int) -> dict[str, float]:
    percussive = _percussive_ratio(y_mono)
    legacy = _legacy_score(y_mono, sr)
    mixed = float(np.clip(1.0 - abs(percussive - 0.45) * 1.35, 0.0, 1.0))
    return {
        "percussive": percussive,
        "legacy": legacy,
        "mixed": mixed,
    }


def _routing_weight(curve: dict, profile: dict[str, float]) -> float:
    confidence = float(np.clip(curve.get("confidence", 0.0), 0.0, 1.0))
    role = str(curve.get("model_role", "mixed"))
    if role == "percussive":
        domain_fit = 0.20 + 0.80 * float(profile.get("percussive", 0.5))
    elif role == "legacy":
        legacy = float(profile.get("legacy", 0.0))
        low_percussive = 1.0 - float(profile.get("percussive", 0.5))
        domain_fit = 0.20 + 0.55 * legacy + 0.25 * low_percussive
    elif role == "mixed_legacy":
        mixed = float(profile.get("mixed", 0.5))
        legacy = float(profile.get("legacy", 0.0))
        # Mixed+legacy checkpoints should help on genuinely mixed material with some
        # vintage character, not pull strongly legacy clips away from the dedicated
        # legacy specialists.
        legacy_dominance = float(np.clip((legacy - mixed + 0.05) / 0.45, 0.0, 1.0))
        legacy_band = float(np.clip(1.0 - abs(legacy - 0.55) * 1.8, 0.25, 1.0))
        transition_legacy = float(np.clip((legacy - 0.35) / 0.18, 0.0, 1.0))
        transition_mixed = float(np.clip((0.82 - mixed) / 0.24, 0.0, 1.0))
        transition_guard = 1.0 - 0.70 * transition_legacy * transition_mixed
        domain_fit = (0.18 + 0.55 * mixed + 0.10 * legacy) * (1.0 - 0.60 * legacy_dominance) * legacy_band * transition_guard
    else:
        mixed = float(profile.get("mixed", 0.5))
        legacy_penalty = 1.0 - 0.35 * float(profile.get("legacy", 0.0))
        domain_fit = (0.25 + 0.75 * mixed) * legacy_penalty
    return max(1e-4, (confidence * domain_fit) ** 2)


def _blend_routed_curve(curves: list[dict], duration_sec: float, profile: dict[str, float]) -> dict:
    target_stack = np.stack([curve["target_times"] for curve in curves], axis=0)
    weights = np.asarray([_routing_weight(curve, profile) for curve in curves], dtype=np.float32)
    weights = weights / max(float(weights.sum()), 1e-6)
    top_idx = int(np.argmax(weights))
    if float(weights[top_idx]) >= 0.7:
        weights = np.zeros_like(weights)
        weights[top_idx] = 1.0
    blended_target = np.sum(target_stack * weights[:, None], axis=0).astype(np.float32)
    blended_target = np.maximum.accumulate(blended_target)
    blended_target[-1] = duration_sec
    agreement = 1.0 - float(np.mean(np.std(target_stack, axis=0)) / max(duration_sec, 1e-6))
    conf = float(
        np.clip(
            np.sum(weights * np.asarray([curve["confidence"] for curve in curves], dtype=np.float32))
            * max(agreement, 0.0),
            0.0,
            1.0,
        )
    )
    return {
        "source_times": curves[0]["source_times"],
        "target_times": blended_target,
        "confidence": conf,
        "model_file": ",".join(Path(curve["model_file"]).name for curve in curves),
        "model_role": "routed",
        "routing_profile": profile,
        "routing_percussive_ratio": float(profile.get("percussive", 0.5)),
        "routing_weights": {
            Path(curve["model_file"]).name: float(weight) for curve, weight in zip(curves, weights)
        },
        "candidate_name": "routed",
    }


def infer_curve_candidates(
    mel: np.ndarray,
    onset: np.ndarray,
    duration_sec: float,
    models_dir: Path,
    device: str = "auto",
    prefer_openvino: bool = True,
    max_models: int | None = None,
    *,
    y_mono: np.ndarray | None = None,
    sr: int | None = None,
    bpm: float | None = None,
    resolution: int = 8,
) -> dict | None:
    model_files = candidate_model_paths(models_dir)
    profile = _routing_profile(y_mono, sr) if y_mono is not None and sr is not None else {"percussive": 0.5, "legacy": 0.0, "mixed": 0.5}
    inference_candidate_strategy = preferred_inference_candidate_strategy()
    if inference_candidate_strategy == "core4_adaptive":
        model_files = _adaptive_core4_model_paths(model_files, profile)
    elif inference_candidate_strategy == "core4_adaptive_plus":
        model_files = _adaptive_core4_plus_model_paths(model_files, profile)
    elif inference_candidate_strategy == "core4_adaptive_plus_qf":
        model_files = _adaptive_core4_plus_qf_model_paths(model_files, profile)
    else:
        model_files = _filter_model_paths_for_strategy(model_files, inference_candidate_strategy)
    if max_models is not None and int(max_models) > 0:
        model_files = model_files[: int(max_models)]
    if not model_files:
        return []

    prepared_payloads: dict[int, dict[str, object]] = {}
    curves = [
        curve
        for curve in (
            _infer_curve_from_model_file(
                model_file,
                mel,
                onset,
                duration_sec,
                device,
                prefer_openvino,
                y_mono=y_mono,
                sr=sr,
                bpm=bpm,
                resolution=resolution,
                prepared_payloads=prepared_payloads,
            )
            for model_file in model_files
        )
        if curve is not None
    ]
    if not curves:
        return []
    if len(curves) == 1:
        curve = dict(curves[0])
        curve["candidate_name"] = Path(curve["model_file"]).stem
        return [curve]

    named_curves: list[dict] = []
    for curve in curves:
        curve_copy = dict(curve)
        curve_copy["candidate_name"] = Path(curve["model_file"]).stem
        named_curves.append(curve_copy)
    named_curves.append(_blend_routed_curve(curves, duration_sec, profile))
    return named_curves


def infer_curve(
    mel: np.ndarray,
    onset: np.ndarray,
    duration_sec: float,
    models_dir: Path,
    device: str = "auto",
    prefer_openvino: bool = True,
    max_models: int | None = None,
    *,
    y_mono: np.ndarray | None = None,
    sr: int | None = None,
    bpm: float | None = None,
    resolution: int = 8,
) -> dict | None:
    curves = infer_curve_candidates(
        mel,
        onset,
        duration_sec,
        models_dir,
        device,
        prefer_openvino,
        max_models=max_models,
        y_mono=y_mono,
        sr=sr,
        bpm=bpm,
        resolution=resolution,
    )
    if not curves:
        return None
    routed = next((curve for curve in curves if curve.get("candidate_name") == "routed"), None)
    return routed or max(curves, key=lambda curve: float(curve.get("confidence", 0.0)))


@lru_cache(maxsize=8)
def _load_model_bundle(model_file_str: str, device: str) -> tuple[dict, int, BoxBoxWarpNet]:
    resolved_device = resolve_device(device)
    model_file = Path(model_file_str)
    ckpt = torch.load(model_file, map_location=resolved_device, weights_only=False)
    feature_dim = int(ckpt.get("feature_dim") or 82)
    model = BoxBoxWarpNet(feature_dim=feature_dim)
    model.load_state_dict(ckpt["model"])
    model.to(resolved_device)
    model.eval()
    return ckpt, feature_dim, model
