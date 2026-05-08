from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import librosa
import numpy as np
from fastapi.testclient import TestClient

from backend.audio.dtw_targets import build_grid, onset_quantize_curve, onset_quantize_curve_from_features
from backend.audio.features import extract_features
from backend.audio.io_utils import load_audio, mixdown_mono
from backend.audio.metadata import metadata_bpm_from_tags
from backend.audio.onsets import detect_onsets, detect_onsets_from_envelope, onset_envelope
from backend.audio.tempo import estimate_tempo
from backend.audio.warp import apply_warp
from backend.app import app as api_app
from backend.app import (
    _blend_ml_with_baseline,
    _candidate_metrics_fast,
    _candidate_metrics_proxy,
    _hybrid_search_curves,
    _hybrid_verify_top_k,
    _optimize_groove_target,
    _rank_hybrid_candidates,
    _select_hybrid_candidate,
    _selection_proxy,
    _segment_timing_metrics,
)
from backend.config import MODELS_DIR
from backend.ml.dataset import WarpDataset, build_warp_target
from backend.ml.infer import (
    candidate_model_paths,
    infer_curve,
    infer_curve_candidates,
    model_exists,
    preferred_inference_accelerator,
    preferred_inference_candidate_strategy,
)
from backend.ml.metrics import timing_metrics

BENCHMARK_CACHE_VERSION = "v5"
DEFAULT_BENCHMARK_SUITE = (
    Path("benchmarks/hale-makame-1930.ogg"),
    Path("benchmarks/koromogo-e-1930.ogg"),
    Path("benchmarks/mickey-1918.ogg"),
    Path("benchmarks/popular-song-1931.ogg"),
    Path("benchmarks/ragged-but-right.ogg"),
    Path("stayin-alive-serban-mix.wav"),
    Path("benchmarks/tico-tico-1943.ogg"),
    Path("benchmarks/ute-1950.ogg"),
)
LEGACY_BENCHMARK_SUITE = (
    Path("benchmarks/koromogo-e-1930.ogg"),
    Path("benchmarks/popular-song-1931.ogg"),
    Path("benchmarks/tico-tico-1943.ogg"),
    Path("benchmarks/ute-1950.ogg"),
)
MIXED_BENCHMARK_SUITE = (
    Path("benchmarks/hale-makame-1930.ogg"),
    Path("benchmarks/mickey-1918.ogg"),
    Path("benchmarks/ragged-but-right.ogg"),
    Path("stayin-alive-serban-mix.wav"),
)


def _clip_audio(audio: np.ndarray, sr: int, clip_start: float = 0.0, clip_duration: float | None = None) -> tuple[np.ndarray, float]:
    start_sec = max(0.0, float(clip_start))
    if clip_duration is None or clip_duration <= 0:
        return audio, start_sec
    start_sample = min(len(audio), max(0, int(round(start_sec * sr))))
    end_sample = min(len(audio), int(round((start_sec + float(clip_duration)) * sr)))
    if end_sample - start_sample < max(int(4.0 * sr), 1):
        return audio[start_sample:], start_sec
    return audio[start_sample:end_sample], start_sec


def _model_signature(models_dir: Path) -> str:
    parts: list[str] = []
    for path in candidate_model_paths(models_dir):
        stat = path.stat()
        parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
    infer_path = Path(__file__).with_name("infer.py")
    infer_stat = infer_path.stat()
    parts.append(f"infer.py:{infer_stat.st_mtime_ns}:{infer_stat.st_size}:{BENCHMARK_CACHE_VERSION}")
    benchmark_stat = Path(__file__).stat()
    app_stat = Path(__file__).resolve().parents[1] / "app.py"
    app_meta = app_stat.stat()
    parts.append(f"benchmark.py:{benchmark_stat.st_mtime_ns}:{benchmark_stat.st_size}")
    parts.append(f"app.py:{app_meta.st_mtime_ns}:{app_meta.st_size}")
    payload = "|".join(parts) if parts else "no-model"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _audio_signature(audio_path: Path) -> str:
    stat = audio_path.stat()
    payload = f"{audio_path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _cache_file(
    cache_dir: Path,
    audio_path: Path,
    models_dir: Path,
    resolution: int,
    target_bpm: float | None,
    *,
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    proxy_only: bool = False,
    fast_proxy_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
) -> Path:
    model_sig = _model_signature(models_dir)
    audio_sig = _audio_signature(audio_path)
    bpm_part = "auto" if target_bpm is None else f"{float(target_bpm):.6f}"
    clip_start_part = f"{float(clip_start):.3f}"
    clip_duration_part = "full" if clip_duration is None else f"{float(clip_duration):.3f}"
    mode_part = "proxy" if proxy_only else "full"
    accel_part = preferred_inference_accelerator()
    infer_candidate_part = preferred_inference_candidate_strategy()
    fast_part = "fast_hybrid" if fast_proxy_hybrid_scoring else "verified_hybrid"
    model_limit_part = "allmodels" if max_inference_models is None or int(max_inference_models) <= 0 else f"m{int(max_inference_models)}"
    hybrid_search_part = os.environ.get("BOXBOX_HYBRID_SEARCH_STRATEGY", "core4").strip().lower() or "core4"
    hybrid_verify_topk_part = str(_hybrid_verify_top_k())
    key = f"{audio_sig}_{model_sig}_r{resolution}_b{bpm_part}_s{clip_start_part}_d{clip_duration_part}_{mode_part}_{accel_part}_{infer_candidate_part}_{fast_part}_{model_limit_part}_{hybrid_search_part}_k{hybrid_verify_topk_part}"
    return cache_dir / f"{key}.json"


def _benchmark_runtime_config() -> dict[str, object]:
    return {
        "inference_accelerator": preferred_inference_accelerator(),
        "inference_candidate_strategy": preferred_inference_candidate_strategy(),
        "hybrid_search_strategy": os.environ.get("BOXBOX_HYBRID_SEARCH_STRATEGY", "core4").strip().lower() or "core4",
        "hybrid_verify_top_k": _hybrid_verify_top_k(),
    }


class _StageTimer:
    def __init__(self) -> None:
        now = time.perf_counter()
        self._start = now
        self._last = now
        self.stages: dict[str, float] = {}

    def mark(self, name: str) -> None:
        now = time.perf_counter()
        self.stages[name] = round(float(now - self._last), 6)
        self._last = now

    def payload(self) -> dict[str, object]:
        return {
            "elapsed_sec": round(float(time.perf_counter() - self._start), 6),
            "stages": dict(self.stages),
        }


def _normalized_baseline_curve(y_mono: np.ndarray, sr: int, bpm: float, resolution: int) -> np.ndarray:
    curve = onset_quantize_curve(y_mono, sr, bpm, resolution)
    duration = max(len(y_mono) / sr, 1e-6)
    baseline = np.interp(
        np.linspace(0.0, duration, len(y_mono) // 512 + 1, dtype=np.float32),
        curve["source_times"],
        curve["target_times"],
    )
    baseline = np.maximum.accumulate(np.clip(baseline / duration, 0.0, 1.0))
    return baseline.astype(np.float32)


def benchmark_validation_set(
    examples_dir: Path | str,
    resolution: int = 8,
    val_ratio: float = 0.15,
    dataset_filters: str | None = None,
) -> dict:
    dataset = WarpDataset(examples_dir, split="val", val_ratio=val_ratio, dataset_filters=dataset_filters)
    if len(dataset) == 0:
        raise RuntimeError("No validation examples available for benchmark")
    if not model_exists(MODELS_DIR):
        raise RuntimeError("No trained model found for ML benchmark")

    baseline_mae: list[float] = []
    ml_mae: list[float] = []
    ml_conf: list[float] = []

    for original_path, warped_path in dataset.items:
        target_feat, target_norm = build_warp_target(original_path, warped_path)
        audio, sr = load_audio(original_path)
        mono = mixdown_mono(audio)
        bpm = estimate_tempo(mono, sr)

        baseline_curve = _normalized_baseline_curve(mono, sr, bpm, resolution)
        baseline_resampled = np.interp(
            np.linspace(0, len(baseline_curve) - 1, len(target_norm), dtype=np.float32),
            np.arange(len(baseline_curve), dtype=np.float32),
            baseline_curve,
        )
        baseline_mae.append(float(np.mean(np.abs(baseline_resampled - target_norm))))

        feat = extract_features(mono, sr)
        meta_path = original_path.parent / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        ml_curve = infer_curve(
            feat["mel"],
            feat["onset"],
            float(len(mono) / sr),
            MODELS_DIR,
            y_mono=mono,
            sr=sr,
            bpm=float(meta.get("bpm") or bpm),
            resolution=int(meta.get("resolution", meta.get("subdivision", resolution))),
        )
        if ml_curve is None:
            continue
        ml_norm = np.clip(ml_curve["target_times"] / max(float(len(mono) / sr), 1e-6), 0.0, 1.0)
        ml_resampled = np.interp(
            np.linspace(0, len(ml_norm) - 1, len(target_norm), dtype=np.float32),
            np.arange(len(ml_norm), dtype=np.float32),
            ml_norm,
        )
        ml_mae.append(float(np.mean(np.abs(ml_resampled - target_norm))))
        ml_conf.append(float(ml_curve["confidence"]))

    return {
        "validation_examples": len(dataset),
        "baseline_curve_mae": float(np.mean(baseline_mae)),
        "ml_curve_mae": float(np.mean(ml_mae)),
        "ml_confidence_avg": float(np.mean(ml_conf)) if ml_conf else 0.0,
        "ml_beats_baseline": bool(np.mean(ml_mae) < np.mean(baseline_mae)),
    }


def benchmark_real_audio(
    audio_path: Path,
    target_bpm: float | None = None,
    resolution: int = 8,
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    groove_preserve: int = 0,
    max_inference_models: int | None = None,
) -> dict:
    timer = _StageTimer()
    audio, sr = load_audio(audio_path)
    timer.mark("load_audio")
    audio, effective_clip_start = _clip_audio(audio, sr, clip_start=clip_start, clip_duration=clip_duration)
    mono = mixdown_mono(audio)
    duration_sec = float(len(mono) / sr)
    timer.mark("prepare_audio")
    bpm = float(target_bpm or estimate_tempo(mono, sr))
    grid = build_grid(duration_sec, bpm, resolution)
    timer.mark("tempo_and_grid")
    feat = extract_features(mono, sr)
    timer.mark("feature_extract")

    before = detect_onsets(mono, sr, units="time")
    timer.mark("detect_onsets_before")

    baseline_curve = onset_quantize_curve(mono, sr, bpm, resolution)
    baseline_target, baseline_groove_preserve, _ = _optimize_groove_target(
        baseline_curve["source_times"],
        baseline_curve["target_times"],
        groove_preserve,
        baseline_curve["grid_times"],
        before,
    )
    timer.mark("baseline_curve")
    baseline_audio, _ = apply_warp(audio, sr, baseline_curve["source_times"], baseline_target)
    baseline_after = detect_onsets(mixdown_mono(baseline_audio), sr, units="time")
    baseline_metrics = timing_metrics(before, baseline_after, baseline_curve["grid_times"])
    proxy_audio, proxy_sr, before_proxy = _selection_proxy(audio, sr)
    timer.mark("baseline_score")

    ml_curves = infer_curve_candidates(
        feat["mel"],
        feat["onset"],
        duration_sec,
        MODELS_DIR,
        y_mono=mono,
        sr=sr,
        bpm=bpm,
        resolution=resolution,
        max_models=max_inference_models,
    )
    ml_curve = next((curve for curve in ml_curves if curve.get("candidate_name") == "routed"), None)
    if ml_curve is None and ml_curves:
        ml_curve = max(ml_curves, key=lambda curve: float(curve.get("confidence", 0.0)))
    if ml_curve is None:
        raise RuntimeError("No trained model found for ML benchmark")
    timer.mark("ml_infer")
    ml_audio, _ = apply_warp(audio, sr, ml_curve["source_times"], ml_curve["target_times"])
    ml_after = detect_onsets(mixdown_mono(ml_audio), sr, units="time")
    ml_metrics = timing_metrics(before, ml_after, grid)
    timer.mark("ml_score")

    _, alpha, diagnostics = _blend_ml_with_baseline(
        baseline_curve["source_times"],
        baseline_curve["target_times"],
        ml_curve,
        baseline_curve["grid_times"],
    )
    ml_curves = _hybrid_search_curves(ml_curves)
    ranked_hybrid = _rank_hybrid_candidates(
        baseline_curve["source_times"],
        baseline_curve["target_times"],
        ml_curves,
        baseline_curve["grid_times"],
        before,
        alpha,
        top_k=_hybrid_verify_top_k(),
    )
    timer.mark("hybrid_rank")
    hybrid_target = None
    hybrid_metrics = None
    hybrid_proxy_metrics = None
    selected_candidate = {}
    best_alpha = 0.0
    hybrid_groove_preserve = baseline_groove_preserve
    for candidate_target, _, candidate_alpha, candidate_info in ranked_hybrid:
        optimized_target, candidate_groove_preserve, _ = _optimize_groove_target(
            baseline_curve["source_times"],
            candidate_target,
            groove_preserve,
            baseline_curve["grid_times"],
            before,
        )
        actual_metrics = _candidate_metrics_proxy(proxy_audio, proxy_sr, baseline_curve["source_times"], optimized_target, grid, before_proxy)
        if hybrid_proxy_metrics is None or float(actual_metrics["avg_abs_error_after_sec"]) < float(hybrid_proxy_metrics["avg_abs_error_after_sec"]):
            hybrid_target = optimized_target
            hybrid_proxy_metrics = actual_metrics
            selected_candidate = {**candidate_info, "groove_preserve": candidate_groove_preserve}
            best_alpha = candidate_alpha
            hybrid_groove_preserve = candidate_groove_preserve
    if hybrid_proxy_metrics is None or hybrid_target is None:
        raise RuntimeError("No verified hybrid candidate generated")
    timer.mark("hybrid_proxy_score")
    hybrid_audio, _ = apply_warp(audio, sr, baseline_curve["source_times"], hybrid_target)
    hybrid_after = detect_onsets(mixdown_mono(hybrid_audio), sr, units="time")
    hybrid_metrics = timing_metrics(before, hybrid_after, grid)
    selected, selected_metrics, alpha = _select_hybrid_candidate(
        baseline_metrics,
        hybrid_metrics,
        best_alpha,
    )
    timer.mark("hybrid_full_score")
    runtime_config = _benchmark_runtime_config()

    return {
        "audio_path": str(audio_path),
        "benchmark_mode": "full",
        "clip_start_sec": effective_clip_start,
        "clip_duration_sec": duration_sec,
        "target_bpm": bpm,
        "resolution": resolution,
        "groove_preserve": groove_preserve,
        "effective_baseline_groove_preserve": baseline_groove_preserve,
        "effective_hybrid_groove_preserve": hybrid_groove_preserve,
        "baseline": baseline_metrics,
        "ml": ml_metrics,
        "hybrid": hybrid_metrics,
        "hybrid_proxy": hybrid_proxy_metrics,
        "hybrid_selected": selected,
        "hybrid_effective": selected_metrics,
        "hybrid_alpha": alpha,
        "hybrid_candidate": selected_candidate,
        "hybrid_diagnostics": diagnostics,
        "ml_confidence": float(ml_curve["confidence"]),
        "ml_beats_baseline": float(ml_metrics["avg_abs_error_after_sec"]) < float(baseline_metrics["avg_abs_error_after_sec"]),
        "hybrid_beats_baseline": float(selected_metrics["avg_abs_error_after_sec"]) < float(baseline_metrics["avg_abs_error_after_sec"]),
        "runtime_config": runtime_config,
        "hybrid_search_strategy": runtime_config["hybrid_search_strategy"],
        "processing_timing": timer.payload(),
    }


def benchmark_real_audio_proxy(
    audio_path: Path,
    target_bpm: float | None = None,
    resolution: int = 8,
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    groove_preserve: int = 0,
    fast_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
) -> dict:
    timer = _StageTimer()
    audio, sr = load_audio(audio_path)
    timer.mark("load_audio")
    audio, effective_clip_start = _clip_audio(audio, sr, clip_start=clip_start, clip_duration=clip_duration)
    mono = mixdown_mono(audio)
    duration_sec = float(len(mono) / sr)
    timer.mark("prepare_audio")
    bpm = float(target_bpm or estimate_tempo(mono, sr))
    grid = build_grid(duration_sec, bpm, resolution)
    timer.mark("tempo_and_grid")
    feat = extract_features(mono, sr)
    timer.mark("feature_extract")
    before = detect_onsets(mono, sr, units="time")
    timer.mark("detect_onsets_before")

    baseline_curve = onset_quantize_curve(mono, sr, bpm, resolution)
    proxy_audio, proxy_sr, before_proxy = _selection_proxy(audio, sr)
    baseline_target, baseline_groove_preserve, _ = _optimize_groove_target(
        baseline_curve["source_times"],
        baseline_curve["target_times"],
        groove_preserve,
        baseline_curve["grid_times"],
        before,
    )
    timer.mark("baseline_curve")
    baseline_metrics = _candidate_metrics_proxy(
        proxy_audio,
        proxy_sr,
        baseline_curve["source_times"],
        baseline_target,
        baseline_curve["grid_times"],
        before_proxy,
    )
    timer.mark("baseline_score")

    ml_curves = infer_curve_candidates(
        feat["mel"],
        feat["onset"],
        duration_sec,
        MODELS_DIR,
        y_mono=mono,
        sr=sr,
        bpm=bpm,
        resolution=resolution,
        max_models=max_inference_models,
    )
    ml_curve = next((curve for curve in ml_curves if curve.get("candidate_name") == "routed"), None)
    if ml_curve is None and ml_curves:
        ml_curve = max(ml_curves, key=lambda curve: float(curve.get("confidence", 0.0)))
    if ml_curve is None:
        raise RuntimeError("No trained model found for ML benchmark")
    timer.mark("ml_infer")
    ml_metrics = _candidate_metrics_proxy(
        proxy_audio,
        proxy_sr,
        ml_curve["source_times"],
        ml_curve["target_times"],
        grid,
        before_proxy,
    )
    timer.mark("ml_score")

    _, alpha, diagnostics = _blend_ml_with_baseline(
        baseline_curve["source_times"],
        baseline_curve["target_times"],
        ml_curve,
        baseline_curve["grid_times"],
    )
    ml_curves = _hybrid_search_curves(ml_curves)
    ranked_hybrid = _rank_hybrid_candidates(
        baseline_curve["source_times"],
        baseline_curve["target_times"],
        ml_curves,
        baseline_curve["grid_times"],
        before_proxy,
        alpha,
        top_k=_hybrid_verify_top_k(),
    )
    timer.mark("hybrid_rank")
    hybrid_metrics = None
    selected_candidate = {}
    best_alpha = 0.0
    hybrid_groove_preserve = baseline_groove_preserve
    for candidate_target, _, candidate_alpha, candidate_info in ranked_hybrid:
        optimized_target, candidate_groove_preserve, _ = _optimize_groove_target(
            baseline_curve["source_times"],
            candidate_target,
            groove_preserve,
            baseline_curve["grid_times"],
            before,
        )
        if fast_hybrid_scoring:
            actual_metrics = _candidate_metrics_fast(
                baseline_curve["source_times"],
                optimized_target,
                grid,
                before_proxy,
            )
        else:
            actual_metrics = _candidate_metrics_proxy(
                proxy_audio,
                proxy_sr,
                baseline_curve["source_times"],
                optimized_target,
                grid,
                before_proxy,
            )
        if hybrid_metrics is None or float(actual_metrics["avg_abs_error_after_sec"]) < float(hybrid_metrics["avg_abs_error_after_sec"]):
            hybrid_metrics = actual_metrics
            selected_candidate = {**candidate_info, "groove_preserve": candidate_groove_preserve}
            best_alpha = candidate_alpha
            hybrid_groove_preserve = candidate_groove_preserve
    if hybrid_metrics is None:
        raise RuntimeError("No verified hybrid candidate generated")
    timer.mark("hybrid_score")
    selected, selected_metrics, alpha = _select_hybrid_candidate(
        baseline_metrics,
        hybrid_metrics,
        best_alpha,
    )
    timer.mark("select")

    runtime_config = _benchmark_runtime_config()
    return {
        "audio_path": str(audio_path),
        "benchmark_mode": "proxy",
        "clip_start_sec": effective_clip_start,
        "clip_duration_sec": duration_sec,
        "target_bpm": bpm,
        "resolution": resolution,
        "groove_preserve": groove_preserve,
        "effective_baseline_groove_preserve": baseline_groove_preserve,
        "effective_hybrid_groove_preserve": hybrid_groove_preserve,
        "baseline": baseline_metrics,
        "ml": ml_metrics,
        "hybrid": hybrid_metrics,
        "hybrid_effective": selected_metrics,
        "hybrid_selected": selected,
        "hybrid_alpha": alpha,
        "hybrid_candidate": selected_candidate,
        "hybrid_diagnostics": diagnostics,
        "proxy_hybrid_scoring": "projected_onsets" if fast_hybrid_scoring else "proxy_audio",
        "max_inference_models": max_inference_models,
        "ml_confidence": float(ml_curve["confidence"]),
        "ml_beats_baseline": float(ml_metrics["avg_abs_error_after_sec"]) < float(baseline_metrics["avg_abs_error_after_sec"]),
        "hybrid_beats_baseline": float(selected_metrics["avg_abs_error_after_sec"]) < float(baseline_metrics["avg_abs_error_after_sec"]),
        "runtime_config": runtime_config,
        "hybrid_search_strategy": runtime_config["hybrid_search_strategy"],
        "processing_timing": timer.payload(),
    }


def benchmark_real_audio_cached(
    audio_path: Path,
    target_bpm: float | None = None,
    resolution: int = 8,
    cache_dir: Path = Path("outputs/benchmark_cache"),
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    proxy_only: bool = False,
    fast_proxy_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
) -> dict:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_file(
        cache_dir,
        audio_path,
        MODELS_DIR,
        resolution,
        target_bpm,
        clip_start=clip_start,
        clip_duration=clip_duration,
        proxy_only=proxy_only,
        fast_proxy_hybrid_scoring=fast_proxy_hybrid_scoring,
        max_inference_models=max_inference_models,
    )
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    if proxy_only:
        report = benchmark_real_audio_proxy(
            audio_path,
            target_bpm=target_bpm,
            resolution=resolution,
            clip_start=clip_start,
            clip_duration=clip_duration,
            fast_hybrid_scoring=fast_proxy_hybrid_scoring,
            max_inference_models=max_inference_models,
        )
    else:
        report = benchmark_real_audio(
            audio_path,
            target_bpm=target_bpm,
            resolution=resolution,
            clip_start=clip_start,
            clip_duration=clip_duration,
            max_inference_models=max_inference_models,
        )
    cache_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _suite_summary(per_file: list[dict]) -> dict:
    if not per_file:
        return {
            "num_files": 0,
            "baseline_avg_error_after_sec": 0.0,
            "ml_avg_error_after_sec": 0.0,
            "hybrid_avg_error_after_sec": 0.0,
            "ml_wins": 0,
            "hybrid_wins": 0,
        }

    def _metric_branch(item: dict, branch: str) -> dict:
        value = item[branch]
        return item["hybrid_effective"] if branch == "hybrid_effective" else value

    def _avg(metric_name: str, branch: str) -> float:
        values = [float(_metric_branch(item, branch)[metric_name]) for item in per_file]
        return float(np.mean(values))

    return {
        "num_files": len(per_file),
        "baseline_avg_error_after_sec": _avg("avg_abs_error_after_sec", "baseline"),
        "ml_avg_error_after_sec": _avg("avg_abs_error_after_sec", "ml"),
        "hybrid_avg_error_after_sec": _avg("avg_abs_error_after_sec", "hybrid_effective"),
        "ml_wins": int(sum(1 for item in per_file if item["ml_beats_baseline"])),
        "hybrid_wins": int(sum(1 for item in per_file if item["hybrid_beats_baseline"])),
    }


def _routed_comparison_summary(per_file: list[dict]) -> dict:
    winner_counts: dict[str, int] = {}
    routed_file_count = 0
    mixed_weight_total = 0.0
    mixed_legacy_weight_total = 0.0
    legacy_weight_total = 0.0
    per_file_rows: list[dict[str, object]] = []

    for item in per_file:
        candidate = dict(item.get("hybrid_candidate") or {})
        routing_weights = dict(candidate.get("routing_weights") or {})
        winner = str(candidate.get("candidate_name") or "unknown")
        winner_counts[winner] = winner_counts.get(winner, 0) + 1
        if routing_weights:
            routed_file_count += 1
            mixed_weight = float(routing_weights.get("boxbox_latest.pt", 0.0))
            mixed_legacy_weight = float(routing_weights.get("boxbox_mixed_legacy_candidate_r1884.pt", 0.0))
            legacy_weight = sum(
                float(routing_weights.get(name, 0.0))
                for name in (
                    "boxbox_legacy_specialist_r1730.pt",
                    "boxbox_legacy_specialist_r1884_qm.pt",
                    "boxbox_legacy_specialist_r1884_qf.pt",
                )
            )
            mixed_weight_total += mixed_weight
            mixed_legacy_weight_total += mixed_legacy_weight
            legacy_weight_total += legacy_weight
        else:
            mixed_weight = 0.0
            mixed_legacy_weight = 0.0
            legacy_weight = 0.0

        per_file_rows.append(
            {
                "audio_path": item["audio_path"],
                "selected_candidate": winner,
                "hybrid_error_after_sec": float(item["hybrid_effective"]["avg_abs_error_after_sec"]),
                "mixed_weight": mixed_weight,
                "mixed_legacy_weight": mixed_legacy_weight,
                "legacy_weight_total": legacy_weight,
            }
        )

    denom = max(routed_file_count, 1)
    return {
        "selected_candidate_counts": winner_counts,
        "routed_file_count": routed_file_count,
        "avg_mixed_weight": mixed_weight_total / denom,
        "avg_mixed_legacy_weight": mixed_legacy_weight_total / denom,
        "avg_legacy_weight_total": legacy_weight_total / denom,
        "files": per_file_rows,
    }


def benchmark_audio_files(
    files: list[Path],
    resolution: int = 8,
    target_bpm: float | None = None,
    cache_dir: Path = Path("outputs/benchmark_cache"),
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    proxy_only: bool = False,
    fast_proxy_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
) -> dict:
    if not files:
        raise RuntimeError("No audio files provided for benchmark")
    per_file = [
        benchmark_real_audio_cached(
            path,
            target_bpm=target_bpm,
            resolution=resolution,
            cache_dir=cache_dir,
            clip_start=clip_start,
            clip_duration=clip_duration,
            proxy_only=proxy_only,
            fast_proxy_hybrid_scoring=fast_proxy_hybrid_scoring,
            max_inference_models=max_inference_models,
        )
        for path in files
    ]
    return {
        "summary": _suite_summary(per_file),
        "routed_comparison": _routed_comparison_summary(per_file),
        "files": per_file,
    }


def load_audio_manifest_tracks(manifest_path: Path) -> list[dict]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        raise RuntimeError(f"Audio manifest has no tracks list: {manifest_path}")
    return [dict(track) for track in tracks]


def _manifest_suite(manifest_path: Path, per_file: list[dict], failed_files: list[dict] | None = None) -> dict:
    return {
        "manifest": str(manifest_path),
        "summary": _suite_summary(per_file),
        "routed_comparison": _routed_comparison_summary(per_file),
        "failed_files": list(failed_files or []),
        "files": per_file,
    }


def _manifest_result_key(audio_path: Path | str) -> str:
    return str(Path(audio_path).resolve())


def _load_incremental_manifest_results(output_path: Path) -> dict[str, dict]:
    if not output_path.exists():
        return {}
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    suite = payload.get("real_audio_suite") if isinstance(payload, dict) else None
    if not isinstance(suite, dict):
        suite = payload if isinstance(payload, dict) else None
    files = suite.get("files") if isinstance(suite, dict) else None
    if not isinstance(files, list):
        return {}
    results: dict[str, dict] = {}
    for item in files:
        if not isinstance(item, dict) or "audio_path" not in item:
            continue
        results[_manifest_result_key(str(item["audio_path"]))] = dict(item)
    return results


def _load_incremental_suite_results(output_path: Path, suite_key: str) -> dict[str, dict]:
    if not output_path.exists():
        return {}
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    suite = payload.get(suite_key) if isinstance(payload, dict) else None
    if not isinstance(suite, dict):
        suite = payload if isinstance(payload, dict) else None
    files = suite.get("files") if isinstance(suite, dict) else None
    if not isinstance(files, list):
        return {}
    results: dict[str, dict] = {}
    for item in files:
        if not isinstance(item, dict) or "audio_path" not in item:
            continue
        results[_manifest_result_key(str(item["audio_path"]))] = dict(item)
    return results


def _write_incremental_manifest_report(output_path: Path, suite: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"real_audio_suite": suite}, indent=2), encoding="utf-8")


def _write_incremental_suite_report(output_path: Path, suite_key: str, suite: dict) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({suite_key: suite}, indent=2), encoding="utf-8")


def benchmark_audio_manifest(
    manifest_path: Path,
    resolution: int = 8,
    target_bpm: float | None = None,
    cache_dir: Path = Path("outputs/benchmark_cache"),
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    proxy_only: bool = False,
    incremental_output: Path | None = None,
    resume: bool = False,
    fast_proxy_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
    skip_audio_errors: bool = False,
) -> dict:
    tracks = load_audio_manifest_tracks(manifest_path)
    if not tracks:
        raise RuntimeError(f"Audio manifest has no tracks: {manifest_path}")
    existing_results = _load_incremental_manifest_results(incremental_output) if resume and incremental_output is not None else {}
    per_file = []
    failed_files: list[dict] = []
    for track in tracks:
        audio_path = Path(str(track.get("path") or ""))
        if not audio_path.exists():
            if not skip_audio_errors:
                raise RuntimeError(f"Manifest audio file is missing: {audio_path}")
            failed_files.append(
                {
                    "audio_path": str(audio_path),
                    "relative_path": track.get("relative_path"),
                    "error": "missing_file",
                    "message": f"Manifest audio file is missing: {audio_path}",
                }
            )
            if incremental_output is not None:
                _write_incremental_suite_report(
                    incremental_output,
                    "metronome_canary_suite",
                    _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files),
                )
            continue
        result_key = _manifest_result_key(audio_path)
        if result_key in existing_results:
            per_file.append(existing_results[result_key])
            continue
        track_bpm = target_bpm
        if track_bpm is None and track.get("filename_bpm") is not None:
            track_bpm = float(track["filename_bpm"])
        try:
            item = benchmark_real_audio_cached(
                audio_path,
                target_bpm=track_bpm,
                resolution=resolution,
                cache_dir=cache_dir,
                clip_start=clip_start,
                clip_duration=clip_duration,
                proxy_only=proxy_only,
                fast_proxy_hybrid_scoring=fast_proxy_hybrid_scoring,
                max_inference_models=max_inference_models,
            )
        except Exception as exc:
            if not skip_audio_errors:
                raise
            failed_files.append(
                {
                    "audio_path": str(audio_path),
                    "relative_path": track.get("relative_path"),
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
            if incremental_output is not None:
                _write_incremental_manifest_report(incremental_output, _manifest_suite(manifest_path, per_file, failed_files))
            continue
        item["manifest_track"] = {
            "relative_path": track.get("relative_path"),
            "filename_bpm": track.get("filename_bpm"),
            "duration_sec": track.get("duration_sec"),
            "target_bpm_source": "override" if target_bpm is not None else ("filename" if track_bpm is not None else "detected"),
        }
        per_file.append(item)
        if incremental_output is not None:
            _write_incremental_manifest_report(incremental_output, _manifest_suite(manifest_path, per_file, failed_files))
    return _manifest_suite(manifest_path, per_file, failed_files)


def benchmark_default_suite(
    resolution: int = 8,
    target_bpm: float | None = None,
    cache_dir: Path = Path("outputs/benchmark_cache"),
) -> dict:
    missing = [str(path) for path in DEFAULT_BENCHMARK_SUITE if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing benchmark files: {', '.join(missing)}")
    return benchmark_audio_files(list(DEFAULT_BENCHMARK_SUITE), resolution=resolution, target_bpm=target_bpm, cache_dir=cache_dir)


def benchmark_legacy_suite(
    resolution: int = 8,
    target_bpm: float | None = None,
    cache_dir: Path = Path("outputs/benchmark_cache"),
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    proxy_only: bool = False,
    fast_proxy_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
) -> dict:
    missing = [str(path) for path in LEGACY_BENCHMARK_SUITE if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing benchmark files: {', '.join(missing)}")
    return benchmark_audio_files(
        list(LEGACY_BENCHMARK_SUITE),
        resolution=resolution,
        target_bpm=target_bpm,
        cache_dir=cache_dir,
        clip_start=clip_start,
        clip_duration=clip_duration,
        proxy_only=proxy_only,
        fast_proxy_hybrid_scoring=fast_proxy_hybrid_scoring,
        max_inference_models=max_inference_models,
    )


def benchmark_mixed_suite(
    resolution: int = 8,
    target_bpm: float | None = None,
    cache_dir: Path = Path("outputs/benchmark_cache"),
    clip_start: float = 0.0,
    clip_duration: float | None = None,
    proxy_only: bool = False,
    fast_proxy_hybrid_scoring: bool = False,
    max_inference_models: int | None = None,
) -> dict:
    missing = [str(path) for path in MIXED_BENCHMARK_SUITE if not path.exists()]
    if missing:
        raise RuntimeError(f"Missing benchmark files: {', '.join(missing)}")
    return benchmark_audio_files(
        list(MIXED_BENCHMARK_SUITE),
        resolution=resolution,
        target_bpm=target_bpm,
        cache_dir=cache_dir,
        clip_start=clip_start,
        clip_duration=clip_duration,
        proxy_only=proxy_only,
        fast_proxy_hybrid_scoring=fast_proxy_hybrid_scoring,
        max_inference_models=max_inference_models,
    )


def benchmark_audio_dir(audio_dir: Path, resolution: int = 8, target_bpm: float | None = None) -> dict:
    supported = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aif", ".aiff"}
    files = sorted(p for p in audio_dir.iterdir() if p.is_file() and p.suffix.lower() in supported)
    if not files:
        raise RuntimeError(f"No supported audio files found in {audio_dir}")
    return benchmark_audio_files(files, resolution=resolution, target_bpm=target_bpm)


def benchmark_metronome_canary(
    audio_path: Path,
    *,
    target_bpm: float,
    resolution: int = 8,
    groove_preserve: int = 50,
    mode: str = "hybrid",
) -> dict:
    started = time.perf_counter()
    client = TestClient(api_app)
    with audio_path.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": (audio_path.name, handle, "audio/wav")})
    if upload.status_code != 200:
        raise RuntimeError(f"Upload failed for canary {audio_path}: {upload.text}")
    upload_payload = upload.json()
    job_id = str(upload_payload["job_id"])
    quantize = client.post(
        "/api/quantize",
        json={
            "job_id": job_id,
            "target_bpm": float(target_bpm),
            "resolution": int(resolution),
            "groove_preserve": int(groove_preserve),
            "mode": mode,
        },
    )
    if quantize.status_code != 200:
        raise RuntimeError(f"Quantize failed for canary {audio_path}: {quantize.text}")
    result = quantize.json()
    report_path = Path("outputs") / job_id / str(result["output_files"]["report_json"])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    wall_time_sec = round(float(time.perf_counter() - started), 4)
    return {
        "audio_path": str(audio_path),
        "job_id": job_id,
        "target_bpm": float(report.get("target_bpm", target_bpm)),
        "mode_effective": str(report.get("mode_effective", mode)),
        "warp_selection": str(report.get("warp_selection", "")),
        "effective_groove_preserve": int(report.get("effective_groove_preserve", groove_preserve)),
        "output_files": dict(report.get("output_files") or result.get("output_files") or {}),
        "metronome_check": dict(report.get("metronome_check") or {}),
        "runtime_config": dict(report.get("runtime_config") or _benchmark_runtime_config()),
        "daw_lock_diagnostics": dict(report.get("daw_lock_diagnostics") or {}),
        "timing_metrics": dict(report.get("timing_metrics") or {}),
        "metronome_lock": dict(report.get("metronome_lock") or {}),
        "candidate_metrics": dict(report.get("candidate_metrics") or {}),
        "processing_timing": dict(report.get("processing_timing") or {}),
        "wall_time_sec": wall_time_sec,
        "report_path": str(report_path),
    }


def _detect_manifest_track_bpm(audio_path: Path) -> float:
    audio, sr = load_audio(audio_path)
    mono = mixdown_mono(audio)
    bpm = float(estimate_tempo(mono, sr))
    if not np.isfinite(bpm) or bpm <= 0.0:
        raise RuntimeError(f"Could not detect target BPM for metronome canary: {audio_path}")
    return bpm


def _candidate_bpm_aliases(bpm: float) -> list[float]:
    candidates: list[float] = []
    for factor in (1.0, 1.5, 2.0, 2.0 / 3.0, 0.75, 0.5):
        candidate = float(round(float(bpm) * factor))
        if 40.0 <= candidate <= 240.0 and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _fixed_window_lock_ratio_proxy(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    duration_sec: float,
) -> float:
    if duration_sec <= 0 or len(grid) < 2:
        return 0.0
    window_sec = 12.0
    locked = 0
    total = 0
    for start_sec in np.arange(window_sec, max(float(duration_sec) - window_sec, window_sec), window_sec):
        metrics = _segment_timing_metrics(
            source_times,
            target_times,
            grid,
            onsets_before,
            float(start_sec),
            float(start_sec + window_sec),
        )
        if metrics is None:
            continue
        total += 1
        after_error = float(metrics.get("avg_abs_error_after_sec", 999.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 999.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 999.0))
        absolute_lock = after_error <= 0.05 and phase_abs <= 0.065 and phase_span <= 0.065
        if after_error <= 0.0615 and (improvement_pct >= 0.0 or absolute_lock):
            locked += 1
    return float(locked / total) if total else 0.0


def _select_manifest_track_bpm_from_audio(
    audio_path: Path,
    *,
    resolution: int = 8,
    seed_bpms: list[float] | None = None,
) -> tuple[float, dict[str, object]]:
    audio, sr = load_audio(audio_path)
    mono = mixdown_mono(audio)
    detected_bpm = float(estimate_tempo(mono, sr))
    if not np.isfinite(detected_bpm) or detected_bpm <= 0.0:
        raise RuntimeError(f"Could not detect target BPM for metronome canary: {audio_path}")

    duration_sec = float(len(mono) / max(int(sr), 1))
    onset_env, hop = onset_envelope(mono, sr)
    onsets_before = detect_onsets_from_envelope(onset_env, hop, sr, units="time")
    candidates: list[dict[str, object]] = []
    seed_rows: list[tuple[str, float]] = []
    for seed in seed_bpms or []:
        seed_value = float(seed)
        if np.isfinite(seed_value) and seed_value > 0.0:
            seed_rows.append(("seed", seed_value))
    seed_rows.append(("detected", detected_bpm))

    candidate_rows: list[tuple[float, str, float]] = []
    seen_bpms: set[float] = set()
    for seed_source, seed_bpm in seed_rows:
        for candidate_bpm in _candidate_bpm_aliases(seed_bpm):
            if candidate_bpm in seen_bpms:
                continue
            seen_bpms.add(candidate_bpm)
            candidate_rows.append((candidate_bpm, seed_source, seed_bpm))

    primary_bpm = float(seed_rows[0][1] if seed_rows else detected_bpm)
    for candidate_bpm, seed_source, seed_bpm in candidate_rows:
        try:
            curve = onset_quantize_curve_from_features(onset_env, hop, sr, duration_sec, candidate_bpm, resolution)
            grid = build_grid(duration_sec, candidate_bpm, resolution)
            metrics = _candidate_metrics_fast(
                np.asarray(curve["source_times"], dtype=np.float32),
                np.asarray(curve["target_times"], dtype=np.float32),
                grid,
                onsets_before,
            )
            fixed_lock_ratio = _fixed_window_lock_ratio_proxy(
                np.asarray(curve["source_times"], dtype=np.float32),
                np.asarray(curve["target_times"], dtype=np.float32),
                grid,
                onsets_before,
                duration_sec,
            )
        except Exception as exc:
            candidates.append({"bpm": float(candidate_bpm), "error": type(exc).__name__})
            continue
        candidates.append(
            {
                "bpm": float(candidate_bpm),
                "seed_source": seed_source,
                "seed_bpm": float(seed_bpm),
                "avg_abs_error_after_sec": float(metrics.get("avg_abs_error_after_sec", 999.0)),
                "improvement_pct": float(metrics.get("improvement_pct", 0.0)),
                "phase_window_abs_max_after_sec": float(metrics.get("phase_window_abs_max_after_sec", 999.0)),
                "phase_window_span_after_sec": float(metrics.get("phase_window_span_after_sec", 999.0)),
                "fixed_locked_ratio_proxy": float(fixed_lock_ratio),
            }
        )

    scored = [item for item in candidates if "avg_abs_error_after_sec" in item]
    if not scored:
        return detected_bpm, {"detected_bpm": detected_bpm, "selected_bpm": detected_bpm, "candidates": candidates}

    def rank(item: dict[str, object]) -> tuple[float, float, float]:
        return (
            float(item.get("avg_abs_error_after_sec", 999.0)),
            float(item.get("phase_window_abs_max_after_sec", 999.0)),
            abs(float(item.get("bpm", detected_bpm)) - round(primary_bpm)),
        )

    selected = min(scored, key=rank)
    best_fixed_ratio = float(selected.get("fixed_locked_ratio_proxy", 0.0))
    fixed_preferred = max(scored, key=lambda item: (float(item.get("fixed_locked_ratio_proxy", 0.0)), -float(item.get("avg_abs_error_after_sec", 999.0))))
    fixed_preferred_ratio = float(fixed_preferred.get("fixed_locked_ratio_proxy", 0.0))
    fixed_preferred_error = float(fixed_preferred.get("avg_abs_error_after_sec", 999.0))
    selected_error = float(selected.get("avg_abs_error_after_sec", 999.0))
    if (
        best_fixed_ratio < 0.25
        and fixed_preferred_ratio >= best_fixed_ratio + 0.35
        or (
            best_fixed_ratio < 0.75
            and fixed_preferred_ratio >= 0.8
            and fixed_preferred_error <= selected_error + 0.012
        )
    ):
        selected = fixed_preferred
    primary_rounded_bpm = float(round(primary_bpm))
    primary_matches = [item for item in scored if abs(float(item.get("bpm", -1.0)) - primary_rounded_bpm) < 1e-6]
    if primary_matches:
        primary_selected = min(primary_matches, key=rank)
        best_error = float(selected.get("avg_abs_error_after_sec", 999.0))
        primary_error = float(primary_selected.get("avg_abs_error_after_sec", 999.0))
        best_phase = float(selected.get("phase_window_abs_max_after_sec", 999.0))
        primary_phase = float(primary_selected.get("phase_window_abs_max_after_sec", 999.0))
        best_fixed = float(selected.get("fixed_locked_ratio_proxy", 0.0))
        primary_fixed = float(primary_selected.get("fixed_locked_ratio_proxy", 0.0))
        if (
            primary_error <= best_error + max(0.0025, best_error * 0.08)
            and primary_phase <= best_phase + 0.012
            and primary_fixed >= best_fixed - 0.12
        ):
            selected = primary_selected
    seed_alias_matches = [item for item in scored if str(item.get("seed_source", "")) == "seed"]
    if seed_alias_matches:
        seed_alias_selected = min(
            seed_alias_matches,
            key=lambda item: (
                float(item.get("avg_abs_error_after_sec", 999.0)),
                float(item.get("phase_window_abs_max_after_sec", 999.0)),
                abs(float(item.get("bpm", primary_bpm)) - primary_rounded_bpm),
            ),
        )
        best_error = float(selected.get("avg_abs_error_after_sec", 999.0))
        seed_error = float(seed_alias_selected.get("avg_abs_error_after_sec", 999.0))
        best_phase = float(selected.get("phase_window_abs_max_after_sec", 999.0))
        seed_phase = float(seed_alias_selected.get("phase_window_abs_max_after_sec", 999.0))
        best_fixed = float(selected.get("fixed_locked_ratio_proxy", 0.0))
        seed_fixed = float(seed_alias_selected.get("fixed_locked_ratio_proxy", 0.0))
        selected_is_primary = abs(float(selected.get("bpm", -1.0)) - primary_rounded_bpm) < 1e-6
        seed_materially_better = seed_error + max(0.0025, best_error * 0.08) < best_error
        if (
            seed_phase <= best_phase + 0.012
            and seed_fixed >= best_fixed - 0.05
            and (
                seed_error <= best_error + max(0.0025, best_error * 0.08)
                and not selected_is_primary
                or seed_materially_better
            )
        ):
            selected = seed_alias_selected
    selected_bpm = float(selected["bpm"])
    return selected_bpm, {
        "detected_bpm": detected_bpm,
        "selected_bpm": selected_bpm,
        "seed_bpms": [float(row[1]) for row in seed_rows],
        "selected_seed_source": str(selected.get("seed_source", "")),
        "selected_seed_bpm": float(selected.get("seed_bpm", detected_bpm)),
        "candidates": candidates,
    }


def _manifest_track_metadata_bpm(track: dict) -> float | None:
    tags = track.get("metadata_tags")
    return metadata_bpm_from_tags(tags if isinstance(tags, dict) else None)


def _manifest_track_non_song_skip_reason(track: dict) -> str | None:
    """Identify obvious non-song clips that should not define full-track DAW-lock quality."""
    text_parts = [
        str(track.get("relative_path") or ""),
        str(track.get("path") or ""),
        str(track.get("title") or ""),
        str(track.get("name") or ""),
    ]
    title = " ".join(text_parts).lower()
    normalized = " ".join(title.replace("_", " ").replace("-", " ").split())
    if bool(track.get("is_full_song")):
        return None
    declared_type = str(track.get("content_type") or track.get("kind") or "").strip().lower()
    if declared_type in {"sfx", "sound_effect", "sound-effect", "cue_montage", "game_cue_montage", "montage"}:
        return f"declared_non_song_content_type:{declared_type}"
    if any(token in normalized for token in ("(drums)", "(bass)", "(vocals)", "(other)")):
        return "stem_file_not_full_song"
    if "mario kart" in normalized and any(token in normalized for token in ("race start", "goals", "sound effect", "sfx")):
        return "game_cue_montage_not_full_song"
    if "evolution of" in normalized and any(token in normalized for token in ("race start", "goals", "sound effect", "sfx")):
        return "cue_evolution_montage_not_full_song"
    return None


def benchmark_metronome_canary_manifest(
    manifest_path: Path,
    *,
    target_bpm: float | None = None,
    resolution: int = 8,
    groove_preserve: int = 50,
    mode: str = "hybrid",
    max_elapsed_sec: float = 130.0,
    max_files: int | None = None,
    incremental_output: Path | None = None,
    resume: bool = False,
    skip_audio_errors: bool = False,
) -> dict:
    tracks = load_audio_manifest_tracks(manifest_path)
    if not tracks:
        raise RuntimeError(f"Audio manifest has no tracks: {manifest_path}")
    if max_files is not None and int(max_files) > 0:
        tracks = tracks[: int(max_files)]
    existing_results = (
        _load_incremental_suite_results(incremental_output, "metronome_canary_suite")
        if resume and incremental_output is not None
        else {}
    )
    files: list[dict] = []
    failed_files: list[dict] = []
    skipped_files: list[dict] = []
    for track in tracks:
        audio_path = Path(str(track.get("path") or ""))
        non_song_reason = _manifest_track_non_song_skip_reason(track)
        if non_song_reason is not None:
            skipped_files.append(
                {
                    "audio_path": str(audio_path),
                    "relative_path": track.get("relative_path"),
                    "reason": non_song_reason,
                    "message": "Skipped because this appears to be a montage, stem, SFX, or cue collection rather than a full-song DAW-lock target.",
                }
            )
            if incremental_output is not None:
                _write_incremental_suite_report(
                    incremental_output,
                    "metronome_canary_suite",
                    _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files, skipped_files),
                )
            continue
        if not audio_path.exists():
            if not skip_audio_errors:
                raise RuntimeError(f"Manifest audio file is missing: {audio_path}")
            failed_files.append(
                {
                    "audio_path": str(audio_path),
                    "relative_path": track.get("relative_path"),
                    "error": "missing_file",
                    "message": f"Manifest audio file is missing: {audio_path}",
                }
            )
            continue
        result_key = _manifest_result_key(audio_path)
        if result_key in existing_results:
            files.append(existing_results[result_key])
            continue
        track_bpm = target_bpm
        bpm_source = "override"
        bpm_detection: dict[str, object] = {}
        if track_bpm is None and track.get("filename_bpm") is not None:
            track_bpm = float(track["filename_bpm"])
            bpm_source = "filename"
        metadata_bpm = _manifest_track_metadata_bpm(track)
        if track_bpm is None and metadata_bpm is not None:
            try:
                track_bpm, bpm_detection = _select_manifest_track_bpm_from_audio(
                    audio_path,
                    resolution=resolution,
                    seed_bpms=[float(metadata_bpm)],
                )
                metadata_rounded = float(round(float(metadata_bpm)))
                bpm_source = "metadata" if abs(float(track_bpm) - metadata_rounded) < 0.5 else "metadata_alias"
            except Exception as exc:
                if not skip_audio_errors:
                    raise RuntimeError(f"Metronome canary manifest track needs target BPM: {audio_path}") from exc
                failed_files.append(
                    {
                        "audio_path": str(audio_path),
                        "relative_path": track.get("relative_path"),
                        "error": "verify_metadata_bpm_failed",
                        "message": f"Could not verify metadata BPM for metronome canary {audio_path}: {exc}",
                    }
                )
                if incremental_output is not None:
                    _write_incremental_suite_report(
                        incremental_output,
                        "metronome_canary_suite",
                        _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files, skipped_files),
                    )
                continue
        if track_bpm is None:
            try:
                track_bpm, bpm_detection = _select_manifest_track_bpm_from_audio(audio_path, resolution=resolution)
                bpm_source = "detected"
            except Exception as exc:
                if not skip_audio_errors:
                    raise RuntimeError(f"Metronome canary manifest track needs target BPM: {audio_path}") from exc
                failed_files.append(
                    {
                        "audio_path": str(audio_path),
                        "relative_path": track.get("relative_path"),
                        "error": "detect_target_bpm_failed",
                        "message": f"Could not detect target BPM for metronome canary {audio_path}: {exc}",
                    }
                )
                if incremental_output is not None:
                    _write_incremental_suite_report(
                        incremental_output,
                        "metronome_canary_suite",
                        _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files, skipped_files),
                    )
                continue
        try:
            item = benchmark_metronome_canary(
                audio_path,
                target_bpm=float(track_bpm),
                resolution=resolution,
                groove_preserve=groove_preserve,
                mode=mode,
            )
            item["manifest_track"] = {
                "relative_path": track.get("relative_path"),
                "filename_bpm": track.get("filename_bpm"),
                "metadata_bpm": float(metadata_bpm) if metadata_bpm is not None else None,
                "detected_bpm": float(bpm_detection.get("detected_bpm", track_bpm)) if bpm_source in {"detected", "metadata", "metadata_alias"} else None,
                "selected_detected_bpm": float(track_bpm) if bpm_source in {"detected", "metadata", "metadata_alias"} else None,
                "bpm_detection": bpm_detection if bpm_source in {"detected", "metadata", "metadata_alias"} else None,
                "duration_sec": track.get("duration_sec"),
                "target_bpm_source": bpm_source,
            }
            item["metronome_canary_gate"] = evaluate_metronome_canary_gate(item, max_elapsed_sec=max_elapsed_sec)
            files.append(item)
            if incremental_output is not None:
                _write_incremental_suite_report(
                    incremental_output,
                    "metronome_canary_suite",
                    _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files, skipped_files),
                )
        except Exception as exc:
            if not skip_audio_errors:
                raise
            failed_files.append(
                {
                    "audio_path": str(audio_path),
                    "relative_path": track.get("relative_path"),
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
            if incremental_output is not None:
                _write_incremental_suite_report(
                    incremental_output,
                    "metronome_canary_suite",
                    _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files, skipped_files),
                )

    return _metronome_canary_manifest_suite(manifest_path, tracks, files, failed_files, skipped_files)


def _metronome_canary_manifest_suite(
    manifest_path: Path,
    tracks: list[dict],
    files: list[dict],
    failed_files: list[dict],
    skipped_files: list[dict] | None = None,
) -> dict:
    skipped_files = list(skipped_files or [])
    passed = [item for item in files if dict(item.get("metronome_canary_gate") or {}).get("passed")]
    failed = [item for item in files if not dict(item.get("metronome_canary_gate") or {}).get("passed")]
    return {
        "manifest": str(manifest_path),
        "summary": {
            "file_count": len(files),
            "requested_track_count": len(tracks),
            "pass_count": len(passed),
            "fail_count": len(failed),
            "failed_file_count": len(failed_files),
            "skipped_non_song_count": len(skipped_files),
            "all_passed": len(files) > 0 and not failed and not failed_files,
        },
        "failed_files": failed_files,
        "skipped_files": skipped_files,
        "files": files,
    }


def evaluate_metronome_canary_gate(
    canary_report: dict,
    *,
    require_verdict: str = "daw_locked",
    min_segment_locked_ratio: float = 1.0,
    min_fixed_locked_ratio: float = 0.8,
    max_unstable_segments: int = 0,
    max_meltdown_segments: int = 0,
    max_unstable_windows: int = 0,
    max_meltdown_windows: int = 0,
    max_avg_error_after_sec: float = 0.035,
    max_beat_phase_avg_sec: float = 0.045,
    max_beat_phase_intro_avg_sec: float = 0.045,
    max_elapsed_sec: float = 130.0,
    require_metronome_check: bool = True,
    require_daw_lock_diagnostics: bool = True,
    require_runtime_config: bool = True,
) -> dict:
    payload = dict(canary_report.get("metronome_canary") or canary_report)
    timing = dict(payload.get("timing_metrics") or {})
    lock = dict(payload.get("metronome_lock") or {})
    fixed = dict(lock.get("fixed_windows") or {})
    continuity = dict(lock.get("warp_continuity") or {})
    beat_phase = dict(lock.get("beat_phase") or {})
    metronome_check = dict(payload.get("metronome_check") or {})
    diagnostics = dict(payload.get("daw_lock_diagnostics") or {})
    runtime_config = dict(payload.get("runtime_config") or {})
    expected_runtime_config = _benchmark_runtime_config()
    diagnostic_items = list(diagnostics.get("items") or [])
    first_diagnostic = dict(diagnostic_items[0]) if diagnostic_items else {}
    blocking_diagnostics = [
        dict(item)
        for item in diagnostic_items
        if str(dict(item).get("severity", "")).lower() not in {"info", "note"}
    ]
    processing = dict(payload.get("processing_timing") or {})
    fixed_locked_ratio = float(fixed.get("effective_locked_ratio", fixed.get("locked_ratio", 0.0)))
    target_bpm = float(payload.get("target_bpm", metronome_check.get("target_bpm", 0.0)) or 0.0)
    check_bpm = float(metronome_check.get("target_bpm", 0.0) or 0.0)

    elapsed_sec = float(processing.get("elapsed_before_report_write_sec", payload.get("wall_time_sec", 0.0)) or 0.0)
    segment_locked_ratio = float(lock.get("effective_locked_ratio", lock.get("locked_ratio", 0.0)))
    avg_error_after_sec = float(timing.get("avg_abs_error_after_sec", 999.0))
    metronome_check_ok = (
        bool(metronome_check.get("generated", False))
        and str(metronome_check.get("filename", "")) == "metronome_check.wav"
        and (target_bpm <= 0.0 or abs(check_bpm - target_bpm) <= 1e-6)
    )
    diagnostics_ok = (
        "issue_count" in diagnostics
        and len(blocking_diagnostics) == 0
        and isinstance(diagnostics.get("items"), list)
    )
    runtime_config_ok = all(runtime_config.get(key) == value for key, value in expected_runtime_config.items())
    beat_phase_ok = (
        bool(beat_phase.get("offbeat_alias", False))
        or bool(beat_phase.get("grid_authoritative", False))
        or (
            bool(beat_phase.get("locked", True))
            and bool(beat_phase.get("intro_locked", True))
            and float(beat_phase.get("avg_abs_error_after_sec", 0.0)) <= float(max_beat_phase_avg_sec)
            and float(beat_phase.get("intro_avg_abs_error_after_sec", 0.0)) <= float(max_beat_phase_intro_avg_sec)
        )
    )
    beat_phase_measured = (
        int(beat_phase.get("beat_count", 0) or 0) >= 4
        or "locked" in beat_phase
        or "intro_locked" in beat_phase
        or bool(beat_phase.get("offbeat_alias", False))
        or bool(beat_phase.get("grid_authoritative", False))
    )
    avg_error_slack_sec = (
        0.010
        if bool(lock.get("fixed_grid_authoritative", False)) and fixed_locked_ratio >= 0.999 and segment_locked_ratio >= 0.999
        else 0.005
    )
    low_error_near_full_lock_authoritative = (
        str(lock.get("verdict", "")) == require_verdict
        and fixed_locked_ratio >= 0.95
        and segment_locked_ratio >= 0.95
        and avg_error_after_sec <= 0.015
        and int(lock.get("unstable_segments", 0)) <= int(max_unstable_segments)
        and int(lock.get("meltdown_segments", 0)) <= int(max_meltdown_segments)
        and int(fixed.get("unstable_windows", 0)) <= int(max_unstable_windows)
        and int(fixed.get("meltdown_windows", 0)) <= int(max_meltdown_windows)
        and str(continuity.get("verdict", "continuous")) != "jump_risk"
        and beat_phase_measured
        and beat_phase_ok
        and diagnostics_ok
    )
    fixed_grid_authoritative = (
        bool(lock.get("fixed_grid_authoritative", False))
        or low_error_near_full_lock_authoritative
        or (
            str(lock.get("verdict", "")) == require_verdict
            and fixed_locked_ratio >= 0.999
            and int(lock.get("unstable_segments", 0)) <= int(max_unstable_segments)
            and int(lock.get("meltdown_segments", 0)) <= int(max_meltdown_segments)
            and int(fixed.get("unstable_windows", 0)) <= int(max_unstable_windows)
            and int(fixed.get("meltdown_windows", 0)) <= int(max_meltdown_windows)
            and str(continuity.get("verdict", "continuous")) != "jump_risk"
            and beat_phase_measured
            and beat_phase_ok
            and diagnostics_ok
            and avg_error_after_sec <= float(max_avg_error_after_sec) + avg_error_slack_sec
            and segment_locked_ratio >= (0.95 if avg_error_after_sec > float(max_avg_error_after_sec) else 0.8)
        )
    )
    fixed_grid_authoritative = (
        fixed_grid_authoritative
        and str(lock.get("verdict", "")) == require_verdict
        and fixed_locked_ratio >= (
            0.95 if (bool(lock.get("fixed_grid_authoritative", False)) or low_error_near_full_lock_authoritative) else 0.999
        )
        and int(fixed.get("unstable_windows", 0)) <= int(max_unstable_windows)
        and int(fixed.get("meltdown_windows", 0)) <= int(max_meltdown_windows)
        and str(continuity.get("verdict", "continuous")) != "jump_risk"
        and beat_phase_measured
        and beat_phase_ok
        and diagnostics_ok
        and avg_error_after_sec <= float(max_avg_error_after_sec) + avg_error_slack_sec
        and segment_locked_ratio
        >= (
            0.70
            if bool(lock.get("fixed_grid_authoritative", False))
            else (0.95 if (low_error_near_full_lock_authoritative or avg_error_after_sec > float(max_avg_error_after_sec)) else 0.8)
        )
    )
    segment_ratio_ok = segment_locked_ratio >= float(min_segment_locked_ratio) or fixed_grid_authoritative
    checks = {
        "verdict": str(lock.get("verdict", "")) == require_verdict,
        "segment_locked_ratio": segment_ratio_ok or fixed_grid_authoritative,
        "fixed_locked_ratio": fixed_locked_ratio >= float(min_fixed_locked_ratio),
        "unstable_segments": int(lock.get("unstable_segments", 0)) <= int(max_unstable_segments) or fixed_grid_authoritative,
        "meltdown_segments": int(lock.get("meltdown_segments", 0)) <= int(max_meltdown_segments) or fixed_grid_authoritative,
        "unstable_windows": int(fixed.get("unstable_windows", 0)) <= int(max_unstable_windows),
        "meltdown_windows": int(fixed.get("meltdown_windows", 0)) <= int(max_meltdown_windows),
        "warp_continuity": str(continuity.get("verdict", "continuous")) != "jump_risk",
        "beat_phase": beat_phase_ok,
        "avg_error_after": avg_error_after_sec <= float(max_avg_error_after_sec) or fixed_grid_authoritative,
        "elapsed_sec": elapsed_sec <= float(max_elapsed_sec),
        "metronome_check": metronome_check_ok if require_metronome_check else True,
        "daw_lock_diagnostics": diagnostics_ok if require_daw_lock_diagnostics else True,
        "runtime_config": runtime_config_ok if require_runtime_config else True,
    }
    failed_checks = [name for name, passed_check in checks.items() if not passed_check]
    passed = all(checks.values())
    failure_summary = (
        "Metronome canary gate passed."
        if passed
        else f"Metronome canary gate failed checks: {', '.join(failed_checks)}."
    )
    return {
        "passed": bool(passed),
        "failure_summary": failure_summary,
        "failed_checks": failed_checks,
        "checks": checks,
        "thresholds": {
            "require_verdict": require_verdict,
            "min_segment_locked_ratio": float(min_segment_locked_ratio),
            "min_fixed_locked_ratio": float(min_fixed_locked_ratio),
            "max_unstable_segments": int(max_unstable_segments),
            "max_meltdown_segments": int(max_meltdown_segments),
            "max_unstable_windows": int(max_unstable_windows),
            "max_meltdown_windows": int(max_meltdown_windows),
            "max_avg_error_after_sec": float(max_avg_error_after_sec),
            "max_beat_phase_avg_sec": float(max_beat_phase_avg_sec),
            "max_beat_phase_intro_avg_sec": float(max_beat_phase_intro_avg_sec),
            "max_elapsed_sec": float(max_elapsed_sec),
            "require_metronome_check": bool(require_metronome_check),
            "require_daw_lock_diagnostics": bool(require_daw_lock_diagnostics),
            "require_runtime_config": bool(require_runtime_config),
            "expected_runtime_config": expected_runtime_config,
        },
        "observed": {
            "target_bpm": target_bpm,
            "verdict": str(lock.get("verdict", "")),
            "segment_locked_ratio": segment_locked_ratio,
            "raw_segment_locked_ratio": float(lock.get("locked_ratio", 0.0)),
            "excluded_segments": int(lock.get("excluded_segments", 0)),
            "segment_lock_fixed_grid_authoritative": bool(fixed_grid_authoritative),
            "fixed_locked_ratio": fixed_locked_ratio,
            "raw_fixed_locked_ratio": float(fixed.get("locked_ratio", 0.0)),
            "excluded_fixed_windows": int(fixed.get("excluded_windows", 0)),
            "unstable_segments": int(lock.get("unstable_segments", 0)),
            "meltdown_segments": int(lock.get("meltdown_segments", 0)),
            "unstable_windows": int(fixed.get("unstable_windows", 0)),
            "meltdown_windows": int(fixed.get("meltdown_windows", 0)),
            "warp_continuity": str(continuity.get("verdict", "continuous")),
            "max_window_offset_jump_sec": float(continuity.get("max_window_offset_jump_sec", 0.0)),
            "max_window_offset_jump_from_sec": continuity.get("max_window_offset_jump_from_sec"),
            "max_window_offset_jump_to_sec": continuity.get("max_window_offset_jump_to_sec"),
            "max_window_offset_before_sec": continuity.get("max_window_offset_before_sec"),
            "max_window_offset_after_sec": continuity.get("max_window_offset_after_sec"),
            "p99_local_stretch_delta": float(continuity.get("p99_local_stretch_delta", 0.0)),
            "beat_phase_avg_sec": float(beat_phase.get("avg_abs_error_after_sec", 0.0)),
            "beat_phase_intro_avg_sec": float(beat_phase.get("intro_avg_abs_error_after_sec", 0.0)),
            "beat_phase_median_signed_sec": float(beat_phase.get("median_signed_error_after_sec", 0.0)),
            "beat_phase_offbeat_alias": bool(beat_phase.get("offbeat_alias", False)),
            "beat_phase_grid_authoritative": bool(beat_phase.get("grid_authoritative", False)),
            "beat_phase_locked": bool(beat_phase.get("locked", True)),
            "beat_phase_intro_locked": bool(beat_phase.get("intro_locked", True)),
            "avg_error_after_sec": avg_error_after_sec,
            "elapsed_sec": elapsed_sec,
            "metronome_check_generated": bool(metronome_check.get("generated", False)),
            "metronome_check_filename": str(metronome_check.get("filename", "")),
            "metronome_check_bpm": check_bpm,
            "daw_lock_diagnostic_issue_count": int(diagnostics.get("issue_count", 999)),
            "daw_lock_blocking_diagnostic_count": int(len(blocking_diagnostics)),
            "daw_lock_diagnostic_summary": str(diagnostics.get("summary", "")),
            "first_daw_lock_diagnostic_type": str(first_diagnostic.get("type", "")),
            "first_daw_lock_diagnostic_severity": str(first_diagnostic.get("severity", "")),
            "runtime_config": runtime_config,
        },
    }


def _suite_payload(report: dict) -> dict:
    if "summary" in report and "files" in report:
        return report
    if "real_audio_suite" in report:
        suite = report["real_audio_suite"]
        if isinstance(suite, dict) and "summary" in suite and "files" in suite:
            return suite
    raise RuntimeError("Report does not contain a benchmark suite payload")


def load_benchmark_report(report_path: Path) -> dict:
    return json.loads(report_path.read_text(encoding="utf-8"))


def compare_benchmark_suites(
    baseline_report: dict,
    candidate_report: dict,
    *,
    baseline_label: str = "baseline",
    candidate_label: str = "candidate",
) -> dict:
    baseline_suite = _suite_payload(baseline_report)
    candidate_suite = _suite_payload(candidate_report)

    baseline_files = {str(item["audio_path"]): item for item in baseline_suite["files"]}
    candidate_files = {str(item["audio_path"]): item for item in candidate_suite["files"]}
    shared_paths = sorted(set(baseline_files) & set(candidate_files))
    missing_from_candidate = sorted(set(baseline_files) - set(candidate_files))
    extra_in_candidate = sorted(set(candidate_files) - set(baseline_files))
    if not shared_paths:
        raise RuntimeError("No overlapping benchmark files found between reports")

    file_rows: list[dict[str, object]] = []
    candidate_wins = 0
    baseline_wins = 0
    ties = 0
    deltas: list[float] = []

    for audio_path in shared_paths:
        baseline_item = baseline_files[audio_path]
        candidate_item = candidate_files[audio_path]
        baseline_error = float(baseline_item["hybrid_effective"]["avg_abs_error_after_sec"])
        candidate_error = float(candidate_item["hybrid_effective"]["avg_abs_error_after_sec"])
        delta = candidate_error - baseline_error
        deltas.append(delta)
        if delta < -1e-9:
            winner = candidate_label
            candidate_wins += 1
        elif delta > 1e-9:
            winner = baseline_label
            baseline_wins += 1
        else:
            winner = "tie"
            ties += 1
        file_rows.append(
            {
                "audio_path": audio_path,
                "baseline_error_after_sec": baseline_error,
                "candidate_error_after_sec": candidate_error,
                "delta_candidate_minus_baseline_sec": delta,
                "winner": winner,
            }
        )

    return {
        "baseline_label": baseline_label,
        "candidate_label": candidate_label,
        "baseline_summary": baseline_suite["summary"],
        "candidate_summary": candidate_suite["summary"],
        "shared_file_count": len(shared_paths),
        "missing_from_candidate": missing_from_candidate,
        "extra_in_candidate": extra_in_candidate,
        "candidate_wins": candidate_wins,
        "baseline_wins": baseline_wins,
        "ties": ties,
        "avg_delta_candidate_minus_baseline_sec": float(np.mean(deltas)),
        "max_candidate_regression_sec": float(max(deltas)),
        "max_candidate_improvement_sec": float(min(deltas)),
        "files": file_rows,
    }


def _processing_elapsed_sec(item: dict) -> float | None:
    timing = item.get("processing_timing")
    if not isinstance(timing, dict):
        return None
    elapsed = timing.get("elapsed_sec")
    if elapsed is None:
        return None
    try:
        elapsed_sec = float(elapsed)
    except (TypeError, ValueError):
        return None
    return elapsed_sec if elapsed_sec > 0 else None


def _avg_processing_elapsed_sec(suite: dict, shared_paths: set[str] | None = None) -> float | None:
    elapsed_values: list[float] = []
    for item in suite.get("files", []):
        if shared_paths is not None and str(item.get("audio_path", "")) not in shared_paths:
            continue
        elapsed_sec = _processing_elapsed_sec(item)
        if elapsed_sec is not None:
            elapsed_values.append(elapsed_sec)
    if not elapsed_values:
        return None
    return float(np.mean(elapsed_values))


def evaluate_benchmark_candidate_gate(
    baseline_report: dict,
    candidate_report: dict,
    *,
    max_avg_regression_sec: float = 0.001,
    max_per_file_regression_sec: float = 0.003,
    min_speedup_pct: float = 0.0,
) -> dict:
    comparison = compare_benchmark_suites(
        baseline_report,
        candidate_report,
        baseline_label="quality_baseline",
        candidate_label="candidate_mode",
    )
    baseline_suite = _suite_payload(baseline_report)
    candidate_suite = _suite_payload(candidate_report)
    shared_paths = {str(row["audio_path"]) for row in comparison["files"]}
    baseline_avg_elapsed = _avg_processing_elapsed_sec(baseline_suite, shared_paths)
    candidate_avg_elapsed = _avg_processing_elapsed_sec(candidate_suite, shared_paths)
    speedup_pct: float | None = None
    if baseline_avg_elapsed is not None and candidate_avg_elapsed is not None and baseline_avg_elapsed > 0:
        speedup_pct = 100.0 * (baseline_avg_elapsed - candidate_avg_elapsed) / baseline_avg_elapsed

    avg_delta = float(comparison["avg_delta_candidate_minus_baseline_sec"])
    max_regression = float(comparison["max_candidate_regression_sec"])
    avg_regression_pass = avg_delta <= float(max_avg_regression_sec)
    max_regression_pass = max_regression <= float(max_per_file_regression_sec)
    speedup_required = float(min_speedup_pct) > 0
    speedup_pass = True
    if speedup_required:
        speedup_pass = speedup_pct is not None and speedup_pct >= float(min_speedup_pct)

    checks = {
        "avg_regression": avg_regression_pass,
        "max_per_file_regression": max_regression_pass,
        "speedup": speedup_pass,
    }
    reasons: list[str] = []
    if avg_regression_pass:
        reasons.append(f"avg regression {avg_delta:.6f}s stays within {float(max_avg_regression_sec):.6f}s")
    else:
        reasons.append(f"avg regression {avg_delta:.6f}s exceeds {float(max_avg_regression_sec):.6f}s")
    if max_regression_pass:
        reasons.append(f"worst per-file regression {max_regression:.6f}s stays within {float(max_per_file_regression_sec):.6f}s")
    else:
        reasons.append(f"worst per-file regression {max_regression:.6f}s exceeds {float(max_per_file_regression_sec):.6f}s")
    if speedup_required:
        if speedup_pass:
            reasons.append(f"speedup {float(speedup_pct or 0.0):.2f}% meets required {float(min_speedup_pct):.2f}%")
        elif speedup_pct is None:
            reasons.append("speedup could not be measured because one or both reports lack processing_timing.elapsed_sec")
        else:
            reasons.append(f"speedup {speedup_pct:.2f}% is below required {float(min_speedup_pct):.2f}%")

    return {
        "passed": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "max_avg_regression_sec": float(max_avg_regression_sec),
            "max_per_file_regression_sec": float(max_per_file_regression_sec),
            "min_speedup_pct": float(min_speedup_pct),
        },
        "observed": {
            "avg_regression_sec": avg_delta,
            "max_per_file_regression_sec": max_regression,
            "baseline_avg_processing_elapsed_sec": baseline_avg_elapsed,
            "candidate_avg_processing_elapsed_sec": candidate_avg_elapsed,
            "speedup_pct": speedup_pct,
        },
        "comparison": comparison,
        "reasons": reasons,
        "failure_summary": [name for name, passed in checks.items() if not passed],
    }


def evaluate_promotion_gate(
    legacy_baseline_report: dict,
    legacy_candidate_report: dict,
    mixed_baseline_report: dict | None = None,
    mixed_candidate_report: dict | None = None,
    *,
    min_legacy_improvement_sec: float = 0.001,
    max_mixed_regression_sec: float = 0.0005,
    max_per_file_regression_sec: float = 0.003,
) -> dict:
    legacy_comparison = compare_benchmark_suites(
        legacy_baseline_report,
        legacy_candidate_report,
        baseline_label="active_router",
        candidate_label="candidate_router",
    )
    legacy_avg_delta = float(legacy_comparison["avg_delta_candidate_minus_baseline_sec"])
    legacy_improvement_sec = -legacy_avg_delta
    legacy_pass = legacy_improvement_sec >= float(min_legacy_improvement_sec)
    legacy_regression_pass = float(legacy_comparison["max_candidate_regression_sec"]) <= float(max_per_file_regression_sec)

    mixed_comparison = None
    mixed_regression_sec = 0.0
    mixed_pass = True
    if mixed_baseline_report is not None or mixed_candidate_report is not None:
        if mixed_baseline_report is None or mixed_candidate_report is None:
            raise RuntimeError("Mixed promotion gate requires both baseline and candidate reports")
        mixed_comparison = compare_benchmark_suites(
            mixed_baseline_report,
            mixed_candidate_report,
            baseline_label="active_router",
            candidate_label="candidate_router",
        )
        mixed_regression_sec = max(0.0, float(mixed_comparison["avg_delta_candidate_minus_baseline_sec"]))
        mixed_pass = mixed_regression_sec <= float(max_mixed_regression_sec)

    passed = legacy_pass and legacy_regression_pass and mixed_pass
    reasons: list[str] = []
    if legacy_pass:
        reasons.append(f"legacy avg improvement {legacy_improvement_sec:.6f}s meets gate")
    else:
        reasons.append(f"legacy avg improvement {legacy_improvement_sec:.6f}s is below required {float(min_legacy_improvement_sec):.6f}s")
    if legacy_regression_pass:
        reasons.append(f"worst per-file regression {float(legacy_comparison['max_candidate_regression_sec']):.6f}s stays within {float(max_per_file_regression_sec):.6f}s")
    else:
        reasons.append(f"worst per-file regression {float(legacy_comparison['max_candidate_regression_sec']):.6f}s exceeds {float(max_per_file_regression_sec):.6f}s")
    if mixed_comparison is not None:
        if mixed_pass:
            reasons.append(f"mixed-suite avg regression {mixed_regression_sec:.6f}s stays within {float(max_mixed_regression_sec):.6f}s")
        else:
            reasons.append(f"mixed-suite avg regression {mixed_regression_sec:.6f}s exceeds {float(max_mixed_regression_sec):.6f}s")

    return {
        "passed": passed,
        "thresholds": {
            "min_legacy_improvement_sec": float(min_legacy_improvement_sec),
            "max_mixed_regression_sec": float(max_mixed_regression_sec),
            "max_per_file_regression_sec": float(max_per_file_regression_sec),
        },
        "legacy": {
            "avg_improvement_sec": legacy_improvement_sec,
            "avg_delta_candidate_minus_baseline_sec": legacy_avg_delta,
            "max_regression_sec": float(legacy_comparison["max_candidate_regression_sec"]),
            "comparison": legacy_comparison,
        },
        "mixed": {
            "avg_regression_sec": mixed_regression_sec,
            "comparison": mixed_comparison,
        },
        "reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=Path, default=Path("data/examples"))
    parser.add_argument("--audio", type=Path, default=Path("stayin-alive-serban-mix.wav"))
    parser.add_argument("--audio-dir", type=Path, default=None)
    parser.add_argument("--audio-manifest", type=Path, default=None)
    parser.add_argument("--metronome-canary", action="store_true")
    parser.add_argument("--default-suite", action="store_true")
    parser.add_argument("--legacy-suite", action="store_true")
    parser.add_argument("--mixed-suite", action="store_true")
    parser.add_argument("--resolution", type=int, default=8)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--target-bpm", type=float, default=0.0)
    parser.add_argument("--groove-preserve", type=int, default=50)
    parser.add_argument("--mode", type=str, default="hybrid")
    parser.add_argument("--output", type=Path, default=Path("outputs/benchmark_report.json"))
    parser.add_argument("--incremental-output", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=Path("outputs/benchmark_cache"))
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--proxy-only", action="store_true")
    parser.add_argument("--skip-audio-errors", action="store_true")
    parser.add_argument("--datasets", type=str, default="")
    parser.add_argument("--clip-start", type=float, default=0.0)
    parser.add_argument("--clip-duration", type=float, default=0.0)
    parser.add_argument("--compare-baseline-report", type=Path, default=None)
    parser.add_argument("--compare-candidate-report", type=Path, default=None)
    parser.add_argument("--gate-legacy-baseline-report", type=Path, default=None)
    parser.add_argument("--gate-legacy-candidate-report", type=Path, default=None)
    parser.add_argument("--gate-mixed-baseline-report", type=Path, default=None)
    parser.add_argument("--gate-mixed-candidate-report", type=Path, default=None)
    parser.add_argument("--gate-metronome-canary-report", type=Path, default=None)
    parser.add_argument("--benchmark-gate-baseline-report", type=Path, default=None)
    parser.add_argument("--benchmark-gate-candidate-report", type=Path, default=None)
    parser.add_argument("--benchmark-gate-max-avg-regression-sec", type=float, default=0.001)
    parser.add_argument("--benchmark-gate-max-per-file-regression-sec", type=float, default=0.003)
    parser.add_argument("--benchmark-gate-min-speedup-pct", type=float, default=0.0)
    parser.add_argument("--min-legacy-improvement-sec", type=float, default=0.001)
    parser.add_argument("--max-mixed-regression-sec", type=float, default=0.0005)
    parser.add_argument("--max-per-file-regression-sec", type=float, default=0.003)
    parser.add_argument("--max-canary-elapsed-sec", type=float, default=130.0)
    parser.add_argument("--max-canary-files", type=int, default=0)
    parser.add_argument("--inference-accelerator", choices=["auto", "npu", "gpu", "cpu", "torch", "cuda"], default=None)
    parser.add_argument("--inference-candidate-strategy", choices=["all", "core4", "core4_adaptive", "core4_adaptive_plus", "core4_adaptive_plus_qf", "core5"], default=None)
    parser.add_argument("--hybrid-search-strategy", choices=["all", "routed", "routed_top1", "routed_top2", "core4", "core5"], default=None)
    parser.add_argument("--hybrid-verify-topk", type=int, default=None)
    parser.add_argument("--fast-proxy-hybrid-scoring", action="store_true")
    parser.add_argument("--max-inference-models", type=int, default=0)
    args = parser.parse_args()
    if args.inference_accelerator is not None:
        os.environ["BOXBOX_INFER_ACCELERATOR"] = args.inference_accelerator
    elif not os.environ.get("BOXBOX_INFER_ACCELERATOR", "").strip():
        os.environ["BOXBOX_INFER_ACCELERATOR"] = "torch"
    if args.inference_candidate_strategy is not None:
        os.environ["BOXBOX_INFER_CANDIDATE_STRATEGY"] = args.inference_candidate_strategy
    elif not os.environ.get("BOXBOX_INFER_CANDIDATE_STRATEGY", "").strip():
        os.environ["BOXBOX_INFER_CANDIDATE_STRATEGY"] = "core4_adaptive_plus"
    if args.hybrid_search_strategy is not None:
        os.environ["BOXBOX_HYBRID_SEARCH_STRATEGY"] = args.hybrid_search_strategy
    elif not os.environ.get("BOXBOX_HYBRID_SEARCH_STRATEGY", "").strip():
        os.environ["BOXBOX_HYBRID_SEARCH_STRATEGY"] = "core4"
    if args.hybrid_verify_topk is not None:
        os.environ["BOXBOX_HYBRID_VERIFY_TOPK"] = str(max(1, int(args.hybrid_verify_topk)))

    report: dict[str, object] = {}
    compare_mode = args.compare_baseline_report is not None or args.compare_candidate_report is not None
    gate_mode = (
        args.gate_legacy_baseline_report is not None
        or args.gate_legacy_candidate_report is not None
        or args.gate_metronome_canary_report is not None
        or args.benchmark_gate_baseline_report is not None
        or args.benchmark_gate_candidate_report is not None
    )
    if args.compare_baseline_report is not None or args.compare_candidate_report is not None:
        if args.compare_baseline_report is None or args.compare_candidate_report is None:
            raise RuntimeError("Suite comparison requires both baseline and candidate reports")
        report["comparison"] = compare_benchmark_suites(
            load_benchmark_report(args.compare_baseline_report),
            load_benchmark_report(args.compare_candidate_report),
            baseline_label="active_router",
            candidate_label="candidate_router",
        )
    if args.gate_legacy_baseline_report is not None or args.gate_legacy_candidate_report is not None:
        if args.gate_legacy_baseline_report is None or args.gate_legacy_candidate_report is None:
            raise RuntimeError("Promotion gate requires both legacy baseline and legacy candidate reports")
        report["promotion_gate"] = evaluate_promotion_gate(
            load_benchmark_report(args.gate_legacy_baseline_report),
            load_benchmark_report(args.gate_legacy_candidate_report),
            load_benchmark_report(args.gate_mixed_baseline_report) if args.gate_mixed_baseline_report is not None else None,
            load_benchmark_report(args.gate_mixed_candidate_report) if args.gate_mixed_candidate_report is not None else None,
            min_legacy_improvement_sec=args.min_legacy_improvement_sec,
            max_mixed_regression_sec=args.max_mixed_regression_sec,
            max_per_file_regression_sec=args.max_per_file_regression_sec,
        )
    if args.gate_metronome_canary_report is not None:
        report["metronome_canary_gate"] = evaluate_metronome_canary_gate(
            load_benchmark_report(args.gate_metronome_canary_report),
            max_elapsed_sec=args.max_canary_elapsed_sec,
        )
    if args.benchmark_gate_baseline_report is not None or args.benchmark_gate_candidate_report is not None:
        if args.benchmark_gate_baseline_report is None or args.benchmark_gate_candidate_report is None:
            raise RuntimeError("Benchmark candidate gate requires both baseline and candidate reports")
        report["benchmark_candidate_gate"] = evaluate_benchmark_candidate_gate(
            load_benchmark_report(args.benchmark_gate_baseline_report),
            load_benchmark_report(args.benchmark_gate_candidate_report),
            max_avg_regression_sec=args.benchmark_gate_max_avg_regression_sec,
            max_per_file_regression_sec=args.benchmark_gate_max_per_file_regression_sec,
            min_speedup_pct=args.benchmark_gate_min_speedup_pct,
        )
    run_standard_benchmark = not compare_mode and not gate_mode
    suite_source_requested = (
        args.audio_manifest is not None
        or args.audio_dir is not None
        or args.default_suite
        or args.legacy_suite
        or args.mixed_suite
    )
    if run_standard_benchmark and not args.skip_validation:
        report["validation"] = benchmark_validation_set(args.examples, args.resolution, args.val_ratio, args.datasets or None)
    if run_standard_benchmark and args.metronome_canary:
        if args.audio_manifest is not None:
            report["metronome_canary_suite"] = benchmark_metronome_canary_manifest(
                args.audio_manifest,
                target_bpm=args.target_bpm or None,
                resolution=args.resolution,
                groove_preserve=args.groove_preserve,
                mode=args.mode,
                max_elapsed_sec=args.max_canary_elapsed_sec,
                max_files=args.max_canary_files or None,
                incremental_output=args.incremental_output,
                resume=args.resume,
                skip_audio_errors=args.skip_audio_errors,
            )
        elif not args.target_bpm:
            raise RuntimeError("Metronome canary requires --target-bpm")
        else:
            report["metronome_canary"] = benchmark_metronome_canary(
                args.audio,
                target_bpm=float(args.target_bpm),
                resolution=args.resolution,
                groove_preserve=args.groove_preserve,
                mode=args.mode,
            )
    elif run_standard_benchmark and args.proxy_only and not suite_source_requested:
        report["real_audio"] = benchmark_real_audio_proxy(
            args.audio,
            args.target_bpm or None,
            args.resolution,
            clip_start=args.clip_start,
            clip_duration=args.clip_duration or None,
            fast_hybrid_scoring=args.fast_proxy_hybrid_scoring,
            max_inference_models=args.max_inference_models or None,
        )
    elif run_standard_benchmark and not suite_source_requested:
        report["real_audio"] = benchmark_real_audio_cached(
            args.audio,
            args.target_bpm or None,
            args.resolution,
            args.cache_dir,
            clip_start=args.clip_start,
            clip_duration=args.clip_duration or None,
            proxy_only=False,
            max_inference_models=args.max_inference_models or None,
        )
    if run_standard_benchmark and args.audio_dir is not None:
        report["real_audio_suite"] = benchmark_audio_dir(args.audio_dir, args.resolution, args.target_bpm or None)
    if run_standard_benchmark and not args.metronome_canary and args.audio_manifest is not None:
        report["real_audio_suite"] = benchmark_audio_manifest(
            args.audio_manifest,
            args.resolution,
            args.target_bpm or None,
            args.cache_dir,
            clip_start=args.clip_start,
            clip_duration=args.clip_duration or None,
            proxy_only=args.proxy_only,
            incremental_output=args.incremental_output,
            resume=args.resume,
            fast_proxy_hybrid_scoring=args.fast_proxy_hybrid_scoring,
            max_inference_models=args.max_inference_models or None,
            skip_audio_errors=args.skip_audio_errors,
        )
    if run_standard_benchmark and args.default_suite:
        report["real_audio_suite"] = benchmark_default_suite(args.resolution, args.target_bpm or None, args.cache_dir)
    if run_standard_benchmark and args.legacy_suite:
        report["real_audio_suite"] = benchmark_legacy_suite(
            args.resolution,
            args.target_bpm or None,
            args.cache_dir,
            clip_start=args.clip_start,
            clip_duration=args.clip_duration or None,
            proxy_only=args.proxy_only,
            fast_proxy_hybrid_scoring=args.fast_proxy_hybrid_scoring,
            max_inference_models=args.max_inference_models or None,
        )
    if run_standard_benchmark and args.mixed_suite:
        report["real_audio_suite"] = benchmark_mixed_suite(
            args.resolution,
            args.target_bpm or None,
            args.cache_dir,
            clip_start=args.clip_start,
            clip_duration=args.clip_duration or None,
            proxy_only=args.proxy_only,
            fast_proxy_hybrid_scoring=args.fast_proxy_hybrid_scoring,
            max_inference_models=args.max_inference_models or None,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()



