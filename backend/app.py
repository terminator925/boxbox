from __future__ import annotations

import traceback
import time
import uuid
import os
from pathlib import Path
from threading import Lock
from typing import Literal

import librosa
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.audio.dtw_targets import build_grid, onset_quantize_curve_from_features
from backend.audio.drift import estimate_tempo_drift
from backend.audio.events import detect_events, summarize_events
from backend.audio.export import export_metronome_check_wav, export_quantized_wav, export_report, export_tempo_midi
from backend.audio.feedback import (
    build_best_next_pass,
    build_feedback_loop,
    build_segment_feedback,
    classify_segment_bucket,
    rank_next_pass_recommendations,
    record_feedback_entry,
    summarize_recommendation_memory,
    summarize_position_feedback_memory,
    summarize_segment_feedback,
    summarize_feedback_memory,
    update_recommendation_outcome_memory,
    update_recommendation_memory,
    update_recommendation_rerun_memory,
    update_segment_feedback_memory,
    update_feedback_memory,
)
from backend.audio.features import compute_mel_feature, extract_features
from backend.audio.io_utils import audio_info, audio_info_from_meta, convert_to_wav, load_audio, mixdown_mono
from backend.audio.metadata import metadata_bpm_from_tags
from backend.audio.onsets import detect_onsets, detect_onsets_from_envelope
from backend.audio.segmentation import find_segments
from backend.audio.style import infer_style_profile
from backend.audio.tempo import estimate_tempo
from backend.audio.warp import apply_warp
from backend.config import BASE_DIR, CORS_ORIGINS, DEFAULT_GROOVE_PRESERVE, DEFAULT_RESOLUTION, DEFAULT_TARGET_BPM, MODELS_DIR, OUTPUTS_DIR, UPLOADS_DIR
from backend.ml.infer import infer_curve, infer_curve_candidates, model_exists, preferred_inference_accelerator, preferred_inference_candidate_strategy
from backend.ml.device import resolve_device
from backend.ml.metrics import timing_metrics
from backend.services.job_manager import job_manager
from backend.services.storage import ensure_job_dirs, read_json, write_json


app = FastAPI(title="BoxBox API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_quantize_lock = Lock()
_active_quantize_job: str | None = None
_feedback_memory_path = OUTPUTS_DIR / "feedback_memory.json"
_segment_feedback_memory_path = OUTPUTS_DIR / "segment_feedback_memory.json"
_recommendation_memory_path = OUTPUTS_DIR / "recommendation_memory.json"
_frontend_dist_dir = BASE_DIR / "frontend" / "dist"
_frontend_assets_dir = _frontend_dist_dir / "assets"

if _frontend_assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=_frontend_assets_dir), name="frontend-assets")


class QuantizeRequest(BaseModel):
    job_id: str
    target_bpm: float = Field(DEFAULT_TARGET_BPM, ge=40, le=240)
    resolution: int = Field(DEFAULT_RESOLUTION)
    groove_preserve: int = Field(DEFAULT_GROOVE_PRESERVE, ge=0, le=100)
    mode: Literal["dtw", "ml", "hybrid"] = "hybrid"


class FeedbackRequest(BaseModel):
    job_id: str
    feedback: Literal["good", "too_loose", "too_tight", "warbly"]
    notes: str = Field("", max_length=500)
    segment_index: int | None = Field(None, ge=0)


class RecommendationSelectionRequest(BaseModel):
    job_id: str
    recommendation_kind: Literal["learned_default", "best_next_pass"]


def _normalize_target_bpm(target_bpm: float) -> float:
    return float(int(np.clip(round(float(target_bpm)), 40, 240)))


def _metadata_bpm_from_source_meta(source_meta: dict[str, object] | None) -> float | None:
    return metadata_bpm_from_tags(dict((source_meta or {}).get("metadata_tags") or {}))


def _matches_recommendation_controls(req: QuantizeRequest, suggested_controls: dict[str, object] | None) -> bool:
    controls = dict(suggested_controls or {})
    if not controls:
        return False
    return (
        str(controls.get("mode", "")) == str(req.mode)
        and abs(float(controls.get("target_bpm", 0.0)) - float(req.target_bpm)) <= 1e-6
        and int(controls.get("resolution", -1)) == int(req.resolution)
        and int(controls.get("groove_preserve", -1)) == int(req.groove_preserve)
    )


def _record_recommendation_rerun(job_id: str, req: QuantizeRequest) -> dict[str, object] | None:
    selection_path = OUTPUTS_DIR / job_id / "recommendation_selection.json"
    selection_store = read_json(selection_path, default={"job_id": job_id, "entries": []})
    entries = list(selection_store.get("entries") or [])
    pending_index = next(
        (
            idx
            for idx in range(len(entries) - 1, -1, -1)
            if not entries[idx].get("rerun_recorded")
        ),
        None,
    )
    if pending_index is None:
        return None

    entry = dict(entries[pending_index])
    entry["rerun_recorded"] = True
    entry["rerun_request"] = {
        "mode": str(req.mode),
        "target_bpm": float(req.target_bpm),
        "resolution": int(req.resolution),
        "groove_preserve": int(req.groove_preserve),
    }
    entry["rerun_matched_suggestion"] = _matches_recommendation_controls(req, entry.get("suggested_controls"))
    entries[pending_index] = entry
    selection_store["entries"] = entries
    write_json(selection_path, selection_store)

    memory = read_json(_recommendation_memory_path, default={})
    updated_memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name=str(entry.get("style_profile", "balanced")),
        recommendation_kind=str(entry.get("recommendation_kind", "")),
        matched_suggestion=bool(entry.get("rerun_matched_suggestion", False)),
        used_control_bias=bool(dict(entry.get("control_bias") or {}).get("applied", False)),
    )
    write_json(_recommendation_memory_path, updated_memory)
    return {
        "entry": entry,
        "recommendation_memory": updated_memory,
    }


def _finalize_rerun_report(job_id: str, rerun_record: dict[str, object] | None) -> None:
    if rerun_record is None:
        return
    report_path = OUTPUTS_DIR / job_id / "report.json"
    report = read_json(report_path, default={})
    feedback_loop = dict(report.get("feedback_loop") or {})
    style_profile_name = str((report.get("style_adaptation") or {}).get("profile", "balanced"))
    feedback_loop["recommendation_behavior"] = summarize_recommendation_memory(
        dict(rerun_record.get("recommendation_memory") or {}),
        style_profile_name=style_profile_name,
    )
    feedback_loop["recommendation_rank"] = rank_next_pass_recommendations(
        feedback_loop.get("learned_default"),
        dict(feedback_loop.get("learned_behavior") or {}),
        feedback_loop.get("best_next_pass"),
        dict(feedback_loop.get("recommendation_behavior") or {}),
    )
    feedback_loop["latest_recommendation_rerun"] = {
        "recommendation_kind": str(dict(rerun_record.get("entry") or {}).get("recommendation_kind", "")),
        "label": str(dict(rerun_record.get("entry") or {}).get("label", "")),
        "matched_suggestion": bool(dict(rerun_record.get("entry") or {}).get("rerun_matched_suggestion", False)),
        "used_control_bias": bool(dict(dict(rerun_record.get("entry") or {}).get("control_bias") or {}).get("applied", False)),
    }
    report["feedback_loop"] = feedback_loop
    write_json(report_path, report)


def _run_quantize_request(job_id: str, req: QuantizeRequest) -> dict[str, object]:
    global _active_quantize_job
    try:
        output_files = _compute_pipeline(job_id, req)
        rerun_record = _record_recommendation_rerun(job_id, req)
        _finalize_rerun_report(job_id, rerun_record)
        return {"job_id": job_id, "status": "completed", "output_files": output_files}
    except FileNotFoundError as exc:
        _update_job(job_id, "failed", "error", 1.0, str(exc))
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        _update_job(job_id, "failed", "error", 1.0, str(exc), {"trace": traceback.format_exc()})
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        with _quantize_lock:
            if _active_quantize_job == job_id:
                _active_quantize_job = None


def _normalize_resolution(resolution: int) -> int:
    if resolution in (4, 8, 16):
        return resolution
    if resolution <= 4:
        return 4
    if resolution <= 8:
        return 8
    return 16


def _update_job(job_id: str, status: str, stage: str, progress: float, message: str, extra: dict | None = None) -> None:
    state = {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "progress": progress,
        "message": message,
    }
    if extra:
        state.update(extra)
    job_manager.set(job_id, **{k: v for k, v in state.items() if k != "job_id"})

    upload_job = UPLOADS_DIR / job_id / "job.json"
    output_job = OUTPUTS_DIR / job_id / "job.json"
    write_json(upload_job, state)
    write_json(output_job, state)


def _blend_with_groove(source_times: np.ndarray, target_times: np.ndarray, groove_preserve: int) -> np.ndarray:
    strength = np.clip(1.0 - (groove_preserve / 100.0), 0.0, 1.0)
    blended = source_times + strength * (target_times - source_times)
    blended = np.maximum.accumulate(blended)
    blended[0] = 0.0
    blended[-1] = target_times[-1]
    return blended


def _groove_preserve_candidates(
    groove_preserve: int,
    *,
    allow_safer: bool = True,
    max_preserve: int = 100,
    min_preserve: int = 0,
) -> list[int]:
    requested = int(np.clip(groove_preserve, 0, 100))
    preserve_cap = int(np.clip(max_preserve, 0, 100))
    preserve_floor = int(np.clip(min_preserve, 0, preserve_cap))
    seeds = [0, 5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 90, 100, requested]
    if allow_safer:
        return sorted({value for value in seeds if preserve_floor <= value <= preserve_cap})
    return sorted({value for value in seeds if preserve_floor <= value <= min(requested, preserve_cap)})


def _learned_groove_request(requested_groove_preserve: int, learned_behavior: dict[str, object]) -> int:
    requested = int(np.clip(requested_groove_preserve, 0, 100))
    confidence = float(np.clip(learned_behavior.get("confidence", 0.0), 0.0, 1.0))
    evidence_count = int(learned_behavior.get("evidence_count", 0))
    if evidence_count < 2 or confidence < 0.55:
        return requested
    return int(np.clip(learned_behavior.get("guided_groove_preserve", requested), 0, 100))


def _segment_regression_summary(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]] | None,
) -> dict[str, float]:
    summary = {
        "segment_count": 0.0,
        "moderate_regressions": 0.0,
        "severe_regressions": 0.0,
        "catastrophic_regressions": 0.0,
        "worst_delta_sec": 0.0,
        "worst_ratio": 1.0,
    }
    for segment in list(segments or []):
        metrics = _segment_timing_metrics(
            source_times,
            target_times,
            grid,
            onsets_before,
            float(segment["start_sec"]),
            float(segment["end_sec"]),
        )
        if metrics is None:
            continue
        summary["segment_count"] += 1.0
        before = float(metrics["avg_abs_error_before_sec"])
        after = float(metrics["avg_abs_error_after_sec"])
        delta = max(0.0, after - before)
        ratio = after / max(before, 1e-6)
        summary["worst_delta_sec"] = max(float(summary["worst_delta_sec"]), delta)
        summary["worst_ratio"] = max(float(summary["worst_ratio"]), ratio)
        if delta > 0.35 and ratio > 3.0:
            summary["catastrophic_regressions"] += 1.0
        elif delta > 0.15 and ratio > 2.0:
            summary["severe_regressions"] += 1.0
        elif delta > 0.06 and ratio > 1.35:
            summary["moderate_regressions"] += 1.0
    return summary


def _phase_lock_key(metrics: dict | None) -> tuple[float, float, float]:
    if not metrics:
        return (0.0, 0.0, 0.0)
    return (
        round(abs(float(metrics.get("median_signed_error_after_sec", 0.0))), 6),
        round(float(metrics.get("phase_window_abs_max_after_sec", 0.0)), 6),
        round(float(metrics.get("phase_window_span_after_sec", 0.0)), 6),
    )


def _optimize_groove_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    groove_preserve: int,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]] | None = None,
    *,
    allow_safer: bool = True,
    max_preserve: int = 100,
    min_preserve: int = 0,
) -> tuple[np.ndarray, int, dict]:
    best_target: np.ndarray | None = None
    best_metrics: dict | None = None
    best_preserve = int(np.clip(groove_preserve, 0, 100))
    best_guard_summary: dict[str, float] | None = None

    for candidate_preserve in _groove_preserve_candidates(
        groove_preserve,
        allow_safer=allow_safer,
        max_preserve=max_preserve,
        min_preserve=min_preserve,
    ):
        candidate_target = _blend_with_groove(source_times, target_times, candidate_preserve).astype(np.float32)
        candidate_metrics = _candidate_metrics_fast(source_times, candidate_target, grid, onsets_before)
        candidate_guard_summary = _segment_regression_summary(
            source_times,
            candidate_target,
            grid,
            onsets_before,
            segments,
        )
        if best_metrics is None:
            best_target = candidate_target
            best_metrics = candidate_metrics
            best_preserve = candidate_preserve
            best_guard_summary = candidate_guard_summary
            continue
        best_guard_summary = best_guard_summary or {
            "catastrophic_regressions": 0.0,
            "severe_regressions": 0.0,
            "moderate_regressions": 0.0,
            "worst_delta_sec": 0.0,
            "worst_ratio": 1.0,
        }
        candidate_guard_key = (
            int(candidate_guard_summary["catastrophic_regressions"]),
            int(candidate_guard_summary["severe_regressions"]),
            int(candidate_guard_summary["moderate_regressions"]),
            round(float(candidate_guard_summary["worst_delta_sec"]), 6),
            round(float(candidate_guard_summary["worst_ratio"]), 6),
        )
        best_guard_key = (
            int(best_guard_summary["catastrophic_regressions"]),
            int(best_guard_summary["severe_regressions"]),
            int(best_guard_summary["moderate_regressions"]),
            round(float(best_guard_summary["worst_delta_sec"]), 6),
            round(float(best_guard_summary["worst_ratio"]), 6),
        )
        candidate_error = float(candidate_metrics["avg_abs_error_after_sec"])
        best_error = float(best_metrics["avg_abs_error_after_sec"])
        candidate_phase_key = _phase_lock_key(candidate_metrics)
        best_phase_key = _phase_lock_key(best_metrics)
        if candidate_guard_key < best_guard_key or (
            candidate_guard_key == best_guard_key and candidate_phase_key < best_phase_key
        ) or (
            candidate_guard_key == best_guard_key
            and candidate_phase_key == best_phase_key
            and candidate_error + 1e-6 < best_error
        ) or (
            candidate_guard_key == best_guard_key
            and candidate_phase_key == best_phase_key
            and abs(candidate_error - best_error) <= 1e-6
            and candidate_preserve > best_preserve
        ):
            best_target = candidate_target
            best_metrics = candidate_metrics
            best_preserve = candidate_preserve
            best_guard_summary = candidate_guard_summary

    if best_target is None or best_metrics is None:
        raise RuntimeError("No groove-preserve candidate generated")
    return best_target, best_preserve, best_metrics


def _style_guided_groove_request(requested_groove_preserve: int, style_profile: dict[str, object]) -> int:
    requested = int(np.clip(requested_groove_preserve, 0, 100))
    recommended = int(np.clip(style_profile.get("recommended_groove_preserve", requested), 0, 100))
    confidence = float(np.clip(style_profile.get("confidence", 0.0), 0.0, 1.0))
    if confidence < 0.55:
        return requested
    blended = int(round((0.7 * requested) + (0.3 * recommended)))
    lower = max(0, requested - 20)
    upper = min(100, requested + 20)
    return int(np.clip(blended, lower, upper))


def _strict_lock_groove_request(
    requested_groove_preserve: int,
    guided_groove_preserve: int,
    *,
    duration_sec: float,
    event_summary: dict[str, object],
) -> int:
    requested = int(np.clip(requested_groove_preserve, 0, 100))
    guided = int(np.clip(guided_groove_preserve, 0, 100))
    event_density = float(event_summary.get("event_density_per_sec", 0.0))
    strong_event_count = int(event_summary.get("strong_event_count", 0))
    if duration_sec < 45.0 or event_density < 0.45 or strong_event_count < 48:
        return guided
    return 20


def _should_prefer_segmented_hybrid(style_profile: dict[str, object]) -> bool:
    return bool(style_profile.get("prefer_segmented_hybrid")) and float(style_profile.get("confidence", 0.0)) >= 0.55


def _should_skip_post_selection_repairs(duration_sec: float, metronome_lock: dict[str, object]) -> bool:
    if duration_sec < 45.0:
        return False
    continuity = dict(metronome_lock.get("warp_continuity") or {})
    if str(continuity.get("verdict", "")) == "jump_risk":
        return False
    if int(metronome_lock.get("meltdown_segments", 0)) > 0:
        return False
    if int(metronome_lock.get("unstable_segments", 0)) > 0:
        return False
    if str(metronome_lock.get("beat_phase_verdict", "")) == "risk":
        return False
    beat_phase = dict(metronome_lock.get("beat_phase") or {})
    if int(beat_phase.get("beat_count", 0) or 0) >= 4 and not (
        bool(beat_phase.get("locked", True))
        and bool(beat_phase.get("intro_locked", True))
        or bool(beat_phase.get("offbeat_alias", False))
    ):
        return False
    locked_ratio = float(metronome_lock.get("locked_ratio", 1.0))
    fixed = dict(metronome_lock.get("fixed_windows") or {})
    fixed_locked_ratio = float(fixed.get("effective_locked_ratio", fixed.get("locked_ratio", 1.0)))
    return (
        str(metronome_lock.get("verdict", "")) in {"mostly_locked", "daw_locked"}
        and locked_ratio >= 0.95
        and fixed_locked_ratio >= 0.95
    )


def _blend_ml_with_baseline(
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    ml_curve: dict,
    grid_times: np.ndarray,
    blend_cap: float = 0.08,
) -> tuple[np.ndarray, float, dict]:
    ml_target_interp = np.interp(source_times, ml_curve["source_times"], ml_curve["target_times"])
    ml_target_interp = np.maximum.accumulate(ml_target_interp)
    confidence = float(np.clip(ml_curve.get("confidence", 0.0), 0.0, 1.0))
    grid_step = float(np.median(np.diff(grid_times))) if len(grid_times) > 1 else 0.25
    displacement = np.abs(ml_target_interp - baseline_target)
    p95 = float(np.percentile(displacement, 95)) if len(displacement) else 0.0
    disagreement_ratio = p95 / max(grid_step, 1e-6)
    agreement_scale = float(np.clip(1.0 - max(0.0, disagreement_ratio - 0.35) / 1.15, 0.0, 1.0))
    alpha = min(blend_cap, 0.02 + 0.06 * confidence) * agreement_scale
    blended = (1.0 - alpha) * baseline_target + alpha * ml_target_interp
    blended = np.maximum.accumulate(blended)
    blended[-1] = baseline_target[-1]
    diagnostics = {
        "grid_step_sec": grid_step,
        "ml_baseline_p95_diff_sec": p95,
        "ml_baseline_disagreement_ratio": disagreement_ratio,
        "ml_agreement_scale": agreement_scale,
    }
    return blended.astype(np.float32), alpha, diagnostics


def _hybrid_alpha_candidates(alpha: float) -> list[float]:
    seeds = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, alpha, min(0.08, alpha * 1.5)]
    values = sorted({float(np.clip(item, 0.0, 0.08)) for item in seeds if item > 1e-6})
    return values


def _select_hybrid_candidate(
    baseline_metrics: dict,
    hybrid_metrics: dict,
    alpha: float,
) -> tuple[str, dict, float]:
    baseline_error = float(baseline_metrics["avg_abs_error_after_sec"])
    hybrid_error = float(hybrid_metrics["avg_abs_error_after_sec"])
    baseline_phase_key = _phase_lock_key(baseline_metrics)
    hybrid_phase_key = _phase_lock_key(hybrid_metrics)
    if alpha <= 1e-6:
        return "baseline_guarded", baseline_metrics, 0.0
    if hybrid_phase_key < baseline_phase_key or (
        hybrid_phase_key == baseline_phase_key and hybrid_error <= baseline_error
    ):
        return "hybrid_candidate", hybrid_metrics, alpha
    return "baseline", baseline_metrics, 0.0


def _candidate_metrics(audio: np.ndarray, sr: int, source_times: np.ndarray, target_times: np.ndarray, grid: np.ndarray, onsets_before: np.ndarray) -> tuple[np.ndarray, str, dict]:
    warped_audio, warp_method = apply_warp(audio, sr, source_times, target_times)
    warped_mono = warped_audio.mean(axis=1) if warped_audio.shape[1] > 1 else warped_audio[:, 0]
    onsets_after = detect_onsets(warped_mono, sr, units="time")
    metrics = timing_metrics(onsets_before, onsets_after, grid)
    return warped_audio, warp_method, metrics


def _selection_proxy(audio: np.ndarray, sr: int, target_sr: int = 16000) -> tuple[np.ndarray, int, np.ndarray]:
    mono = audio.mean(axis=1).astype(np.float32) if audio.shape[1] > 1 else audio[:, 0].astype(np.float32)
    proxy_sr = int(min(sr, target_sr))
    if proxy_sr != sr:
        mono = librosa.resample(mono, orig_sr=sr, target_sr=proxy_sr).astype(np.float32)
    return mono[:, None], proxy_sr, detect_onsets(mono, proxy_sr, units="time")


def _candidate_metrics_proxy(
    proxy_audio: np.ndarray,
    proxy_sr: int,
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before_proxy: np.ndarray,
) -> dict:
    warped_audio, _ = apply_warp(proxy_audio, proxy_sr, source_times, target_times)
    warped_mono = warped_audio[:, 0]
    onsets_after = detect_onsets(warped_mono, proxy_sr, units="time")
    return timing_metrics(onsets_before_proxy, onsets_after, grid)


def _project_onsets(source_times: np.ndarray, target_times: np.ndarray, onsets_before: np.ndarray) -> np.ndarray:
    if len(onsets_before) == 0:
        return np.array([], dtype=np.float32)
    projected = np.interp(onsets_before, source_times, target_times).astype(np.float32)
    return np.maximum.accumulate(projected)


def _candidate_metrics_fast(source_times: np.ndarray, target_times: np.ndarray, grid: np.ndarray, onsets_before: np.ndarray) -> dict:
    return timing_metrics(onsets_before, _project_onsets(source_times, target_times, onsets_before), grid)


def _segment_timing_metrics(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    start_sec: float,
    end_sec: float,
) -> dict | None:
    if end_sec - start_sec <= 1e-3:
        return None
    onset_mask = (onsets_before >= start_sec) & (onsets_before < end_sec)
    if int(np.count_nonzero(onset_mask)) < 2:
        return None
    local_grid = grid[(grid >= start_sec) & (grid <= end_sec)]
    if len(local_grid) < 2:
        return None
    local_before = onsets_before[onset_mask]
    local_after = _project_onsets(source_times, target_times, local_before)
    return timing_metrics(local_before, local_after, local_grid)


def _segment_blend_weights(source_times: np.ndarray, start_sec: float, end_sec: float, transition_sec: float = 0.18) -> np.ndarray:
    weights = np.zeros_like(source_times, dtype=np.float32)
    mask = (source_times >= start_sec) & (source_times <= end_sec)
    if not np.any(mask):
        return weights
    weights[mask] = 1.0
    if transition_sec <= 1e-4:
        return weights

    seg_len = end_sec - start_sec
    local_transition = min(float(transition_sec), max(seg_len * 0.35, 1e-3))
    idx = np.flatnonzero(mask)
    local_times = source_times[idx]
    fade_in = np.clip((local_times - start_sec) / local_transition, 0.0, 1.0)
    fade_out = np.clip((end_sec - local_times) / local_transition, 0.0, 1.0)
    weights[idx] = np.minimum(weights[idx], np.minimum(fade_in, fade_out).astype(np.float32))
    peak = float(weights[idx].max()) if len(idx) else 0.0
    if peak > 1e-6:
        weights[idx] = np.clip(weights[idx] / peak, 0.0, 1.0)
    return weights


def _nearest_grid_index(grid: np.ndarray, time_sec: float) -> int:
    if len(grid) == 0:
        return 0
    idx = int(np.searchsorted(grid, time_sec))
    if 0 < idx < len(grid):
        prev_time = float(grid[idx - 1])
        next_time = float(grid[idx])
        if abs(time_sec - prev_time) <= abs(next_time - time_sec):
            idx -= 1
    return int(np.clip(idx, 0, len(grid) - 1))


def _reanchor_cluster_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    start_sec: float,
    end_sec: float,
) -> np.ndarray | None:
    if end_sec - start_sec <= 1e-3:
        return None

    local_grid = grid[(grid >= start_sec) & (grid <= end_sec)]
    local_onsets = onsets_before[(onsets_before >= start_sec) & (onsets_before <= end_sec)]
    if len(local_grid) < 4 or len(local_onsets) < 4:
        return None

    start_target = float(np.interp(start_sec, source_times, target_times))
    end_target = float(np.interp(end_sec, source_times, target_times))
    start_idx = _nearest_grid_index(local_grid, start_target)
    end_idx = _nearest_grid_index(local_grid, end_target)
    if end_idx <= start_idx:
        end_idx = min(len(local_grid) - 1, start_idx + max(2, len(local_onsets) // 2))
    if end_idx <= start_idx:
        return None

    source_anchors = [start_sec]
    target_anchors = [float(local_grid[start_idx])]
    last_idx = start_idx
    max_idx = max(start_idx + 1, end_idx - 1)

    for onset_time in local_onsets:
        grid_idx = _nearest_grid_index(local_grid, float(onset_time))
        if float(onset_time) > source_anchors[-1]:
            grid_idx = max(grid_idx, last_idx + 1)
        else:
            grid_idx = max(grid_idx, last_idx)
        grid_idx = min(grid_idx, max_idx)
        target_time = float(local_grid[grid_idx])
        if target_time <= target_anchors[-1] + 1e-3:
            continue
        if float(onset_time) <= source_anchors[-1] + 0.05:
            continue
        source_anchors.append(float(onset_time))
        target_anchors.append(target_time)
        last_idx = grid_idx

    end_target_grid = float(local_grid[end_idx])
    if end_target_grid <= target_anchors[-1]:
        end_target_grid = min(float(local_grid[-1]), target_anchors[-1] + max((end_sec - start_sec) * 0.2, 1e-3))
    if end_target_grid <= target_anchors[-1]:
        return None

    source_anchors.append(end_sec)
    target_anchors.append(end_target_grid)

    src = np.asarray(source_anchors, dtype=np.float32)
    tgt = np.asarray(target_anchors, dtype=np.float32)
    if len(src) < 3:
        return None

    repaired = target_times.astype(np.float32, copy=True)
    cluster_mask = (source_times >= start_sec) & (source_times <= end_sec)
    if not np.any(cluster_mask):
        return None
    repaired[cluster_mask] = np.interp(source_times[cluster_mask], src, tgt).astype(np.float32)
    repaired = np.maximum.accumulate(repaired)
    repaired[-1] = target_times[-1]
    return repaired


def _summarize_segments(
    segments: list[dict[str, float]],
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    final_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for index, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        final_metrics = _segment_timing_metrics(
            source_times,
            final_target,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if final_metrics is None:
            continue
        baseline_metrics = _segment_timing_metrics(
            source_times,
            baseline_target,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        baseline_after = float(baseline_metrics["avg_abs_error_after_sec"]) if baseline_metrics else float(
            final_metrics["avg_abs_error_after_sec"]
        )
        summaries.append(
            {
                "segment_index": index,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "position_bucket": classify_segment_bucket(start_sec, end_sec, float(source_times[-1]) if len(source_times) else end_sec),
                "duration_sec": max(0.0, end_sec - start_sec),
                "timing_metrics": final_metrics,
                "baseline_error_after_sec": baseline_after,
                "delta_vs_baseline_sec": float(final_metrics["avg_abs_error_after_sec"]) - baseline_after,
            }
        )
    return summaries


def _summarize_metronome_lock(
    timing_metrics: dict[str, float],
    segment_summaries: list[dict[str, object]],
) -> dict[str, object]:
    total_segments = len(segment_summaries)
    if total_segments == 0:
        return {
            "score": 0.0,
            "verdict": "unknown",
            "locked_ratio": 0.0,
            "strong_locked_ratio": 0.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "first_unstable_sec": None,
            "first_meltdown_sec": None,
        }

    locked = 0
    strong_locked = 0
    eligible_locked = 0
    eligible_strong_locked = 0
    eligible_total = 0
    excluded = 0
    unstable = 0
    meltdown = 0
    first_unstable_sec: float | None = None
    first_meltdown_sec: float | None = None

    for segment in segment_summaries:
        metrics = dict(segment.get("timing_metrics") or {})
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs_max = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        event_count = int(metrics.get("num_events_after", 0))
        start_sec = float(segment.get("start_sec", 0.0))

        absolute_lock = after_error <= 0.05 and phase_abs_max <= 0.065 and phase_span <= 0.065
        phase_coherent_near_lock = (
            after_error <= 0.065
            and phase_abs_max <= 0.07
            and phase_span <= 0.09
            and improvement_pct >= -15.0
        ) or (
            after_error <= 0.07
            and phase_abs_max <= 0.04
            and phase_span <= 0.04
            and improvement_pct >= 0.0
        )
        segment_locked = False
        segment_strong = False
        if after_error <= 0.0615 and (improvement_pct >= 0.0 or absolute_lock):
            locked += 1
            segment_locked = True
        elif phase_coherent_near_lock:
            locked += 1
            segment_locked = True
        if after_error <= 0.05 and phase_abs_max <= 0.04 and phase_span <= 0.06 and (
            improvement_pct >= 5.0 or after_error <= 0.04
        ):
            strong_locked += 1
            segment_strong = True
        sparse_low_evidence = (
            event_count <= 4
            and after_error <= 0.07
            and phase_abs_max <= 0.015
            and phase_span <= 0.015
        )
        if sparse_low_evidence:
            excluded += 1
        else:
            eligible_total += 1
            eligible_locked += int(segment_locked)
            eligible_strong_locked += int(segment_strong)
        is_unstable = after_error >= 0.09 and (improvement_pct < 5.0 or phase_abs_max >= 0.05 or phase_span >= 0.08)
        is_meltdown = after_error >= 0.1 and (improvement_pct < 0.0 or phase_abs_max >= 0.08 or phase_span >= 0.1)
        if is_unstable:
            unstable += 1
            if first_unstable_sec is None:
                first_unstable_sec = start_sec
        if is_meltdown:
            meltdown += 1
            if first_meltdown_sec is None:
                first_meltdown_sec = start_sec

    locked_ratio = locked / total_segments
    strong_locked_ratio = strong_locked / total_segments
    overall_phase_abs = float(timing_metrics.get("phase_window_abs_max_after_sec", 0.0))
    overall_phase_span = float(timing_metrics.get("phase_window_span_after_sec", 0.0))
    score = 100.0
    score -= (1.0 - locked_ratio) * 35.0
    score -= (1.0 - strong_locked_ratio) * 20.0
    score -= unstable * 2.5
    score -= meltdown * 5.0
    score -= min(overall_phase_abs / 0.04, 2.0) * 8.0
    score -= min(overall_phase_span / 0.08, 2.0) * 8.0
    score = float(np.clip(score, 0.0, 100.0))

    verdict = "unstable"
    if meltdown == 0 and locked_ratio >= 0.8 and overall_phase_abs <= 0.04 and overall_phase_span <= 0.08:
        verdict = "daw_locked"
    elif meltdown <= 1 and locked_ratio >= 0.65:
        verdict = "mostly_locked"
    elif first_meltdown_sec is not None and first_meltdown_sec >= 60.0:
        verdict = "drifts_late"
    elif first_unstable_sec is not None and first_unstable_sec >= 45.0:
        verdict = "drifts_mid_song"

    return {
        "score": score,
        "verdict": verdict,
        "locked_ratio": round(float(locked_ratio), 4),
        "strong_locked_ratio": round(float(strong_locked_ratio), 4),
        "effective_locked_ratio": round(float(eligible_locked / max(eligible_total, 1)), 4),
        "effective_strong_locked_ratio": round(float(eligible_strong_locked / max(eligible_total, 1)), 4),
        "eligible_segment_count": int(eligible_total),
        "excluded_segments": int(excluded),
        "unstable_segments": int(unstable),
        "meltdown_segments": int(meltdown),
        "first_unstable_sec": first_unstable_sec,
        "first_meltdown_sec": first_meltdown_sec,
        "overall_phase_window_abs_max_after_sec": overall_phase_abs,
        "overall_phase_window_span_after_sec": overall_phase_span,
    }


def _summarize_fixed_window_lock(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    duration_sec: float,
    target_bpm: float,
) -> dict[str, object]:
    if duration_sec <= 0.0:
        return {"locked_ratio": 0.0, "unstable_windows": 0, "meltdown_windows": 0, "window_count": 0}

    window_sec = float(np.clip((60.0 / max(target_bpm, 1e-6)) * 16.0, 6.0, 14.0))
    starts = np.arange(0.0, max(duration_sec - 1e-6, 0.0), window_sec, dtype=np.float32)
    locked = 0
    strong_locked = 0
    eligible_locked = 0
    eligible_strong_locked = 0
    eligible_total = 0
    excluded = 0
    unstable = 0
    meltdown = 0
    summaries: list[dict[str, object]] = []

    for start in starts:
        start_sec = float(start)
        end_sec = min(float(duration_sec), start_sec + window_sec)
        if end_sec - start_sec < max(2.0, window_sec * 0.35):
            continue
        metrics = _segment_timing_metrics(
            source_times,
            target_times,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        event_count = int(metrics.get("num_events_after", 0))
        sparse_tail = (
            duration_sec - end_sec <= window_sec * 0.35
            and event_count <= 8
            and after_error <= 0.085
            and phase_abs <= 0.02
            and phase_span <= 0.02
        )
        absolute_lock = after_error <= 0.05 and phase_abs <= 0.04 and phase_span <= 0.06
        is_locked = after_error <= 0.061 and (improvement_pct >= 0.0 or absolute_lock)
        is_strong = after_error <= 0.05 and phase_abs <= 0.04 and phase_span <= 0.06 and (
            improvement_pct >= 5.0 or after_error <= 0.04
        )
        is_unstable = after_error >= 0.09 and (improvement_pct < 5.0 or phase_abs >= 0.05 or phase_span >= 0.08)
        is_meltdown = after_error >= 0.1 and (improvement_pct < 0.0 or phase_abs >= 0.08 or phase_span >= 0.1)
        locked += int(is_locked)
        strong_locked += int(is_strong)
        if sparse_tail:
            excluded += 1
        else:
            eligible_total += 1
            eligible_locked += int(is_locked)
            eligible_strong_locked += int(is_strong)
        unstable += int(is_unstable)
        meltdown += int(is_meltdown)
        summaries.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "avg_abs_error_after_sec": after_error,
                "improvement_pct": improvement_pct,
                "num_events_after": event_count,
                "phase_window_abs_max_after_sec": phase_abs,
                "phase_window_span_after_sec": phase_span,
                "locked": bool(is_locked),
                "strong_locked": bool(is_strong),
                "excluded_from_effective_ratio": bool(sparse_tail),
                "exclusion_reason": "sparse_tail" if sparse_tail else None,
            }
        )

    total = len(summaries)
    if total == 0:
        return {"locked_ratio": 0.0, "strong_locked_ratio": 0.0, "unstable_windows": 0, "meltdown_windows": 0, "window_count": 0}

    return {
        "locked_ratio": round(float(locked / total), 4),
        "strong_locked_ratio": round(float(strong_locked / total), 4),
        "effective_locked_ratio": round(float(eligible_locked / max(eligible_total, 1)), 4),
        "effective_strong_locked_ratio": round(float(eligible_strong_locked / max(eligible_total, 1)), 4),
        "unstable_windows": int(unstable),
        "meltdown_windows": int(meltdown),
        "window_count": int(total),
        "eligible_window_count": int(eligible_total),
        "excluded_windows": int(excluded),
        "window_sec": float(window_sec),
        "windows": summaries[:64],
    }


def _nearest_signed_grid_errors(event_times: np.ndarray, grid_times: np.ndarray) -> np.ndarray:
    if len(event_times) == 0 or len(grid_times) == 0:
        return np.array([], dtype=np.float32)
    idx = np.searchsorted(grid_times, event_times)
    idx = np.clip(idx, 0, len(grid_times) - 1)
    prev_idx = np.clip(idx - 1, 0, len(grid_times) - 1)
    err_next = event_times - grid_times[idx]
    err_prev = event_times - grid_times[prev_idx]
    choose_prev = np.abs(err_prev) <= np.abs(err_next)
    return np.where(choose_prev, err_prev, err_next).astype(np.float32)


def _summarize_beat_phase_lock(
    source_times: np.ndarray,
    target_times: np.ndarray,
    beat_times: np.ndarray,
    *,
    target_bpm: float,
    duration_sec: float,
) -> dict[str, object]:
    beat_times = np.asarray(beat_times, dtype=np.float32)
    valid_beats = beat_times[np.isfinite(beat_times) & (beat_times >= 0.0) & (beat_times <= duration_sec)]
    beat_sec = 60.0 / max(float(target_bpm), 1e-6)
    quarter_grid = np.arange(0.0, duration_sec + beat_sec, beat_sec, dtype=np.float32)
    if len(valid_beats) < 4 or len(source_times) < 2 or len(target_times) < 2 or len(quarter_grid) < 2:
        return {
            "beat_count": int(len(valid_beats)),
            "avg_abs_error_after_sec": 0.0,
            "p90_abs_error_after_sec": 0.0,
            "intro_avg_abs_error_after_sec": 0.0,
            "median_signed_error_after_sec": 0.0,
            "locked": True,
            "intro_locked": True,
        }

    projected_beats = np.interp(valid_beats, source_times, target_times).astype(np.float32)
    errors = _nearest_signed_grid_errors(projected_beats, quarter_grid)
    abs_errors = np.abs(errors)
    intro_mask = valid_beats <= min(30.0, duration_sec)
    intro_abs = abs_errors[intro_mask] if int(np.count_nonzero(intro_mask)) >= 3 else abs_errors[: min(len(abs_errors), 8)]
    avg_abs = float(np.mean(abs_errors)) if len(abs_errors) else 0.0
    p90_abs = float(np.percentile(abs_errors, 90)) if len(abs_errors) else 0.0
    intro_avg_abs = float(np.mean(intro_abs)) if len(intro_abs) else 0.0
    median_signed = float(np.median(errors)) if len(errors) else 0.0
    beat_sec = 60.0 / max(float(target_bpm), 1e-6)
    half_beat = beat_sec * 0.5
    offbeat_alias = abs(abs(median_signed) - half_beat) <= max(0.025, beat_sec * 0.06)
    return {
        "beat_count": int(len(valid_beats)),
        "avg_abs_error_after_sec": avg_abs,
        "p90_abs_error_after_sec": p90_abs,
        "intro_avg_abs_error_after_sec": intro_avg_abs,
        "median_signed_error_after_sec": median_signed,
        "offbeat_alias": bool(offbeat_alias),
        "locked": bool(avg_abs <= 0.045 and p90_abs <= 0.075),
        "intro_locked": bool(intro_avg_abs <= 0.045),
    }


def _apply_beat_phase_gate(metronome_lock: dict[str, object], beat_phase_lock: dict[str, object]) -> dict[str, object]:
    gated = dict(metronome_lock)
    gated["beat_phase"] = beat_phase_lock
    if int(beat_phase_lock.get("beat_count", 0)) < 4:
        return gated
    if bool(beat_phase_lock.get("locked", True)) and bool(beat_phase_lock.get("intro_locked", True)):
        return gated
    if bool(beat_phase_lock.get("offbeat_alias", False)):
        gated["beat_phase_verdict"] = "offbeat_alias"
        gated["beat_phase_note"] = "beat tracker appears to be following an eighth-note offbeat; fixed-window lock remains authoritative"
        return gated
    fixed = dict(gated.get("fixed_windows") or {})
    fixed_locked_ratio = float(fixed.get("effective_locked_ratio", fixed.get("locked_ratio", 0.0)))
    segment_locked_ratio = float(gated.get("effective_locked_ratio", gated.get("locked_ratio", 0.0)))
    phase_abs = float(gated.get("overall_phase_window_abs_max_after_sec", 999.0))
    phase_span = float(gated.get("overall_phase_window_span_after_sec", 999.0))
    grid_authoritative = (
        str(gated.get("verdict")) in {"daw_locked", "mostly_locked"}
        and (
            segment_locked_ratio >= 0.94
            or (
                segment_locked_ratio >= 0.70
                and phase_abs <= 0.015
                and phase_span <= 0.025
                and int(fixed.get("eligible_window_count", fixed.get("window_count", 0)) or 0) >= 8
                and float(fixed.get("effective_strong_locked_ratio", fixed.get("strong_locked_ratio", 0.0)) or 0.0) >= 0.65
            )
        )
        and fixed_locked_ratio >= 0.999
        and int(gated.get("unstable_segments", 0)) == 0
        and int(gated.get("meltdown_segments", 0)) == 0
        and int(fixed.get("unstable_windows", 0)) == 0
        and int(fixed.get("meltdown_windows", 0)) == 0
        and phase_abs <= 0.04
        and phase_span <= 0.08
    )
    if grid_authoritative:
        beat_phase_with_context = dict(beat_phase_lock)
        beat_phase_with_context["grid_authoritative"] = True
        gated["beat_phase"] = beat_phase_with_context
        gated["beat_phase_verdict"] = "grid_authoritative"
        gated["beat_phase_note"] = "fixed-window and segment locks are perfect; beat tracker is treated as ambiguous"
        return gated
    gated["beat_phase_verdict"] = "risk"
    if str(gated.get("verdict")) == "daw_locked":
        gated["verdict"] = "mostly_locked"
    return gated


def _beat_phase_shift_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    beat_times: np.ndarray,
    duration_sec: float,
    target_bpm: float,
) -> tuple[np.ndarray, dict[str, object] | None]:
    before = _summarize_beat_phase_lock(
        source_times,
        target_times,
        beat_times,
        target_bpm=target_bpm,
        duration_sec=duration_sec,
    )
    if bool(before.get("locked", True)) and bool(before.get("intro_locked", True)):
        return target_times, None
    if int(before.get("beat_count", 0)) < 8 or len(grid) < 2:
        return target_times, None

    grid_step = float(np.median(np.diff(grid)))
    median_signed = float(before.get("median_signed_error_after_sec", 0.0))
    if not np.isfinite(median_signed) or abs(median_signed) < max(0.04, grid_step * 0.45):
        return target_times, None
    shift_steps = int(np.round(median_signed / max(grid_step, 1e-6)))
    if shift_steps == 0 or abs(shift_steps) > 2:
        return target_times, None
    shift_sec = float(shift_steps * grid_step)
    candidate = np.maximum(target_times.astype(np.float32, copy=True) - shift_sec, 0.0)
    candidate = np.maximum.accumulate(candidate).astype(np.float32)

    after = _summarize_beat_phase_lock(
        source_times,
        candidate,
        beat_times,
        target_bpm=target_bpm,
        duration_sec=duration_sec,
    )
    if not (bool(after.get("locked", False)) and bool(after.get("intro_locked", False))):
        return target_times, None
    base_metrics = _candidate_metrics_fast(source_times, target_times, grid, onsets_before)
    candidate_metrics = _candidate_metrics_fast(source_times, candidate, grid, onsets_before)
    if float(candidate_metrics.get("avg_abs_error_after_sec", 1.0)) > float(base_metrics.get("avg_abs_error_after_sec", 1.0)) + 0.006:
        return target_times, None
    base_fixed = _summarize_fixed_window_lock(source_times, target_times, grid, onsets_before, duration_sec, target_bpm)
    candidate_fixed = _summarize_fixed_window_lock(source_times, candidate, grid, onsets_before, duration_sec, target_bpm)
    base_fixed_ratio = float(base_fixed.get("effective_locked_ratio", base_fixed.get("locked_ratio", 0.0)))
    candidate_fixed_ratio = float(candidate_fixed.get("effective_locked_ratio", candidate_fixed.get("locked_ratio", 0.0)))
    if candidate_fixed_ratio < base_fixed_ratio - 0.02:
        return target_times, None
    if int(candidate_fixed.get("meltdown_windows", 0)) > int(base_fixed.get("meltdown_windows", 0)):
        return target_times, None
    if int(candidate_fixed.get("unstable_windows", 0)) > int(base_fixed.get("unstable_windows", 0)):
        return target_times, None
    return candidate, {
        "shift_sec": shift_sec,
        "shift_steps": int(shift_steps),
        "before": before,
        "after": after,
        "avg_abs_error_before_shift_sec": float(base_metrics.get("avg_abs_error_after_sec", 0.0)),
        "avg_abs_error_after_shift_sec": float(candidate_metrics.get("avg_abs_error_after_sec", 0.0)),
        "fixed_locked_ratio_before_shift": base_fixed_ratio,
        "fixed_locked_ratio_after_shift": candidate_fixed_ratio,
    }


def _apply_fixed_window_gate(metronome_lock: dict[str, object], fixed_window_lock: dict[str, object]) -> dict[str, object]:
    gated = dict(metronome_lock)
    fixed_locked_ratio = float(fixed_window_lock.get("effective_locked_ratio", fixed_window_lock.get("locked_ratio", 0.0)))
    fixed_meltdowns = int(fixed_window_lock.get("meltdown_windows", 0))
    fixed_unstable = int(fixed_window_lock.get("unstable_windows", 0))
    if str(gated.get("verdict")) == "daw_locked" and (
        fixed_locked_ratio < 0.8 or fixed_meltdowns > 0 or fixed_unstable > 0
    ):
        gated["verdict"] = "mostly_locked" if fixed_meltdowns == 0 and fixed_locked_ratio >= 0.65 else "unstable"
        gated["segment_verdict"] = str(metronome_lock.get("verdict", "unknown"))
    gated["fixed_windows"] = fixed_window_lock
    return gated


def _summarize_warp_continuity(
    source_times: np.ndarray,
    target_times: np.ndarray,
    target_bpm: float,
    resolution: int,
) -> dict[str, object]:
    if len(source_times) < 4 or len(source_times) != len(target_times):
        return {
            "verdict": "unknown",
            "max_window_offset_jump_sec": 0.0,
            "max_local_stretch_delta": 0.0,
            "p99_local_stretch_delta": 0.0,
            "window_count": 0,
        }

    src = np.asarray(source_times, dtype=np.float32)
    tgt = np.asarray(target_times, dtype=np.float32)
    source_delta = np.diff(src)
    target_delta = np.diff(tgt)
    valid = source_delta >= 0.035
    if not np.any(valid):
        return {
            "verdict": "unknown",
            "max_window_offset_jump_sec": 0.0,
            "max_local_stretch_delta": 0.0,
            "p99_local_stretch_delta": 0.0,
            "window_count": 0,
        }

    ratios = target_delta[valid] / np.maximum(source_delta[valid], 1e-6)
    finite = np.isfinite(ratios)
    ratios = ratios[finite]
    if len(ratios) == 0:
        local_max = 0.0
        local_p99 = 0.0
        median_ratio = 1.0
    else:
        median_ratio = float(np.median(ratios))
        ratio_delta = np.abs(ratios - median_ratio)
        local_max = float(np.max(ratio_delta))
        local_p99 = float(np.percentile(ratio_delta, 99.0))

    beat_sec = 60.0 / max(float(target_bpm), 1e-6)
    step_sec = beat_sec * (4.0 / max(float(resolution), 1.0))
    window_sec = float(np.clip(beat_sec * 8.0, 3.0, 8.0))
    offsets = tgt - src
    starts = np.arange(float(src[0]), float(src[-1]) + 1e-6, window_sec, dtype=np.float32)
    offset_medians: list[float] = []
    offset_windows: list[dict[str, float]] = []
    for start in starts:
        end = float(start) + window_sec
        mask = (src >= float(start)) & (src < end)
        if int(np.count_nonzero(mask)) < 3:
            continue
        median_offset = float(np.median(offsets[mask]))
        offset_medians.append(median_offset)
        offset_windows.append(
            {
                "start_sec": float(start),
                "end_sec": float(end),
                "median_offset_sec": median_offset,
            }
        )

    offset_jumps = np.abs(np.diff(np.asarray(offset_medians, dtype=np.float32))) if len(offset_medians) >= 2 else np.array([], dtype=np.float32)
    if len(offset_jumps):
        max_jump_index = int(np.argmax(offset_jumps))
        max_window_offset_jump = float(offset_jumps[max_jump_index])
        max_jump_from = offset_windows[max_jump_index]
        max_jump_to = offset_windows[max_jump_index + 1]
    else:
        max_window_offset_jump = 0.0
        max_jump_from = None
        max_jump_to = None

    nearest_subdivision_steps = int(round(max_window_offset_jump / max(step_sec, 1e-6))) if step_sec > 0.0 else 0
    subdivision_alias_jump = (
        nearest_subdivision_steps >= 1
        and abs(max_window_offset_jump - nearest_subdivision_steps * step_sec) <= max(0.04, step_sec * 0.25)
    )

    verdict = "continuous"
    if max_window_offset_jump >= max(beat_sec * 0.45, step_sec * 0.85) or local_p99 >= 0.35 or (local_max >= 0.75 and local_p99 >= 0.12):
        verdict = "jump_risk"
    elif max_window_offset_jump >= max(beat_sec * 0.25, step_sec * 0.5) or local_p99 >= 0.22 or local_max >= 0.5:
        verdict = "watch"

    return {
        "verdict": verdict,
        "max_window_offset_jump_sec": max_window_offset_jump,
        "max_local_stretch_delta": local_max,
        "p99_local_stretch_delta": local_p99,
        "median_stretch_ratio": median_ratio,
        "window_count": int(len(offset_medians)),
        "window_sec": window_sec,
        "subdivision_alias_jump": bool(subdivision_alias_jump),
        "nearest_subdivision_steps": int(nearest_subdivision_steps),
        "grid_step_sec": float(step_sec),
        "max_window_offset_jump_from_sec": None if max_jump_from is None else max_jump_from["start_sec"],
        "max_window_offset_jump_to_sec": None if max_jump_to is None else max_jump_to["start_sec"],
        "max_window_offset_before_sec": None if max_jump_from is None else max_jump_from["median_offset_sec"],
        "max_window_offset_after_sec": None if max_jump_to is None else max_jump_to["median_offset_sec"],
    }


def _apply_warp_continuity_gate(metronome_lock: dict[str, object], continuity: dict[str, object]) -> dict[str, object]:
    gated = dict(metronome_lock)
    gated["warp_continuity"] = continuity
    if str(continuity.get("verdict", "")) == "jump_risk" and str(gated.get("verdict")) == "daw_locked":
        gated["verdict"] = "mostly_locked"
        gated["continuity_verdict"] = "jump_risk"
    return gated


def _apply_fixed_grid_authority_gate(
    metronome_lock: dict[str, object],
    timing_metrics: dict[str, float],
) -> dict[str, object]:
    gated = dict(metronome_lock)
    fixed = dict(gated.get("fixed_windows") or {})
    continuity = dict(gated.get("warp_continuity") or {})
    beat_phase = dict(gated.get("beat_phase") or {})
    fixed_locked_ratio = float(fixed.get("effective_locked_ratio", fixed.get("locked_ratio", 0.0)) or 0.0)
    segment_locked_ratio = float(gated.get("effective_locked_ratio", gated.get("locked_ratio", 0.0)) or 0.0)
    beat_phase_ok = (
        bool(beat_phase.get("offbeat_alias", False))
        or bool(beat_phase.get("grid_authoritative", False))
        or (
            float(beat_phase.get("avg_abs_error_after_sec", 0.0) or 0.0) <= 0.045
            and float(beat_phase.get("intro_avg_abs_error_after_sec", 0.0) or 0.0) <= 0.045
        )
    )
    phase_window_ok = (
        float(gated.get("overall_phase_window_abs_max_after_sec", 999.0) or 999.0) <= 0.025
        and float(gated.get("overall_phase_window_span_after_sec", 999.0) or 999.0) <= 0.035
    )
    beat_phase_clean = (
        bool(beat_phase.get("locked", False))
        and bool(beat_phase.get("intro_locked", False))
        and float(beat_phase.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.045
        and float(beat_phase.get("intro_avg_abs_error_after_sec", 999.0) or 999.0) <= 0.045
    )
    perfect_fixed_grid_authoritative = (
        fixed_locked_ratio >= 0.999
        and int(fixed.get("unstable_windows", 0) or 0) == 0
        and int(fixed.get("meltdown_windows", 0) or 0) == 0
        and str(continuity.get("verdict", "continuous")) != "jump_risk"
        and float(timing_metrics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.035
        and float(gated.get("effective_locked_ratio", gated.get("locked_ratio", 0.0)) or 0.0) >= 0.70
    )
    strict_fixed_grid_authoritative = (
        fixed_locked_ratio >= 0.999
        and int(fixed.get("unstable_windows", 0) or 0) == 0
        and int(fixed.get("meltdown_windows", 0) or 0) == 0
        and str(continuity.get("verdict", "continuous")) != "jump_risk"
        and beat_phase_ok
        and float(timing_metrics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.035
        and float(gated.get("effective_locked_ratio", gated.get("locked_ratio", 0.0)) or 0.0) >= 0.70
        and (phase_window_ok or beat_phase_clean)
    )
    mostly_fixed_grid_authoritative = (
        fixed_locked_ratio >= 0.96
        and int(fixed.get("unstable_windows", 0) or 0) == 0
        and int(fixed.get("meltdown_windows", 0) or 0) == 0
        and str(continuity.get("verdict", "continuous")) != "jump_risk"
        and float(timing_metrics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.035
        and float(gated.get("effective_locked_ratio", gated.get("locked_ratio", 0.0)) or 0.0) >= 0.95
        and phase_window_ok
    )
    subdivision_alias_fixed_grid_authoritative = (
        fixed_locked_ratio >= 0.999
        and segment_locked_ratio >= 0.999
        and int(fixed.get("unstable_windows", 0) or 0) == 0
        and int(fixed.get("meltdown_windows", 0) or 0) == 0
        and int(gated.get("unstable_segments", 0) or 0) == 0
        and int(gated.get("meltdown_segments", 0) or 0) == 0
        and str(continuity.get("verdict", "continuous")) == "jump_risk"
        and bool(continuity.get("subdivision_alias_jump", False))
        and beat_phase_ok
        and phase_window_ok
        and float(timing_metrics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.045
    )
    mostly_fixed_subdivision_alias_authoritative = (
        fixed_locked_ratio >= 0.96
        and segment_locked_ratio >= 0.92
        and int(fixed.get("unstable_windows", 0) or 0) == 0
        and int(fixed.get("meltdown_windows", 0) or 0) == 0
        and int(gated.get("unstable_segments", 0) or 0) == 0
        and int(gated.get("meltdown_segments", 0) or 0) == 0
        and str(continuity.get("verdict", "continuous")) == "jump_risk"
        and bool(continuity.get("subdivision_alias_jump", False))
        and phase_window_ok
        and float(timing_metrics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.035
    )
    fixed_grid_authoritative = (
        perfect_fixed_grid_authoritative
        or strict_fixed_grid_authoritative
        or mostly_fixed_grid_authoritative
        or subdivision_alias_fixed_grid_authoritative
        or mostly_fixed_subdivision_alias_authoritative
    )
    if fixed_grid_authoritative:
        gated["segment_verdict"] = str(gated.get("verdict", "unknown"))
        gated["verdict"] = "daw_locked"
        gated["fixed_grid_authoritative"] = True
        gated["fixed_grid_authority_note"] = (
            "perfect fixed-window grid evidence treats a one-subdivision continuity alias as non-drifting"
            if subdivision_alias_fixed_grid_authoritative
            else (
                "mostly fixed-window grid evidence treats a subdivision-like continuity alias as non-drifting"
                if mostly_fixed_subdivision_alias_authoritative
                else (
                    "fixed-window grid evidence overrides sparse low-evidence segment warnings"
                    if fixed_locked_ratio >= 0.999
                    else "mostly locked fixed-window evidence is clean and continuity-safe"
                )
            )
        )
        if subdivision_alias_fixed_grid_authoritative or mostly_fixed_subdivision_alias_authoritative:
            continuity = dict(continuity)
            continuity["grid_authoritative"] = True
            continuity["verdict"] = "subdivision_alias"
            gated["warp_continuity"] = continuity
            gated["continuity_verdict"] = "subdivision_alias"
        if beat_phase:
            beat_phase = dict(beat_phase)
            beat_phase["grid_authoritative"] = True
            gated["beat_phase"] = beat_phase
            gated["beat_phase_verdict"] = "grid_authoritative"
    return gated


def _continuity_smooth_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    duration_sec: float,
    target_bpm: float,
    resolution: int,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    smoothed = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    before_metrics = _candidate_metrics_fast(source_times, smoothed, grid, onsets_before)
    before_fixed = _summarize_fixed_window_lock(source_times, smoothed, grid, onsets_before, duration_sec, target_bpm)
    before_fixed_ratio = float(before_fixed.get("effective_locked_ratio", before_fixed.get("locked_ratio", 0.0)))
    before_avg_error = float(before_metrics.get("avg_abs_error_after_sec", 999.0))

    for _ in range(6):
        continuity = _summarize_warp_continuity(source_times, smoothed, target_bpm, resolution)
        if str(continuity.get("verdict", "")) != "jump_risk":
            break
        jump_sec = float(continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
        from_sec = continuity.get("max_window_offset_jump_from_sec")
        to_sec = continuity.get("max_window_offset_jump_to_sec")
        offset_before = continuity.get("max_window_offset_before_sec")
        offset_after = continuity.get("max_window_offset_after_sec")
        if from_sec is None or to_sec is None or offset_before is None or offset_after is None:
            break

        window_sec = float(continuity.get("window_sec", 3.0) or 3.0)
        beat_sec = 60.0 / max(float(target_bpm), 1e-6)
        grid_step_sec = beat_sec * (4.0 / max(float(resolution), 1.0))
        offset_delta = float(offset_before) - float(offset_after)
        alias_shift = round(offset_delta / max(grid_step_sec, 1e-6)) * grid_step_sec
        alias_offset_after = float(offset_after) + float(alias_shift)
        best_candidate: np.ndarray | None = None
        best_decision: dict[str, object] | None = None
        best_rank: tuple[int, float, float, int] | None = None
        for start_multiplier in (1.0, 2.0):
            for end_multiplier in (2.0, 4.0, 8.0, 999.0):
                smooth_start = max(0.0, float(from_sec) - window_sec * start_multiplier)
                smooth_end = min(float(duration_sec), float(to_sec) + window_sec * end_multiplier)
                if smooth_end <= smooth_start + max(0.25, window_sec):
                    continue

                mask = (source_times >= smooth_start) & (source_times <= smooth_end)
                if not np.any(mask):
                    continue

                candidate = smoothed.astype(np.float32, copy=True)
                end_offset = float(np.median(candidate[mask][-min(8, int(np.count_nonzero(mask))):] - source_times[mask][-min(8, int(np.count_nonzero(mask))):]))
                offset_plans: list[tuple[str, float, int]] = [
                    ("smooth_to_tail", end_offset, 0),
                    ("flatten_to_pre_jump", float(offset_before), 1),
                ]
                if abs(alias_shift) >= grid_step_sec * 0.45 and abs(alias_offset_after - float(offset_before)) < abs(float(offset_after) - float(offset_before)):
                    offset_plans.append(("subdivision_alias_realign", end_offset + float(alias_shift), 2))

                for repair_mode, desired_end_offset, repair_priority in offset_plans:
                    candidate = smoothed.astype(np.float32, copy=True)
                    desired_offsets = np.interp(
                        source_times[mask],
                        np.array([smooth_start, smooth_end], dtype=np.float32),
                        np.array([float(offset_before), float(desired_end_offset)], dtype=np.float32),
                    )
                    desired_target = source_times[mask] + desired_offsets
                    weights = _segment_blend_weights(
                        source_times,
                        smooth_start,
                        smooth_end,
                        transition_sec=min(0.9, max(0.35, window_sec * 0.3)),
                    )[mask]
                    candidate[mask] = ((1.0 - weights) * candidate[mask] + weights * desired_target).astype(np.float32)
                    candidate = np.maximum.accumulate(candidate)
                    candidate[-1] = target_times[-1]

                    candidate_continuity = _summarize_warp_continuity(source_times, candidate, target_bpm, resolution)
                    candidate_metrics = _candidate_metrics_fast(source_times, candidate, grid, onsets_before)
                    candidate_fixed = _summarize_fixed_window_lock(source_times, candidate, grid, onsets_before, duration_sec, target_bpm)
                    candidate_fixed_ratio = float(candidate_fixed.get("effective_locked_ratio", candidate_fixed.get("locked_ratio", 0.0)))
                    candidate_avg_error = float(candidate_metrics.get("avg_abs_error_after_sec", 999.0))
                    candidate_jump = float(candidate_continuity.get("max_window_offset_jump_sec", 999.0) or 999.0)
                    fixed_meltdowns = int(candidate_fixed.get("meltdown_windows", 0) or 0)
                    fixed_unstable = int(candidate_fixed.get("unstable_windows", 0) or 0)
                    candidate_verdict = str(candidate_continuity.get("verdict", ""))

                    if candidate_verdict == "jump_risk" and candidate_jump + 1e-6 >= jump_sec:
                        continue
                    if candidate_fixed_ratio + 1e-6 < min(before_fixed_ratio, 0.999):
                        continue
                    if fixed_meltdowns > 0 or fixed_unstable > 0:
                        continue
                    if candidate_avg_error > before_avg_error + 0.012:
                        continue

                    rank = (
                        1 if candidate_verdict != "jump_risk" else 0,
                        -candidate_jump,
                        -candidate_avg_error,
                        repair_priority,
                    )
                    if best_rank is None or rank > best_rank:
                        best_candidate = candidate
                        best_rank = rank
                        best_decision = {
                            "start_sec": float(smooth_start),
                            "end_sec": float(smooth_end),
                            "repair_mode": repair_mode,
                            "jump_before_sec": jump_sec,
                            "jump_after_sec": candidate_jump,
                            "continuity_verdict_after": candidate_verdict,
                            "offset_before_sec": float(offset_before),
                            "offset_after_sec": float(end_offset),
                            "desired_end_offset_sec": float(desired_end_offset),
                            "avg_error_after_sec": candidate_avg_error,
                            "fixed_locked_ratio": candidate_fixed_ratio,
                        }

                if (
                    abs(alias_shift) >= grid_step_sec * 0.45
                    and abs(alias_offset_after - float(offset_before)) < abs(float(offset_after) - float(offset_before))
                ):
                    candidate = smoothed.astype(np.float32, copy=True)
                    ramp_start = max(0.0, float(from_sec))
                    ramp_end = max(ramp_start + 1e-3, float(to_sec))
                    shift_weights = np.clip((source_times - ramp_start) / (ramp_end - ramp_start), 0.0, 1.0).astype(np.float32)
                    candidate = (candidate + shift_weights * float(alias_shift)).astype(np.float32)
                    candidate = np.maximum.accumulate(candidate)

                    candidate_continuity = _summarize_warp_continuity(source_times, candidate, target_bpm, resolution)
                    candidate_metrics = _candidate_metrics_fast(source_times, candidate, grid, onsets_before)
                    candidate_fixed = _summarize_fixed_window_lock(source_times, candidate, grid, onsets_before, duration_sec, target_bpm)
                    candidate_fixed_ratio = float(candidate_fixed.get("effective_locked_ratio", candidate_fixed.get("locked_ratio", 0.0)))
                    candidate_avg_error = float(candidate_metrics.get("avg_abs_error_after_sec", 999.0))
                    candidate_jump = float(candidate_continuity.get("max_window_offset_jump_sec", 999.0) or 999.0)
                    fixed_meltdowns = int(candidate_fixed.get("meltdown_windows", 0) or 0)
                    fixed_unstable = int(candidate_fixed.get("unstable_windows", 0) or 0)
                    candidate_verdict = str(candidate_continuity.get("verdict", ""))

                    if (
                        not (candidate_verdict == "jump_risk" and candidate_jump + 1e-6 >= jump_sec)
                        and candidate_fixed_ratio + 1e-6 >= min(before_fixed_ratio, 0.999)
                        and fixed_meltdowns == 0
                        and fixed_unstable == 0
                        and candidate_avg_error <= before_avg_error + 0.012
                    ):
                        rank = (
                            1 if candidate_verdict != "jump_risk" else 0,
                            -candidate_jump,
                            -candidate_avg_error,
                            3,
                        )
                        if best_rank is None or rank > best_rank:
                            best_candidate = candidate
                            best_rank = rank
                            best_decision = {
                                "start_sec": float(from_sec),
                                "end_sec": float(duration_sec),
                                "repair_mode": "tail_subdivision_alias_realign",
                                "jump_before_sec": jump_sec,
                                "jump_after_sec": candidate_jump,
                                "continuity_verdict_after": candidate_verdict,
                                "offset_before_sec": float(offset_before),
                                "offset_after_sec": float(offset_after),
                                "alias_shift_sec": float(alias_shift),
                                "avg_error_after_sec": candidate_avg_error,
                                "fixed_locked_ratio": candidate_fixed_ratio,
                            }

        if best_candidate is None or best_decision is None:
            break

        smoothed = best_candidate
        decisions.append(best_decision)

    return smoothed, decisions


def _build_daw_lock_diagnostics(metronome_lock: dict[str, object]) -> dict[str, object]:
    verdict = str(metronome_lock.get("verdict", "unknown"))
    fixed = dict(metronome_lock.get("fixed_windows") or {})
    continuity = dict(metronome_lock.get("warp_continuity") or {})
    beat_phase = dict(metronome_lock.get("beat_phase") or {})
    items: list[dict[str, object]] = []

    if str(continuity.get("verdict", "")) == "jump_risk":
        start_sec = continuity.get("max_window_offset_jump_from_sec")
        end_sec = continuity.get("max_window_offset_jump_to_sec")
        offset_before = continuity.get("max_window_offset_before_sec")
        offset_after = continuity.get("max_window_offset_after_sec")
        jump_sec = continuity.get("max_window_offset_jump_sec")
        items.append(
            {
                "type": "warp_continuity_jump",
                "severity": "high",
                "summary": "Largest warp offset jump can shift the DAW bar alignment even when nearby sections stay tempo-locked.",
                "start_sec": start_sec if isinstance(start_sec, (int, float)) else None,
                "end_sec": end_sec if isinstance(end_sec, (int, float)) else None,
                "offset_before_sec": offset_before if isinstance(offset_before, (int, float)) else None,
                "offset_after_sec": offset_after if isinstance(offset_after, (int, float)) else None,
                "offset_jump_sec": jump_sec if isinstance(jump_sec, (int, float)) else None,
                "suggested_focus": "Listen to the grid-check render at this timestamp and prioritize continuous warp repair before changing training data.",
            }
        )

    fixed_unstable = int(fixed.get("unstable_windows", 0) or 0)
    fixed_meltdown = int(fixed.get("meltdown_windows", 0) or 0)
    if fixed_unstable > 0 or fixed_meltdown > 0:
        items.append(
            {
                "type": "fixed_window_instability",
                "severity": "high" if fixed_meltdown > 0 else "medium",
                "summary": "One or more fixed-tempo windows failed the DAW-lock stability thresholds.",
                "unstable_windows": fixed_unstable,
                "meltdown_windows": fixed_meltdown,
                "effective_locked_ratio": fixed.get("effective_locked_ratio", fixed.get("locked_ratio")),
                "suggested_focus": "Inspect the failed fixed windows before trusting the overall segment lock score.",
            }
        )

    if str(metronome_lock.get("beat_phase_verdict", "")) == "risk":
        items.append(
            {
                "type": "beat_phase_shift",
                "severity": "medium",
                "summary": "The track is subdivision-locked, but the detected musical beat is shifted against the quarter-note metronome.",
                "avg_abs_error_after_sec": beat_phase.get("avg_abs_error_after_sec"),
                "intro_avg_abs_error_after_sec": beat_phase.get("intro_avg_abs_error_after_sec"),
                "median_signed_error_after_sec": beat_phase.get("median_signed_error_after_sec"),
                "suggested_focus": "Add a continuity-safe bar/beat phase repair instead of globally shifting the whole warp map.",
            }
        )
    elif str(metronome_lock.get("beat_phase_verdict", "")) == "offbeat_alias":
        items.append(
            {
                "type": "beat_phase_offbeat_alias",
                "severity": "info",
                "summary": "The beat tracker appears to be following a consistent eighth-note offbeat rather than the downbeat.",
                "avg_abs_error_after_sec": beat_phase.get("avg_abs_error_after_sec"),
                "intro_avg_abs_error_after_sec": beat_phase.get("intro_avg_abs_error_after_sec"),
                "median_signed_error_after_sec": beat_phase.get("median_signed_error_after_sec"),
                "suggested_focus": "Use fixed-window lock and listening checks before applying any destructive phase shift.",
            }
        )
    elif str(metronome_lock.get("beat_phase_verdict", "")) == "grid_authoritative":
        items.append(
            {
                "type": "beat_phase_grid_authoritative",
                "severity": "info",
                "summary": "Fixed-window and segment locks are perfect, so the beat tracker is treated as ambiguous rather than blocking DAW lock.",
                "avg_abs_error_after_sec": beat_phase.get("avg_abs_error_after_sec"),
                "intro_avg_abs_error_after_sec": beat_phase.get("intro_avg_abs_error_after_sec"),
                "median_signed_error_after_sec": beat_phase.get("median_signed_error_after_sec"),
                "suggested_focus": "Trust the fixed-grid metronome render unless listening reveals a real bar/downbeat offset.",
            }
        )

    if not items:
        summary = "No DAW-lock diagnostic issues detected."
    elif any(str(item.get("type")) == "warp_continuity_jump" for item in items):
        summary = "DAW-lock risk: a warp-continuity jump was detected; inspect the reported timestamp in the grid-check render."
    elif any(str(item.get("type")) == "beat_phase_shift" for item in items):
        summary = "DAW-lock risk: musical beat phase is shifted against the quarter-note metronome."
    elif any(str(item.get("type")) == "beat_phase_offbeat_alias" for item in items):
        summary = "DAW-lock note: beat tracker is following an eighth-note offbeat alias."
    elif any(str(item.get("type")) == "beat_phase_grid_authoritative" for item in items):
        summary = "DAW-lock note: fixed-grid lock is authoritative; beat tracker appears ambiguous."
    else:
        summary = "DAW-lock risk: fixed-window instability was detected; inspect failed windows before rerunning."

    return {
        "verdict": verdict,
        "summary": summary,
        "issue_count": int(len(items)),
        "items": items,
    }


def _fixed_window_repair_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    duration_sec: float,
    target_bpm: float,
) -> tuple[np.ndarray, list[dict[str, object]], dict[str, object]]:
    repaired = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    diagnostics: dict[str, object] = {"iterations": []}

    def summary_key(summary: dict[str, object]) -> tuple[float, float, int, int]:
        return (
            float(summary.get("effective_locked_ratio", summary.get("locked_ratio", 0.0))),
            float(summary.get("effective_strong_locked_ratio", summary.get("strong_locked_ratio", 0.0))),
            -int(summary.get("unstable_windows", 0)),
            -int(summary.get("meltdown_windows", 0)),
        )

    grid_step = float(np.median(np.diff(grid))) if len(grid) > 1 else 60.0 / max(float(target_bpm), 1e-6) * 0.5
    inferred_resolution = int(round((60.0 / max(float(target_bpm), 1e-6)) * 4.0 / max(grid_step, 1e-6)))
    current_summary = _summarize_fixed_window_lock(source_times, repaired, grid, onsets_before, duration_sec, target_bpm)
    current_continuity = _summarize_warp_continuity(source_times, repaired, target_bpm, inferred_resolution)
    diagnostics["initial_summary"] = current_summary
    diagnostics["initial_continuity"] = current_continuity
    if int(current_summary.get("window_count", 0)) == 0 or float(current_summary.get("effective_locked_ratio", current_summary.get("locked_ratio", 0.0))) >= 0.999:
        diagnostics["stop_reason"] = "already_locked_or_no_windows"
        return repaired, decisions, diagnostics

    for _ in range(8):
        current_key = summary_key(current_summary)
        best_candidate: np.ndarray | None = None
        best_summary: dict[str, object] | None = None
        best_decision: dict[str, object] | None = None
        best_rank: tuple[float, float, int, int, float, float, float] | None = None
        iteration_diagnostics = {
            "unlocked_windows": 0,
            "candidates": 0,
            "key_not_improved": 0,
            "key_improved": 0,
            "rejected_jump_risk": 0,
            "rejected_stretch": 0,
            "rejected_no_metrics": 0,
            "rejected_local_error": 0,
        }

        for window in list(current_summary.get("windows") or []):
            if bool(window.get("locked")):
                continue
            iteration_diagnostics["unlocked_windows"] += 1
            start_sec = float(window.get("start_sec", 0.0))
            end_sec = float(window.get("end_sec", start_sec))
            base_metrics = _segment_timing_metrics(source_times, repaired, grid, onsets_before, start_sec, end_sec)
            if base_metrics is None:
                continue
            base_error = float(base_metrics.get("avg_abs_error_after_sec", 0.0))

            candidates: list[tuple[np.ndarray, dict[str, object]]] = []
            for transition_sec in (1.0, 1.5, 2.0, 3.0, 4.0, 6.0):
                weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=transition_sec)
                if not np.any(weights > 0.0):
                    continue
                for shift_sec in (
                    -0.07,
                    -0.055,
                    -0.045,
                    -0.035,
                    -0.025,
                    -0.02,
                    -0.015,
                    -0.0125,
                    -0.01,
                    0.01,
                    0.0125,
                    0.015,
                    0.02,
                    0.025,
                    0.035,
                    0.045,
                    0.055,
                    0.07,
                ):
                    candidate = (repaired + weights * float(shift_sec)).astype(np.float32)
                    candidate = np.maximum.accumulate(candidate)
                    candidate[-1] = target_times[-1]
                    iteration_diagnostics["candidates"] += 1
                    candidates.append(
                        (
                            candidate,
                            {
                                "repair": "phase_shift",
                                "shift_sec": float(shift_sec),
                                "transition_sec": float(transition_sec),
                            },
                        )
                    )

            for candidate, repair_info in candidates:
                candidate_summary = _summarize_fixed_window_lock(
                    source_times,
                    candidate,
                    grid,
                    onsets_before,
                    duration_sec,
                    target_bpm,
                )
                candidate_key = summary_key(candidate_summary)
                if candidate_key <= current_key:
                    iteration_diagnostics["key_not_improved"] += 1
                    continue
                iteration_diagnostics["key_improved"] += 1
                continuity = _summarize_warp_continuity(source_times, candidate, target_bpm, inferred_resolution)
                current_jump_risk = str(current_continuity.get("verdict", "")) == "jump_risk"
                current_jump = float(current_continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
                candidate_jump = float(continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
                if str(continuity.get("verdict", "")) == "jump_risk" and (
                    not current_jump_risk or candidate_jump > current_jump + 0.015
                ):
                    iteration_diagnostics["rejected_jump_risk"] += 1
                    continue
                max_stretch_limit = max(0.12, float(current_continuity.get("max_local_stretch_delta", 0.0)) + 0.03)
                p99_stretch_limit = max(0.16, float(current_continuity.get("p99_local_stretch_delta", 0.0)) + 0.03)
                if (
                    float(continuity.get("max_local_stretch_delta", 0.0)) > max_stretch_limit
                    or float(continuity.get("p99_local_stretch_delta", 0.0)) > p99_stretch_limit
                ):
                    iteration_diagnostics["rejected_stretch"] += 1
                    continue
                candidate_metrics = _segment_timing_metrics(source_times, candidate, grid, onsets_before, start_sec, end_sec)
                if candidate_metrics is None:
                    iteration_diagnostics["rejected_no_metrics"] += 1
                    continue
                candidate_error = float(candidate_metrics.get("avg_abs_error_after_sec", base_error))
                if candidate_error > max(0.066, base_error + 0.002):
                    iteration_diagnostics["rejected_local_error"] += 1
                    continue
                candidate_overall = _candidate_metrics_fast(source_times, candidate, grid, onsets_before)
                candidate_rank = (
                    *candidate_key,
                    -float(candidate_overall.get("avg_abs_error_after_sec", 999.0)),
                    -candidate_error,
                    -float(continuity.get("max_window_offset_jump_sec", 999.0)),
                )
                if best_rank is None or candidate_rank > best_rank:
                    best_candidate = candidate
                    best_summary = candidate_summary
                    best_rank = candidate_rank
                    best_decision = {
                        "start_sec": start_sec,
                        "end_sec": end_sec,
                        "before_locked_ratio": float(current_summary.get("locked_ratio", 0.0)),
                        "after_locked_ratio": float(candidate_summary.get("locked_ratio", 0.0)),
                        "before_strong_locked_ratio": float(current_summary.get("strong_locked_ratio", 0.0)),
                        "after_strong_locked_ratio": float(candidate_summary.get("strong_locked_ratio", 0.0)),
                        "before_error_sec": base_error,
                        "after_error_sec": candidate_error,
                        **repair_info,
                    }

        if best_candidate is None or best_summary is None or best_decision is None:
            iteration_diagnostics["selected"] = None
            diagnostics["iterations"].append(iteration_diagnostics)
            diagnostics["stop_reason"] = "no_safe_improving_candidate"
            break
        repaired = best_candidate
        current_summary = best_summary
        current_continuity = _summarize_warp_continuity(source_times, repaired, target_bpm, inferred_resolution)
        decisions.append(best_decision)
        iteration_diagnostics["selected"] = best_decision
        diagnostics["iterations"].append(iteration_diagnostics)
        if float(current_summary.get("effective_locked_ratio", current_summary.get("locked_ratio", 0.0))) >= 0.999:
            diagnostics["stop_reason"] = "locked_after_repair"
            break

    diagnostics.setdefault("stop_reason", "iteration_limit")
    diagnostics["final_summary"] = current_summary
    return repaired, decisions, diagnostics


def _nearest_grid_time(grid: np.ndarray, time_sec: float) -> float:
    if len(grid) == 0:
        return float(time_sec)
    idx = int(np.searchsorted(grid, time_sec))
    if 0 < idx < len(grid):
        prev_time = float(grid[idx - 1])
        next_time = float(grid[idx])
        if abs(time_sec - prev_time) <= abs(next_time - time_sec):
            idx -= 1
    return float(grid[int(np.clip(idx, 0, len(grid) - 1))])


def _refine_segments_for_strict_lock(
    segments: list[dict[str, float]],
    grid: np.ndarray,
    duration_sec: float,
    event_summary: dict[str, object],
) -> list[dict[str, float]]:
    if len(segments) < 2:
        return segments
    if duration_sec < 45.0:
        return segments
    if float(event_summary.get("event_density_per_sec", 0.0)) < 0.45:
        return segments
    if int(event_summary.get("strong_event_count", 0)) < 48:
        return segments

    step = float(np.median(np.diff(grid))) if len(grid) > 1 else max(duration_sec / max(len(segments), 1), 0.25)
    min_segment_sec = max(4.0, step * 8.0)
    tail_floor_sec = max(3.0, min_segment_sec * 0.75)

    snapped_boundaries = [0.0]
    for segment in segments[:-1]:
        boundary = _nearest_grid_time(grid, float(segment["end_sec"]))
        if boundary <= snapped_boundaries[-1] + max(step, 0.25):
            continue
        if boundary >= duration_sec - 0.25:
            continue
        snapped_boundaries.append(boundary)
    if duration_sec > snapped_boundaries[-1]:
        snapped_boundaries.append(float(duration_sec))

    refined: list[dict[str, float]] = []
    current_start = float(snapped_boundaries[0])
    for idx in range(1, len(snapped_boundaries)):
        boundary = float(snapped_boundaries[idx])
        segment_len = boundary - current_start
        remaining = float(duration_sec - boundary)
        if segment_len + 1e-6 < min_segment_sec and idx < len(snapped_boundaries) - 1:
            continue
        if remaining > 0.0 and remaining < tail_floor_sec and idx < len(snapped_boundaries) - 1:
            continue
        if boundary <= current_start + 1e-6:
            continue
        refined.append({"start_sec": current_start, "end_sec": boundary})
        current_start = boundary

    if not refined or current_start < duration_sec - 1e-6:
        if refined and duration_sec - current_start < tail_floor_sec:
            refined[-1]["end_sec"] = float(duration_sec)
        else:
            refined.append({"start_sec": current_start, "end_sec": float(duration_sec)})

    normalized: list[dict[str, float]] = []
    prev_end = 0.0
    for segment in refined:
        start_sec = max(prev_end, float(segment["start_sec"]))
        end_sec = max(start_sec, float(segment["end_sec"]))
        if end_sec - start_sec < 1e-3:
            continue
        normalized.append({"start_sec": start_sec, "end_sec": end_sec})
        prev_end = end_sec

    return normalized or segments


def _stabilize_baseline_target(
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
    target_bpm: float,
    resolution: int,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    stabilized = baseline_target.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    segment_rows: list[dict[str, object]] = []
    duration_sec = float(source_times[-1]) if len(source_times) else 0.0

    for index, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            stabilized,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue

        after_error = float(metrics["avg_abs_error_after_sec"])
        improvement_pct = float(metrics["improvement_pct"])
        phase_abs_max = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))

        retreat_strength = 0.0
        transition_sec = 0.24
        if after_error >= 0.09 and not (improvement_pct >= 8.0 and phase_abs_max < 0.05 and phase_span < 0.06):
            retreat_strength = 0.35
            if after_error >= 0.11 or improvement_pct < 0.0:
                retreat_strength = 0.5
            if phase_abs_max >= 0.08 or phase_span >= 0.1:
                retreat_strength = max(retreat_strength, 0.6)
            if after_error >= 0.1 and improvement_pct < 2.5:
                retreat_strength = max(retreat_strength, 0.72)
                transition_sec = 0.4
            elif after_error >= 0.095 and improvement_pct < 6.0:
                retreat_strength = max(retreat_strength, 0.55)
                transition_sec = max(transition_sec, 0.3)
            elif phase_abs_max >= 0.05 and after_error >= 0.075:
                retreat_strength = max(retreat_strength, 0.45)
                transition_sec = max(transition_sec, 0.28)

        bridge_candidate = phase_abs_max >= 0.055 and after_error >= 0.05
        segment_rows.append(
            {
                "index": index,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "after_error_sec": after_error,
                "improvement_pct": improvement_pct,
                "phase_window_abs_max_after_sec": phase_abs_max,
                "phase_window_span_after_sec": phase_span,
                "retreat_strength": float(retreat_strength),
                "transition_sec": float(transition_sec),
                "bridge_candidate": bridge_candidate,
            }
        )

    if not segment_rows:
        return stabilized, decisions

    active_indexes = {
        int(row["index"])
        for row in segment_rows
        if float(row["retreat_strength"]) > 0.0
    }
    for row in segment_rows:
        idx = int(row["index"])
        if not bool(row["bridge_candidate"]):
            continue
        if (idx - 1 in active_indexes) or (idx + 1 in active_indexes):
            row["retreat_strength"] = max(float(row["retreat_strength"]), 0.4)
            row["transition_sec"] = max(float(row["transition_sec"]), 0.3)
            active_indexes.add(idx)

    active_rows = [row for row in segment_rows if float(row["retreat_strength"]) > 0.0]
    cluster: list[dict[str, object]] = []
    clusters: list[list[dict[str, object]]] = []
    for row in active_rows:
        if not cluster:
            cluster = [row]
            continue
        prev = cluster[-1]
        if int(row["index"]) <= int(prev["index"]) + 2:
            cluster.append(row)
        else:
            clusters.append(cluster)
            cluster = [row]
    if cluster:
        clusters.append(cluster)

    for cluster_rows in clusters:
        start_sec = float(cluster_rows[0]["start_sec"])
        end_sec = float(cluster_rows[-1]["end_sec"])
        retreat_strength = max(float(row["retreat_strength"]) for row in cluster_rows)
        transition_sec = max(float(row["transition_sec"]) for row in cluster_rows)
        cluster_count = len(cluster_rows)
        reanchored_cluster = False
        before_cluster = stabilized.astype(np.float32, copy=True)
        before_metrics = _candidate_metrics_fast(source_times, before_cluster, grid, onsets_before)
        before_fixed = _summarize_fixed_window_lock(
            source_times,
            before_cluster,
            grid,
            onsets_before,
            duration_sec,
            target_bpm,
        )
        before_continuity = _summarize_warp_continuity(source_times, before_cluster, target_bpm, resolution)
        if cluster_count >= 2:
            cluster_weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=transition_sec)
            cluster_mask = cluster_weights > 0.0
            if np.any(cluster_mask):
                cluster_linear = stabilized.astype(np.float32, copy=True)
                cluster_linear[cluster_mask] = np.interp(
                    source_times[cluster_mask],
                    np.array([start_sec, end_sec], dtype=np.float32),
                    np.array(
                        [
                            float(np.interp(start_sec, source_times, stabilized)),
                            float(np.interp(end_sec, source_times, stabilized)),
                        ],
                        dtype=np.float32,
                    ),
                ).astype(np.float32)
                smooth_strength = min(0.55, 0.2 + 0.1 * cluster_count)
                stabilized = ((1.0 - cluster_weights * smooth_strength) * stabilized + (cluster_weights * smooth_strength) * cluster_linear).astype(np.float32)
                stabilized = np.maximum.accumulate(stabilized)
                stabilized[-1] = baseline_target[-1]
        weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=transition_sec) * retreat_strength
        stabilized = ((1.0 - weights) * stabilized + weights * source_times).astype(np.float32)
        stabilized = np.maximum.accumulate(stabilized)
        stabilized[-1] = baseline_target[-1]
        if cluster_count >= 2 and retreat_strength >= 0.55:
            reanchored = _reanchor_cluster_target(
                source_times,
                stabilized,
                grid,
                onsets_before,
                start_sec,
                end_sec,
            )
            if reanchored is not None:
                reanchor_weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=max(transition_sec, 0.36))
                reanchor_strength = min(0.82, 0.45 + 0.08 * cluster_count + 0.2 * max(retreat_strength - 0.55, 0.0))
                stabilized = (
                    (1.0 - reanchor_weights * reanchor_strength) * stabilized
                    + (reanchor_weights * reanchor_strength) * reanchored
                ).astype(np.float32)
                stabilized = np.maximum.accumulate(stabilized)
                stabilized[-1] = baseline_target[-1]
                reanchored_cluster = True

        after_metrics = _candidate_metrics_fast(source_times, stabilized, grid, onsets_before)
        after_fixed = _summarize_fixed_window_lock(
            source_times,
            stabilized,
            grid,
            onsets_before,
            duration_sec,
            target_bpm,
        )
        after_continuity = _summarize_warp_continuity(source_times, stabilized, target_bpm, resolution)
        before_avg = float(before_metrics.get("avg_abs_error_after_sec", 999.0))
        after_avg = float(after_metrics.get("avg_abs_error_after_sec", 999.0))
        before_fixed_ratio = float(before_fixed.get("effective_locked_ratio", before_fixed.get("locked_ratio", 0.0)) or 0.0)
        after_fixed_ratio = float(after_fixed.get("effective_locked_ratio", after_fixed.get("locked_ratio", 0.0)) or 0.0)
        before_verdict = str(before_continuity.get("verdict", "unknown"))
        after_verdict = str(after_continuity.get("verdict", "unknown"))
        before_jump = float(before_continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
        after_jump = float(after_continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
        rejected_reason = ""
        if duration_sec >= 45.0:
            if before_verdict != "jump_risk" and after_verdict == "jump_risk":
                rejected_reason = "introduced_warp_jump"
            elif after_verdict == "jump_risk" and after_jump + 1e-6 >= before_jump:
                rejected_reason = "warp_jump_not_improved"
            elif after_fixed_ratio + 1e-6 < before_fixed_ratio - 0.02:
                rejected_reason = "fixed_window_lock_regressed"
            elif after_avg > before_avg + 0.012:
                rejected_reason = "avg_error_regressed"
        if rejected_reason:
            stabilized = before_cluster

        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "after_error_sec": max(float(row["after_error_sec"]) for row in cluster_rows),
                "improvement_pct": min(float(row["improvement_pct"]) for row in cluster_rows),
                "phase_window_abs_max_after_sec": max(float(row["phase_window_abs_max_after_sec"]) for row in cluster_rows),
                "phase_window_span_after_sec": max(float(row["phase_window_span_after_sec"]) for row in cluster_rows),
                "transition_sec": float(transition_sec),
                "retreat_strength": float(retreat_strength),
                "segment_count": cluster_count,
                "linearized_cluster": bool(cluster_count >= 2),
                "reanchored_cluster": reanchored_cluster,
                "accepted": not bool(rejected_reason),
                "rejected_reason": rejected_reason or None,
                "continuity_before": before_verdict,
                "continuity_after": after_verdict,
                "jump_before_sec": before_jump,
                "jump_after_sec": after_jump,
                "fixed_locked_ratio_before": before_fixed_ratio,
                "fixed_locked_ratio_after": after_fixed_ratio,
                "avg_error_before_sec": before_avg,
                "avg_error_after_sec": after_avg,
            }
        )

    return stabilized, decisions


def _phase_snap_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
    target_bpm: float,
    resolution: int,
) -> tuple[np.ndarray, list[dict[str, object]]]:
    snapped = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []

    for segment in segments:
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            snapped,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue

        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        median_signed = float(metrics.get("median_signed_error_after_sec", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        if after_error < 0.085 or improvement_pct >= 5.0:
            continue
        if abs(median_signed) < 0.01 or phase_abs > 0.025 or phase_span > 0.03:
            continue

        shift_sec = float(np.clip(-median_signed, -0.045, 0.045))
        weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=0.22)
        if not np.any(weights > 0.0):
            continue
        before_continuity = _summarize_warp_continuity(source_times, snapped, target_bpm, resolution)
        segment_shift = weights * shift_sec
        candidate = (snapped + segment_shift).astype(np.float32)
        candidate = np.maximum.accumulate(candidate)
        candidate[-1] = target_times[-1]
        candidate_continuity = _summarize_warp_continuity(source_times, candidate, target_bpm, resolution)
        before_verdict = str(before_continuity.get("verdict", "unknown"))
        candidate_verdict = str(candidate_continuity.get("verdict", "unknown"))
        before_jump = float(before_continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
        candidate_jump = float(candidate_continuity.get("max_window_offset_jump_sec", 0.0) or 0.0)
        if before_verdict != "jump_risk" and candidate_verdict == "jump_risk":
            continue
        if candidate_verdict == "jump_risk" and candidate_jump + 1e-6 >= before_jump:
            continue
        candidate_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if candidate_metrics is None:
            continue
        if float(candidate_metrics.get("avg_abs_error_after_sec", after_error)) + 1e-6 >= after_error:
            continue

        snapped = candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "shift_sec": shift_sec,
                "after_error_sec": after_error,
                "snapped_error_sec": float(candidate_metrics.get("avg_abs_error_after_sec", after_error)),
                "median_signed_error_after_sec": median_signed,
                "continuity_before": before_verdict,
                "continuity_after": candidate_verdict,
                "jump_before_sec": before_jump,
                "jump_after_sec": candidate_jump,
            }
        )

    return snapped, decisions


def _coverage_snap_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    snapped = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []

    for segment in segments:
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            snapped,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue

        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        median_signed = float(metrics.get("median_signed_error_after_sec", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        if after_error < 0.06 or after_error > 0.075:
            continue
        if improvement_pct < 0.0:
            continue
        if abs(median_signed) < 0.009 or phase_abs > 0.022 or phase_span > 0.03:
            continue

        shift_sec = float(np.clip(-median_signed, -0.018, 0.018))
        weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=0.18)
        if not np.any(weights > 0.0):
            continue
        candidate = (snapped + weights * shift_sec).astype(np.float32)
        candidate = np.maximum.accumulate(candidate)
        candidate[-1] = target_times[-1]
        candidate_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if candidate_metrics is None:
            continue
        candidate_after = float(candidate_metrics.get("avg_abs_error_after_sec", after_error))
        if candidate_after + 1e-6 >= after_error:
            continue
        snapped = candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "shift_sec": shift_sec,
                "after_error_sec": after_error,
                "snapped_error_sec": candidate_after,
                "median_signed_error_after_sec": median_signed,
            }
        )

    return snapped, decisions


def _coverage_tighten_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    strict_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    tightened = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []

    for segment in segments:
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            tightened,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue

        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        median_signed = float(metrics.get("median_signed_error_after_sec", 0.0))
        if after_error < 0.06 or after_error > 0.085:
            continue
        if improvement_pct < 0.0:
            continue
        if phase_abs > 0.03 or phase_span > 0.04:
            continue

        best_candidate: np.ndarray | None = None
        best_metrics: dict[str, float] | None = None
        best_alpha: float | None = None
        alpha_seed = float(np.clip(0.18 + max(0.0, after_error - 0.06) * 6.0, 0.18, 0.5))
        alpha_candidates = sorted({0.18, 0.24, 0.3, 0.38, alpha_seed})
        for alpha in alpha_candidates:
            weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=0.2) * float(alpha)
            if not np.any(weights > 0.0):
                continue

            candidate = ((1.0 - weights) * tightened + weights * strict_target).astype(np.float32)
            candidate = np.maximum.accumulate(candidate)
            candidate[-1] = target_times[-1]
            candidate_metrics = _segment_timing_metrics(
                source_times,
                candidate,
                grid,
                onsets_before,
                start_sec,
                end_sec,
            )
            if candidate_metrics is None:
                continue
            candidate_after = float(candidate_metrics.get("avg_abs_error_after_sec", after_error))
            candidate_phase_abs = float(candidate_metrics.get("phase_window_abs_max_after_sec", phase_abs))
            candidate_phase_span = float(candidate_metrics.get("phase_window_span_after_sec", phase_span))
            if candidate_after + 1e-6 >= after_error:
                continue
            if candidate_phase_abs > phase_abs + 0.01 or candidate_phase_span > phase_span + 0.015:
                continue
            if best_metrics is None or candidate_after + 1e-6 < float(best_metrics.get("avg_abs_error_after_sec", after_error)):
                best_candidate = candidate
                best_metrics = candidate_metrics
                best_alpha = float(alpha)

        if best_candidate is None or best_metrics is None or best_alpha is None:
            continue

        tightened = best_candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "alpha": best_alpha,
                "after_error_sec": after_error,
                "tightened_error_sec": float(best_metrics.get("avg_abs_error_after_sec", after_error)),
                "median_signed_error_after_sec": median_signed,
            }
        )

    return tightened, decisions


def _coverage_rebuild_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    strict_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    rebuilt = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    candidate_rows: list[dict[str, float]] = []

    for idx, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            rebuilt,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        if after_error < 0.06 or after_error > 0.085:
            continue
        if improvement_pct < 0.0:
            continue
        if phase_abs > 0.03 or phase_span > 0.04:
            continue
        candidate_rows.append(
            {
                "index": idx,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "after_error_sec": after_error,
                "median_signed_error_after_sec": float(metrics.get("median_signed_error_after_sec", 0.0)),
            }
        )

    if not candidate_rows:
        return rebuilt, decisions

    clusters: list[list[dict[str, float]]] = []
    cluster: list[dict[str, float]] = []
    for row in candidate_rows:
        if not cluster:
            cluster = [row]
            continue
        prev = cluster[-1]
        if int(row["index"]) <= int(prev["index"]) + 1:
            cluster.append(row)
        else:
            clusters.append(cluster)
            cluster = [row]
    if cluster:
        clusters.append(cluster)

    for cluster_rows in clusters:
        start_sec = float(cluster_rows[0]["start_sec"])
        end_sec = float(cluster_rows[-1]["end_sec"])
        base_metrics = _segment_timing_metrics(
            source_times,
            rebuilt,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if base_metrics is None:
            continue
        base_error = float(base_metrics.get("avg_abs_error_after_sec", 0.0))
        base_phase_abs = float(base_metrics.get("phase_window_abs_max_after_sec", 0.0))
        base_phase_span = float(base_metrics.get("phase_window_span_after_sec", 0.0))
        base_median_signed = float(base_metrics.get("median_signed_error_after_sec", 0.0))

        best_candidate: np.ndarray | None = None
        best_metrics: dict[str, float] | None = None
        best_alpha: float | None = None
        best_shift_sec = 0.0
        transition_sec = 0.18 if len(cluster_rows) == 1 else 0.24
        alpha_candidates = (0.3, 0.45, 0.6, 0.75, 0.9, 1.0) if len(cluster_rows) == 1 else (0.45, 0.6, 0.75, 0.9, 1.0)
        shift_seed = float(np.clip(-base_median_signed, -0.018, 0.018))
        shift_candidates = [0.0]
        if abs(shift_seed) >= 0.006:
            shift_candidates.extend([shift_seed * 0.5, shift_seed])

        for alpha in alpha_candidates:
            weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=transition_sec) * float(alpha)
            if not np.any(weights > 0.0):
                continue
            blended = ((1.0 - weights) * rebuilt + weights * strict_target).astype(np.float32)
            for shift_sec in shift_candidates:
                candidate = blended
                if abs(shift_sec) > 1e-6:
                    candidate = (blended + weights * float(shift_sec)).astype(np.float32)
                candidate = np.maximum.accumulate(candidate)
                candidate[-1] = target_times[-1]
                candidate_metrics = _segment_timing_metrics(
                    source_times,
                    candidate,
                    grid,
                    onsets_before,
                    start_sec,
                    end_sec,
                )
                if candidate_metrics is None:
                    continue
                candidate_error = float(candidate_metrics.get("avg_abs_error_after_sec", base_error))
                candidate_phase_abs = float(candidate_metrics.get("phase_window_abs_max_after_sec", base_phase_abs))
                candidate_phase_span = float(candidate_metrics.get("phase_window_span_after_sec", base_phase_span))
                if candidate_error + 1e-6 >= base_error:
                    continue
                if candidate_phase_abs > base_phase_abs + 0.012 or candidate_phase_span > base_phase_span + 0.02:
                    continue
                if best_metrics is None or candidate_error + 1e-6 < float(best_metrics.get("avg_abs_error_after_sec", base_error)):
                    best_candidate = candidate
                    best_metrics = candidate_metrics
                    best_alpha = float(alpha)
                    best_shift_sec = float(shift_sec)

        if best_candidate is None or best_metrics is None or best_alpha is None:
            continue

        rebuilt = best_candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "segment_count": len(cluster_rows),
                "alpha": best_alpha,
                "shift_sec": best_shift_sec,
                "after_error_sec": base_error,
                "rebuilt_error_sec": float(best_metrics.get("avg_abs_error_after_sec", base_error)),
            }
        )

    return rebuilt, decisions


def _coverage_commit_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    strict_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    committed = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []

    for segment in segments:
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            committed,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue

        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        median_signed = float(metrics.get("median_signed_error_after_sec", 0.0))
        if after_error <= 0.06 or after_error > 0.074:
            continue
        if improvement_pct < 5.0:
            continue
        if phase_abs > 0.03 or phase_span > 0.04:
            continue

        weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=0.14)
        if not np.any(weights > 0.0):
            continue

        shift_sec = float(np.clip(-median_signed, -0.014, 0.014))
        candidate = ((1.0 - weights) * committed + weights * strict_target).astype(np.float32)
        if abs(shift_sec) > 0.002:
            candidate = (candidate + weights * shift_sec).astype(np.float32)
        candidate = np.maximum.accumulate(candidate)
        candidate[-1] = target_times[-1]
        candidate_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if candidate_metrics is None:
            continue
        candidate_after = float(candidate_metrics.get("avg_abs_error_after_sec", after_error))
        candidate_phase_abs = float(candidate_metrics.get("phase_window_abs_max_after_sec", phase_abs))
        candidate_phase_span = float(candidate_metrics.get("phase_window_span_after_sec", phase_span))
        if candidate_after >= 0.0605:
            continue
        if candidate_after + 0.001 >= after_error:
            continue
        if candidate_phase_abs > phase_abs + 0.01 or candidate_phase_span > phase_span + 0.015:
            continue

        committed = candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "shift_sec": shift_sec,
                "after_error_sec": after_error,
                "committed_error_sec": candidate_after,
            }
        )

    return committed, decisions


def _coverage_reanchor_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    reanchored_target = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    candidate_rows: list[dict[str, float]] = []

    for idx, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            reanchored_target,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        if after_error <= 0.06 or after_error > 0.085:
            continue
        if improvement_pct < 8.0:
            continue
        if phase_abs > 0.025 or phase_span > 0.04:
            continue
        candidate_rows.append(
            {
                "index": idx,
                "start_sec": start_sec,
                "end_sec": end_sec,
            }
        )

    if not candidate_rows:
        return reanchored_target, decisions

    clusters: list[list[dict[str, float]]] = []
    cluster: list[dict[str, float]] = []
    for row in candidate_rows:
        if not cluster:
            cluster = [row]
            continue
        prev = cluster[-1]
        if int(row["index"]) <= int(prev["index"]) + 2:
            cluster.append(row)
        else:
            clusters.append(cluster)
            cluster = [row]
    if cluster:
        clusters.append(cluster)

    for cluster_rows in clusters:
        if len(cluster_rows) < 2:
            continue
        start_sec = float(cluster_rows[0]["start_sec"])
        end_sec = float(cluster_rows[-1]["end_sec"])
        base_metrics = _segment_timing_metrics(
            source_times,
            reanchored_target,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if base_metrics is None:
            continue
        base_error = float(base_metrics.get("avg_abs_error_after_sec", 0.0))
        base_phase_abs = float(base_metrics.get("phase_window_abs_max_after_sec", 0.0))
        base_phase_span = float(base_metrics.get("phase_window_span_after_sec", 0.0))

        repaired = _reanchor_cluster_target(
            source_times,
            reanchored_target,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if repaired is None:
            continue

        best_candidate: np.ndarray | None = None
        best_metrics: dict[str, float] | None = None
        best_strength: float | None = None
        for strength in (0.55, 0.72, 0.88, 1.0):
            weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=0.28) * float(strength)
            if not np.any(weights > 0.0):
                continue
            candidate = ((1.0 - weights) * reanchored_target + weights * repaired).astype(np.float32)
            candidate = np.maximum.accumulate(candidate)
            candidate[-1] = target_times[-1]
            candidate_metrics = _segment_timing_metrics(
                source_times,
                candidate,
                grid,
                onsets_before,
                start_sec,
                end_sec,
            )
            if candidate_metrics is None:
                continue
            candidate_error = float(candidate_metrics.get("avg_abs_error_after_sec", base_error))
            candidate_phase_abs = float(candidate_metrics.get("phase_window_abs_max_after_sec", base_phase_abs))
            candidate_phase_span = float(candidate_metrics.get("phase_window_span_after_sec", base_phase_span))
            if candidate_error + 1e-6 >= base_error:
                continue
            if candidate_phase_abs > base_phase_abs + 0.012 or candidate_phase_span > base_phase_span + 0.02:
                continue
            if best_metrics is None or candidate_error + 1e-6 < float(best_metrics.get("avg_abs_error_after_sec", base_error)):
                best_candidate = candidate
                best_metrics = candidate_metrics
                best_strength = float(strength)

        if best_candidate is None or best_metrics is None or best_strength is None:
            continue

        reanchored_target = best_candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "segment_count": len(cluster_rows),
                "strength": best_strength,
                "after_error_sec": base_error,
                "reanchored_error_sec": float(best_metrics.get("avg_abs_error_after_sec", base_error)),
            }
        )

    return reanchored_target, decisions


def _coverage_bridge_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    strict_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    bridged = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    segment_rows: list[dict[str, float | bool | int]] = []

    for idx, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            bridged,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        locked = after_error <= 0.06 and improvement_pct >= 0.0
        bridge_candidate = (
            after_error > 0.06
            and after_error <= 0.078
            and improvement_pct >= 8.0
            and phase_abs <= 0.03
            and phase_span <= 0.05
        )
        segment_rows.append(
            {
                "index": idx,
                "row_position": len(segment_rows),
                "start_sec": start_sec,
                "end_sec": end_sec,
                "locked": locked,
                "bridge_candidate": bridge_candidate,
                "median_signed_error_after_sec": float(metrics.get("median_signed_error_after_sec", 0.0)),
            }
        )

    if not segment_rows:
        return bridged, decisions

    clusters: list[list[dict[str, float | bool | int]]] = []
    cluster: list[dict[str, float | bool | int]] = []
    for row in segment_rows:
        if not bool(row["bridge_candidate"]):
            if cluster:
                clusters.append(cluster)
                cluster = []
            continue
        if not cluster:
            cluster = [row]
            continue
        prev = cluster[-1]
        if int(row["index"]) <= int(prev["index"]) + 1:
            cluster.append(row)
        else:
            clusters.append(cluster)
            cluster = [row]
    if cluster:
        clusters.append(cluster)

    for cluster_rows in clusters:
        first_pos = int(cluster_rows[0]["row_position"])
        last_pos = int(cluster_rows[-1]["row_position"])
        prev_locked = first_pos > 0 and bool(segment_rows[first_pos - 1]["locked"])
        next_locked = last_pos + 1 < len(segment_rows) and bool(segment_rows[last_pos + 1]["locked"])
        if not (prev_locked and next_locked):
            continue

        start_sec = float(cluster_rows[0]["start_sec"])
        end_sec = float(cluster_rows[-1]["end_sec"])
        base_metrics = _segment_timing_metrics(
            source_times,
            bridged,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if base_metrics is None:
            continue
        base_error = float(base_metrics.get("avg_abs_error_after_sec", 0.0))
        base_phase_abs = float(base_metrics.get("phase_window_abs_max_after_sec", 0.0))
        base_phase_span = float(base_metrics.get("phase_window_span_after_sec", 0.0))
        median_seed = float(base_metrics.get("median_signed_error_after_sec", 0.0))

        best_candidate: np.ndarray | None = None
        best_metrics: dict[str, float] | None = None
        best_alpha: float | None = None
        best_shift_sec = 0.0
        shift_seed = float(np.clip(-median_seed, -0.012, 0.012))
        shift_candidates = [0.0]
        if abs(shift_seed) >= 0.003:
            shift_candidates.extend([shift_seed * 0.5, shift_seed])

        for alpha in (0.72, 0.88, 1.0):
            weights = _segment_blend_weights(source_times, start_sec, end_sec, transition_sec=0.16) * float(alpha)
            if not np.any(weights > 0.0):
                continue
            blended = ((1.0 - weights) * bridged + weights * strict_target).astype(np.float32)
            for shift_sec in shift_candidates:
                candidate = blended
                if abs(shift_sec) > 1e-6:
                    candidate = (blended + weights * shift_sec).astype(np.float32)
                candidate = np.maximum.accumulate(candidate)
                candidate[-1] = target_times[-1]
                candidate_metrics = _segment_timing_metrics(
                    source_times,
                    candidate,
                    grid,
                    onsets_before,
                    start_sec,
                    end_sec,
                )
                if candidate_metrics is None:
                    continue
                candidate_error = float(candidate_metrics.get("avg_abs_error_after_sec", base_error))
                candidate_phase_abs = float(candidate_metrics.get("phase_window_abs_max_after_sec", base_phase_abs))
                candidate_phase_span = float(candidate_metrics.get("phase_window_span_after_sec", base_phase_span))
                if candidate_error + 1e-6 >= base_error:
                    continue
                if candidate_phase_abs > base_phase_abs + 0.012 or candidate_phase_span > base_phase_span + 0.02:
                    continue
                if best_metrics is None or candidate_error + 1e-6 < float(best_metrics.get("avg_abs_error_after_sec", base_error)):
                    best_candidate = candidate
                    best_metrics = candidate_metrics
                    best_alpha = float(alpha)
                    best_shift_sec = float(shift_sec)

        if best_candidate is None or best_metrics is None or best_alpha is None:
            continue

        bridged = best_candidate
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "segment_count": len(cluster_rows),
                "alpha": best_alpha,
                "shift_sec": best_shift_sec,
                "after_error_sec": base_error,
                "bridged_error_sec": float(best_metrics.get("avg_abs_error_after_sec", base_error)),
            }
        )

    return bridged, decisions


def _coverage_context_reanchor_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    refined = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    segment_rows: list[dict[str, float | bool | int]] = []

    for idx, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            refined,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        locked = after_error <= 0.06 and improvement_pct >= 0.0
        bridge_candidate = (
            after_error > 0.06
            and after_error <= 0.078
            and improvement_pct >= 8.0
            and phase_abs <= 0.03
            and phase_span <= 0.05
        )
        segment_rows.append(
            {
                "index": idx,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "locked": locked,
                "bridge_candidate": bridge_candidate,
            }
        )

    if not segment_rows:
        return refined, decisions

    clusters: list[list[dict[str, float | bool | int]]] = []
    cluster: list[dict[str, float | bool | int]] = []
    for row in segment_rows:
        if not bool(row["bridge_candidate"]):
            if cluster:
                clusters.append(cluster)
                cluster = []
            continue
        if not cluster:
            cluster = [row]
            continue
        prev = cluster[-1]
        if int(row["index"]) <= int(prev["index"]) + 1:
            cluster.append(row)
        else:
            clusters.append(cluster)
            cluster = [row]
    if cluster:
        clusters.append(cluster)

    for cluster_rows in clusters:
        first_idx = int(cluster_rows[0]["index"])
        last_idx = int(cluster_rows[-1]["index"])
        if first_idx <= 0 or last_idx + 1 >= len(segment_rows):
            continue
        if not bool(segment_rows[first_idx - 1]["locked"]) or not bool(segment_rows[last_idx + 1]["locked"]):
            continue

        context_start_sec = float(segment_rows[first_idx - 1]["start_sec"])
        context_end_sec = float(segment_rows[last_idx + 1]["end_sec"])
        left_start_sec = float(segment_rows[first_idx - 1]["start_sec"])
        left_end_sec = float(segment_rows[first_idx - 1]["end_sec"])
        right_start_sec = float(segment_rows[last_idx + 1]["start_sec"])
        right_end_sec = float(segment_rows[last_idx + 1]["end_sec"])
        core_start_sec = float(cluster_rows[0]["start_sec"])
        core_end_sec = float(cluster_rows[-1]["end_sec"])

        base_core_metrics = _segment_timing_metrics(
            source_times,
            refined,
            grid,
            onsets_before,
            core_start_sec,
            core_end_sec,
        )
        base_context_metrics = _segment_timing_metrics(
            source_times,
            refined,
            grid,
            onsets_before,
            context_start_sec,
            context_end_sec,
        )
        if base_core_metrics is None or base_context_metrics is None:
            continue

        base_core_error = float(base_core_metrics.get("avg_abs_error_after_sec", 0.0))
        base_core_phase_abs = float(base_core_metrics.get("phase_window_abs_max_after_sec", 0.0))
        base_core_phase_span = float(base_core_metrics.get("phase_window_span_after_sec", 0.0))
        base_context_error = float(base_context_metrics.get("avg_abs_error_after_sec", 0.0))

        repaired = _reanchor_cluster_target(
            source_times,
            refined,
            grid,
            onsets_before,
            context_start_sec,
            context_end_sec,
        )
        if repaired is None:
            continue

        best_candidate: np.ndarray | None = None
        best_core_metrics: dict[str, float] | None = None
        best_context_metrics: dict[str, float] | None = None
        best_strength: float | None = None
        for strength in (0.55, 0.72, 0.88, 1.0):
            weights = _segment_blend_weights(source_times, context_start_sec, context_end_sec, transition_sec=0.24) * float(strength)
            if not np.any(weights > 0.0):
                continue
            candidate = ((1.0 - weights) * refined + weights * repaired).astype(np.float32)
            candidate = np.maximum.accumulate(candidate)
            candidate[-1] = target_times[-1]
            core_metrics = _segment_timing_metrics(
                source_times,
                candidate,
                grid,
                onsets_before,
                core_start_sec,
                core_end_sec,
            )
            context_metrics = _segment_timing_metrics(
                source_times,
                candidate,
                grid,
                onsets_before,
                context_start_sec,
                context_end_sec,
            )
            if core_metrics is None or context_metrics is None:
                continue
            core_error = float(core_metrics.get("avg_abs_error_after_sec", base_core_error))
            core_phase_abs = float(core_metrics.get("phase_window_abs_max_after_sec", base_core_phase_abs))
            core_phase_span = float(core_metrics.get("phase_window_span_after_sec", base_core_phase_span))
            context_error = float(context_metrics.get("avg_abs_error_after_sec", base_context_error))
            if core_error + 1e-6 >= base_core_error:
                continue
            if context_error > base_context_error + 0.004:
                continue
            if core_phase_abs > base_core_phase_abs + 0.012 or core_phase_span > base_core_phase_span + 0.02:
                continue
            if best_core_metrics is None or core_error + 1e-6 < float(best_core_metrics.get("avg_abs_error_after_sec", base_core_error)):
                best_candidate = candidate
                best_core_metrics = core_metrics
                best_context_metrics = context_metrics
                best_strength = float(strength)

        if best_candidate is None or best_core_metrics is None or best_context_metrics is None or best_strength is None:
            continue

        refined = best_candidate
        decisions.append(
            {
                "context_start_sec": context_start_sec,
                "context_end_sec": context_end_sec,
                "core_start_sec": core_start_sec,
                "core_end_sec": core_end_sec,
                "segment_count": len(cluster_rows),
                "strength": best_strength,
                "core_error_sec": base_core_error,
                "context_error_sec": base_context_error,
                "refined_core_error_sec": float(best_core_metrics.get("avg_abs_error_after_sec", base_core_error)),
                "refined_context_error_sec": float(best_context_metrics.get("avg_abs_error_after_sec", base_context_error)),
            }
        )

    return refined, decisions


def _coverage_replace_target(
    source_times: np.ndarray,
    target_times: np.ndarray,
    strict_target: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    replaced = target_times.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []
    segment_rows: list[dict[str, float | bool | int]] = []

    for idx, segment in enumerate(segments):
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        metrics = _segment_timing_metrics(
            source_times,
            replaced,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if metrics is None:
            continue
        after_error = float(metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(metrics.get("improvement_pct", 0.0))
        phase_abs = float(metrics.get("phase_window_abs_max_after_sec", 0.0))
        phase_span = float(metrics.get("phase_window_span_after_sec", 0.0))
        locked = after_error <= 0.06 and improvement_pct >= 0.0
        replace_candidate = (
            after_error > 0.06
            and after_error <= 0.076
            and improvement_pct >= 10.0
            and phase_abs <= 0.025
            and phase_span <= 0.04
        )
        segment_rows.append(
            {
                "index": idx,
                "row_position": len(segment_rows),
                "start_sec": start_sec,
                "end_sec": end_sec,
                "locked": locked,
                "replace_candidate": replace_candidate,
            }
        )

    if not segment_rows:
        return replaced, decisions

    clusters: list[list[dict[str, float | bool | int]]] = []
    cluster: list[dict[str, float | bool | int]] = []
    for row in segment_rows:
        if not bool(row["replace_candidate"]):
            if cluster:
                clusters.append(cluster)
                cluster = []
            continue
        if not cluster:
            cluster = [row]
            continue
        prev = cluster[-1]
        if int(row["index"]) <= int(prev["index"]) + 1:
            cluster.append(row)
        else:
            clusters.append(cluster)
            cluster = [row]
    if cluster:
        clusters.append(cluster)

    for cluster_rows in clusters:
        first_pos = int(cluster_rows[0]["row_position"])
        last_pos = int(cluster_rows[-1]["row_position"])
        prev_locked = first_pos > 0 and bool(segment_rows[first_pos - 1]["locked"])
        next_locked = last_pos + 1 < len(segment_rows) and bool(segment_rows[last_pos + 1]["locked"])
        if not (prev_locked and next_locked):
            continue

        core_start_sec = float(cluster_rows[0]["start_sec"])
        core_end_sec = float(cluster_rows[-1]["end_sec"])
        context_start_sec = float(segment_rows[first_pos - 1]["start_sec"])
        context_end_sec = float(segment_rows[last_pos + 1]["end_sec"])
        left_start_sec = float(segment_rows[first_pos - 1]["start_sec"])
        left_end_sec = float(segment_rows[first_pos - 1]["end_sec"])
        right_start_sec = float(segment_rows[last_pos + 1]["start_sec"])
        right_end_sec = float(segment_rows[last_pos + 1]["end_sec"])

        base_core_metrics = _segment_timing_metrics(
            source_times,
            replaced,
            grid,
            onsets_before,
            core_start_sec,
            core_end_sec,
        )
        base_context_metrics = _segment_timing_metrics(
            source_times,
            replaced,
            grid,
            onsets_before,
            context_start_sec,
            context_end_sec,
        )
        base_left_metrics = _segment_timing_metrics(
            source_times,
            replaced,
            grid,
            onsets_before,
            left_start_sec,
            left_end_sec,
        )
        base_right_metrics = _segment_timing_metrics(
            source_times,
            replaced,
            grid,
            onsets_before,
            right_start_sec,
            right_end_sec,
        )
        if base_core_metrics is None or base_context_metrics is None or base_left_metrics is None or base_right_metrics is None:
            continue

        base_core_error = float(base_core_metrics.get("avg_abs_error_after_sec", 0.0))
        base_core_phase_abs = float(base_core_metrics.get("phase_window_abs_max_after_sec", 0.0))
        base_core_phase_span = float(base_core_metrics.get("phase_window_span_after_sec", 0.0))
        base_context_error = float(base_context_metrics.get("avg_abs_error_after_sec", 0.0))
        base_left_error = float(base_left_metrics.get("avg_abs_error_after_sec", 0.0))
        base_right_error = float(base_right_metrics.get("avg_abs_error_after_sec", 0.0))

        weights = _segment_blend_weights(source_times, core_start_sec, core_end_sec, transition_sec=0.1)
        if not np.any(weights > 0.0):
            continue
        candidate = ((1.0 - weights) * replaced + weights * strict_target).astype(np.float32)
        candidate = np.maximum.accumulate(candidate)
        candidate[-1] = target_times[-1]

        core_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            core_start_sec,
            core_end_sec,
        )
        context_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            context_start_sec,
            context_end_sec,
        )
        left_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            left_start_sec,
            left_end_sec,
        )
        right_metrics = _segment_timing_metrics(
            source_times,
            candidate,
            grid,
            onsets_before,
            right_start_sec,
            right_end_sec,
        )
        if core_metrics is None or context_metrics is None or left_metrics is None or right_metrics is None:
            continue

        core_error = float(core_metrics.get("avg_abs_error_after_sec", base_core_error))
        core_phase_abs = float(core_metrics.get("phase_window_abs_max_after_sec", base_core_phase_abs))
        core_phase_span = float(core_metrics.get("phase_window_span_after_sec", base_core_phase_span))
        context_error = float(context_metrics.get("avg_abs_error_after_sec", base_context_error))
        left_error = float(left_metrics.get("avg_abs_error_after_sec", base_left_error))
        right_error = float(right_metrics.get("avg_abs_error_after_sec", base_right_error))
        if core_error >= 0.0605:
            continue
        if core_error + 0.001 >= base_core_error:
            continue
        if context_error > base_context_error + 0.004:
            continue
        if left_error > base_left_error + 0.003 or right_error > base_right_error + 0.003:
            continue
        if core_phase_abs > base_core_phase_abs + 0.012 or core_phase_span > base_core_phase_span + 0.02:
            continue

        replaced = candidate
        decisions.append(
            {
                "context_start_sec": context_start_sec,
                "context_end_sec": context_end_sec,
                "core_start_sec": core_start_sec,
                "core_end_sec": core_end_sec,
                "segment_count": len(cluster_rows),
                "core_error_sec": base_core_error,
                "context_error_sec": base_context_error,
                "left_error_sec": base_left_error,
                "right_error_sec": base_right_error,
                "replaced_core_error_sec": core_error,
                "replaced_context_error_sec": context_error,
                "replaced_left_error_sec": left_error,
                "replaced_right_error_sec": right_error,
            }
        )

    return replaced, decisions


def _build_segmented_hybrid_target(
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    ranked_hybrid: list[tuple[np.ndarray, dict, float, dict]],
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
) -> tuple[np.ndarray, list[dict[str, object]]]:
    segmented_target = baseline_target.astype(np.float32, copy=True)
    decisions: list[dict[str, object]] = []

    for segment in segments:
        start_sec = float(segment["start_sec"])
        end_sec = float(segment["end_sec"])
        baseline_metrics = _segment_timing_metrics(
            source_times,
            baseline_target,
            grid,
            onsets_before,
            start_sec,
            end_sec,
        )
        if baseline_metrics is None:
            continue

        best_target = baseline_target
        best_metrics = baseline_metrics
        best_alpha = 0.0
        best_info: dict[str, object] = {
            "candidate_name": "baseline",
            "model_file": "",
            "confidence": 0.0,
            "accelerator": "baseline",
        }
        for candidate_target, _, candidate_alpha, candidate_info in ranked_hybrid:
            candidate_metrics = _segment_timing_metrics(
                source_times,
                candidate_target,
                grid,
                onsets_before,
                start_sec,
                end_sec,
            )
            if candidate_metrics is None:
                continue
            if float(candidate_metrics["avg_abs_error_after_sec"]) + 1e-6 < float(best_metrics["avg_abs_error_after_sec"]):
                best_target = candidate_target
                best_metrics = candidate_metrics
                best_alpha = candidate_alpha
                best_info = candidate_info

        segment_gain = float(baseline_metrics["avg_abs_error_after_sec"]) - float(best_metrics["avg_abs_error_after_sec"])
        decisions.append(
            {
                "start_sec": start_sec,
                "end_sec": end_sec,
                "baseline_error_after_sec": float(baseline_metrics["avg_abs_error_after_sec"]),
                "selected_error_after_sec": float(best_metrics["avg_abs_error_after_sec"]),
                "gain_sec": segment_gain,
                "selected_alpha": float(best_alpha),
                "candidate_name": str(best_info.get("candidate_name", "baseline")),
                "model_file": str(best_info.get("model_file", "")),
            }
        )
        if best_target is baseline_target:
            continue
        weights = _segment_blend_weights(source_times, start_sec, end_sec)
        segmented_target = ((1.0 - weights) * segmented_target + weights * best_target).astype(np.float32)

    segmented_target = np.maximum.accumulate(segmented_target)
    segmented_target[-1] = baseline_target[-1]
    return segmented_target, decisions


def _search_hybrid_candidate(
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    ml_curve: dict,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    base_alpha: float,
) -> tuple[np.ndarray, dict, float]:
    ml_target_interp = np.interp(source_times, ml_curve["source_times"], ml_curve["target_times"])
    ml_target_interp = np.maximum.accumulate(ml_target_interp)

    best_target: np.ndarray | None = None
    best_metrics: dict | None = None
    best_alpha = 0.0

    for alpha in _hybrid_alpha_candidates(base_alpha):
        candidate_target = (1.0 - alpha) * baseline_target + alpha * ml_target_interp
        candidate_target = np.maximum.accumulate(candidate_target)
        candidate_target[-1] = baseline_target[-1]
        candidate_metrics = _candidate_metrics_fast(
            source_times,
            candidate_target.astype(np.float32),
            grid,
            onsets_before,
        )
        if best_metrics is None or float(candidate_metrics["avg_abs_error_after_sec"]) < float(best_metrics["avg_abs_error_after_sec"]):
            best_target = candidate_target.astype(np.float32)
            best_metrics = candidate_metrics
            best_alpha = alpha

    if best_target is None or best_metrics is None:
        raise RuntimeError("No hybrid candidate generated")
    return best_target, best_metrics, best_alpha


def _search_best_hybrid_candidate(
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    ml_curves: list[dict],
    grid: np.ndarray,
    onsets_before: np.ndarray,
    base_alpha: float,
) -> tuple[np.ndarray, dict, float, dict]:
    best_result: tuple[np.ndarray, dict, float, dict] | None = None
    for ml_curve in ml_curves:
        candidate_target, candidate_metrics, candidate_alpha = _search_hybrid_candidate(
            source_times,
            baseline_target,
            ml_curve,
            grid,
            onsets_before,
            base_alpha,
        )
        candidate_info = {
            "candidate_name": str(ml_curve.get("candidate_name") or ml_curve.get("model_file") or "ml"),
            "model_file": str(ml_curve.get("model_file", "")),
            "confidence": float(ml_curve.get("confidence", 0.0)),
            "accelerator": str(ml_curve.get("accelerator", "torch")),
            "routing_weights": ml_curve.get("routing_weights"),
            "routing_percussive_ratio": ml_curve.get("routing_percussive_ratio"),
        }
        if best_result is None or float(candidate_metrics["avg_abs_error_after_sec"]) < float(best_result[1]["avg_abs_error_after_sec"]):
            best_result = (candidate_target, candidate_metrics, candidate_alpha, candidate_info)
    if best_result is None:
        raise RuntimeError("No hybrid candidate generated")
    return best_result


def _rank_hybrid_candidates(
    source_times: np.ndarray,
    baseline_target: np.ndarray,
    ml_curves: list[dict],
    grid: np.ndarray,
    onsets_before: np.ndarray,
    base_alpha: float,
    top_k: int = 2,
) -> list[tuple[np.ndarray, dict, float, dict]]:
    ranked: list[tuple[np.ndarray, dict, float, dict]] = []
    selected_keys: set[tuple[str, float]] = set()
    selected: list[tuple[np.ndarray, dict, float, dict]] = []
    for ml_curve in ml_curves:
        ml_target_interp = np.interp(source_times, ml_curve["source_times"], ml_curve["target_times"])
        ml_target_interp = np.maximum.accumulate(ml_target_interp)
        curve_entries: list[tuple[np.ndarray, dict, float, dict]] = []
        _, curve_alpha_seed, _ = _blend_ml_with_baseline(source_times, baseline_target, ml_curve, grid)
        for alpha in _hybrid_alpha_candidates(base_alpha):
            candidate_target = (1.0 - alpha) * baseline_target + alpha * ml_target_interp
            candidate_target = np.maximum.accumulate(candidate_target)
            candidate_target[-1] = baseline_target[-1]
            candidate_metrics = _candidate_metrics_fast(
                source_times,
                candidate_target.astype(np.float32),
                grid,
                onsets_before,
            )
            candidate_info = {
                "candidate_name": str(ml_curve.get("candidate_name") or ml_curve.get("model_file") or "ml"),
                "model_file": str(ml_curve.get("model_file", "")),
                "confidence": float(ml_curve.get("confidence", 0.0)),
                "accelerator": str(ml_curve.get("accelerator", "torch")),
                "routing_weights": ml_curve.get("routing_weights"),
                "routing_percussive_ratio": ml_curve.get("routing_percussive_ratio"),
            }
            entry = (candidate_target.astype(np.float32), candidate_metrics, alpha, candidate_info)
            ranked.append(entry)
            curve_entries.append(entry)
        direct_target = ml_target_interp.astype(np.float32)
        direct_target[-1] = baseline_target[-1]
        direct_metrics = _candidate_metrics_fast(
            source_times,
            direct_target,
            grid,
            onsets_before,
        )
        direct_info = {
            "candidate_name": str(ml_curve.get("candidate_name") or ml_curve.get("model_file") or "ml"),
            "model_file": str(ml_curve.get("model_file", "")),
            "confidence": float(ml_curve.get("confidence", 0.0)),
            "accelerator": str(ml_curve.get("accelerator", "torch")),
            "routing_weights": ml_curve.get("routing_weights"),
            "routing_percussive_ratio": ml_curve.get("routing_percussive_ratio"),
            "candidate_mode": "direct_ml",
        }
        direct_entry = (direct_target, direct_metrics, 1.0, direct_info)
        ranked.append(direct_entry)
        curve_entries.append(direct_entry)
        curve_entries.sort(key=lambda item: (float(item[1]["avg_abs_error_after_sec"]), float(item[2])))
        if curve_entries:
            best_entry = curve_entries[0]
            best_key = (str(best_entry[3]["candidate_name"]), round(float(best_entry[2]), 6))
            if best_key not in selected_keys:
                selected_keys.add(best_key)
                selected.append(best_entry)
            seed_entry = min(curve_entries, key=lambda item: abs(float(item[2]) - float(curve_alpha_seed)))
            seed_key = (str(seed_entry[3]["candidate_name"]), round(float(seed_entry[2]), 6))
            if seed_key not in selected_keys:
                selected_keys.add(seed_key)
                selected.append(seed_entry)
    ranked.sort(key=lambda item: (float(item[1]["avg_abs_error_after_sec"]), float(item[2])))
    if ranked:
        best_error = float(ranked[0][1]["avg_abs_error_after_sec"])
        close_margin = max(5e-4, best_error * 0.02)
        ranked = [
            entry
            for entry in ranked
            if float(entry[1]["avg_abs_error_after_sec"]) <= best_error + close_margin
        ]
    for entry in ranked:
        key = (str(entry[3]["candidate_name"]), round(float(entry[2]), 6))
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected.append(entry)
        if len(selected) >= max(1, len(ml_curves) + top_k):
            break
    return selected


def _hybrid_verify_top_k(default: int = 3) -> int:
    raw = os.environ.get("BOXBOX_HYBRID_VERIFY_TOPK", "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def _hybrid_search_curves(ml_curves: list[dict]) -> list[dict]:
    strategy = os.environ.get("BOXBOX_HYBRID_SEARCH_STRATEGY", "core4").strip().lower()
    if strategy in {"", "all"}:
        return ml_curves

    routed = [curve for curve in ml_curves if str(curve.get("candidate_name", "")).lower() == "routed"]
    if strategy == "routed":
        return routed or ml_curves

    if strategy in {"core4", "core5"}:
        core_names = {
            "routed",
            "boxbox_latest",
            "boxbox_mixed_legacy_candidate_r1884",
            "boxbox_before_groove_860",
        }
        if strategy == "core5":
            core_names.add("boxbox_legacy_specialist_r1730")
        selected = [
            curve
            for curve in ml_curves
            if str(curve.get("candidate_name", "")).lower() in core_names
        ]
        return selected or ml_curves

    if strategy.startswith("routed_top"):
        suffix = strategy.removeprefix("routed_top")
        try:
            top_n = max(0, int(suffix))
        except ValueError:
            return ml_curves
        selected: list[dict] = []
        seen: set[int] = set()
        for curve in routed:
            selected.append(curve)
            seen.add(id(curve))
        non_routed = [curve for curve in ml_curves if id(curve) not in seen]
        non_routed.sort(key=lambda curve: float(curve.get("confidence", 0.0)), reverse=True)
        selected.extend(non_routed[:top_n])
        return selected or ml_curves

    return ml_curves


def _runtime_config_report() -> dict[str, object]:
    return {
        "inference_accelerator": preferred_inference_accelerator(),
        "inference_candidate_strategy": preferred_inference_candidate_strategy(),
        "hybrid_search_strategy": os.environ.get("BOXBOX_HYBRID_SEARCH_STRATEGY", "core4").strip().lower() or "core4",
        "hybrid_verify_top_k": _hybrid_verify_top_k(),
    }


def _should_skip_hybrid_search(
    duration_sec: float,
    ml_blend_alpha: float,
    ml_blend_diagnostics: dict[str, float],
) -> bool:
    if duration_sec < 45.0:
        return False
    agreement_scale = float(ml_blend_diagnostics.get("ml_agreement_scale", 1.0))
    disagreement_ratio = float(ml_blend_diagnostics.get("ml_baseline_disagreement_ratio", 0.0))
    if ml_blend_alpha <= 1e-6:
        return True
    if agreement_scale <= 0.1:
        return True
    return disagreement_ratio >= 1.5


def _hybrid_inference_skip_reason(
    request_mode: str,
    duration_sec: float,
    event_summary: dict[str, object],
    style_profile: dict[str, object],
) -> str | None:
    if request_mode != "hybrid":
        return None
    if duration_sec < 180.0:
        return None
    event_density = float(event_summary.get("event_density_per_sec", 0.0))
    strong_event_count = int(event_summary.get("strong_event_count", 0))
    profile = str(style_profile.get("profile", "balanced"))
    confidence = float(style_profile.get("confidence", 0.0))
    features = dict(style_profile.get("features") or {})
    strong_event_ratio = float(features.get("strong_event_ratio", 0.0))
    mixed_ratio = float(features.get("mixed_ratio", 0.0))
    harmonic_ratio = float(features.get("harmonic_ratio", 0.0))
    drift_pct = float(features.get("mean_abs_drift_pct", 999.0))
    prefer_segmented_hybrid = bool(style_profile.get("prefer_segmented_hybrid"))

    if event_density < 0.8 or strong_event_count < 160:
        return None
    if confidence < 0.7:
        return None
    if prefer_segmented_hybrid:
        return None
    if profile not in {"balanced", "percussive"}:
        return None
    if strong_event_ratio < 0.8 or mixed_ratio < 0.45:
        return None
    if harmonic_ratio > 0.35:
        return None
    if drift_pct > 8.0:
        return None
    return "strict_lock_dense_long_track"


def _hybrid_baseline_grid_skip_reason(
    request_mode: str,
    source_times: np.ndarray,
    target_times: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
    duration_sec: float,
    target_bpm: float,
    resolution: int,
) -> tuple[str | None, dict[str, object]]:
    if request_mode != "hybrid" or duration_sec < 45.0:
        return None, {}
    metrics = _candidate_metrics_fast(source_times, target_times, grid, onsets_before)
    fixed = _summarize_fixed_window_lock(source_times, target_times, grid, onsets_before, duration_sec, target_bpm)
    segment_summaries = _summarize_segments(segments, source_times, target_times, target_times, grid, onsets_before)
    lock = _summarize_metronome_lock(metrics, segment_summaries)
    continuity = _summarize_warp_continuity(source_times, target_times, target_bpm, resolution)
    fixed_ratio = float(fixed.get("effective_locked_ratio", fixed.get("locked_ratio", 0.0)) or 0.0)
    segment_ratio = float(lock.get("effective_locked_ratio", lock.get("locked_ratio", 0.0)) or 0.0)
    raw_avg_error = metrics.get("avg_abs_error_after_sec", 999.0)
    avg_error = float(999.0 if raw_avg_error is None else raw_avg_error)
    diagnostics = {
        "avg_abs_error_after_sec": avg_error,
        "median_signed_error_after_sec": float(metrics.get("median_signed_error_after_sec", 999.0) or 999.0),
        "phase_window_abs_max_after_sec": float(metrics.get("phase_window_abs_max_after_sec", 999.0) or 999.0),
        "phase_window_span_after_sec": float(metrics.get("phase_window_span_after_sec", 999.0) or 999.0),
        "fixed_locked_ratio": fixed_ratio,
        "segment_locked_ratio": segment_ratio,
        "fixed_unstable_windows": int(fixed.get("unstable_windows", 0) or 0),
        "fixed_meltdown_windows": int(fixed.get("meltdown_windows", 0) or 0),
        "continuity_verdict": str(continuity.get("verdict", "unknown")),
        "continuity_subdivision_alias_jump": bool(continuity.get("subdivision_alias_jump", False)),
        "continuity_max_window_offset_jump_sec": float(continuity.get("max_window_offset_jump_sec", 0.0) or 0.0),
        "continuity_grid_step_sec": float(continuity.get("grid_step_sec", 0.0) or 0.0),
    }
    continuity_verdict = str(continuity.get("verdict", ""))
    has_fixed_instability = int(fixed.get("unstable_windows", 0) or 0) > 0 or int(fixed.get("meltdown_windows", 0) or 0) > 0
    if (
        avg_error <= 0.04
        and fixed_ratio >= 0.999
        and not has_fixed_instability
        and segment_ratio >= 0.95
        and continuity_verdict != "jump_risk"
    ):
        return "baseline_grid_strong_precheck", diagnostics

    phase_abs_max = float(diagnostics["phase_window_abs_max_after_sec"])
    phase_span = float(diagnostics["phase_window_span_after_sec"])
    median_signed_error = abs(float(diagnostics["median_signed_error_after_sec"]))
    subdivision_alias_jump = bool(diagnostics["continuity_subdivision_alias_jump"])
    if (
        duration_sec >= 180.0
        and avg_error <= 0.035
        and fixed_ratio >= 0.999
        and not has_fixed_instability
        and segment_ratio >= 0.7
        and continuity_verdict == "continuous"
        and phase_abs_max <= 0.05
        and phase_span <= 0.07
    ):
        return "baseline_grid_sparse_authoritative_precheck", diagnostics
    if (
        duration_sec >= 180.0
        and avg_error <= 0.035
        and fixed_ratio >= 0.96
        and segment_ratio >= 0.92
        and not has_fixed_instability
        and continuity_verdict == "jump_risk"
        and subdivision_alias_jump
        and phase_abs_max <= 0.05
        and phase_span <= 0.07
        and median_signed_error <= 0.03
    ):
        return "baseline_grid_subdivision_alias_precheck", diagnostics
    if (
        duration_sec >= 180.0
        and avg_error <= 0.035
        and fixed_ratio >= 0.96
        and segment_ratio >= 0.97
        and not has_fixed_instability
        and continuity_verdict != "jump_risk"
        and subdivision_alias_jump
        and phase_abs_max <= 0.05
        and phase_span <= 0.07
        and median_signed_error <= 0.03
    ):
        return "baseline_grid_smoothed_alias_precheck", diagnostics
    if (
        duration_sec >= 180.0
        and avg_error <= 0.043
        and fixed_ratio >= 0.999
        and segment_ratio >= 0.999
        and not has_fixed_instability
        and continuity_verdict in {"watch", "subdivision_alias", "continuous"}
        and phase_abs_max <= 0.05
        and phase_span <= 0.07
        and median_signed_error <= 0.03
    ):
        return "baseline_grid_repaired_authoritative_precheck", diagnostics
    if duration_sec < 180.0:
        return None, diagnostics
    if avg_error > 0.042:
        return None, diagnostics
    if fixed_ratio < 0.97 or segment_ratio < 0.97:
        return None, diagnostics
    if has_fixed_instability:
        return None, diagnostics
    if continuity_verdict == "jump_risk":
        return None, diagnostics
    if phase_abs_max > 0.06 or phase_span > 0.09:
        return None, diagnostics
    if median_signed_error > 0.03:
        return None, diagnostics
    return "baseline_grid_mostly_strong_precheck", diagnostics


def _build_baseline_candidate(
    audio: np.ndarray,
    sr: int,
    source_times: np.ndarray,
    base_target_times: np.ndarray,
    style_guided_groove_preserve: int,
    request_groove_preserve: int,
    strict_first_pass_floor: int,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    segments: list[dict[str, float]],
    target_bpm: float,
    resolution: int,
    strict_lock_fast_path: bool,
) -> tuple[np.ndarray, int, dict[str, dict], np.ndarray | None, int, np.ndarray | None]:
    candidate_metrics: dict[str, dict] = {}
    if strict_lock_fast_path:
        baseline_target = base_target_times.astype(np.float32, copy=True)
        baseline_groove_preserve = style_guided_groove_preserve
        candidate_metrics["baseline_skipped"] = {"reason": "strict_lock_dense_long_track"}
        return baseline_target, baseline_groove_preserve, candidate_metrics, None, 0, None

    baseline_target, baseline_groove_preserve, _ = _optimize_groove_target(
        source_times,
        base_target_times,
        style_guided_groove_preserve,
        grid,
        onsets_before,
        segments,
        allow_safer=False,
        max_preserve=request_groove_preserve,
        min_preserve=strict_first_pass_floor,
    )
    baseline_target, baseline_stability_decisions = _stabilize_baseline_target(
        source_times,
        baseline_target,
        grid,
        onsets_before,
        segments,
        target_bpm,
        resolution,
    )
    baseline_metrics = _candidate_metrics_fast(source_times, baseline_target, grid, onsets_before)
    candidate_metrics["baseline"] = baseline_metrics
    if baseline_stability_decisions:
        candidate_metrics["baseline_stability"] = {"retreated_segments": baseline_stability_decisions}
    proxy_audio, proxy_sr, onsets_before_proxy = _selection_proxy(audio, sr)
    return baseline_target, baseline_groove_preserve, candidate_metrics, proxy_audio, proxy_sr, onsets_before_proxy


def _select_non_ml_target(
    request_mode: str,
    ml_used: bool,
    strict_lock_fast_path: bool,
    hybrid_skip_reason: str | None,
    baseline_target: np.ndarray,
    baseline_metrics: dict[str, object],
    onset_grid_target: np.ndarray,
    final_target: np.ndarray,
    final_source: np.ndarray,
    grid: np.ndarray,
    onsets_before: np.ndarray,
    candidate_metrics: dict[str, dict],
    baseline_groove_preserve: int,
) -> tuple[np.ndarray, dict[str, object], str, int]:
    if request_mode == "ml" and not ml_used:
        return baseline_target, baseline_metrics, "baseline", baseline_groove_preserve
    if request_mode == "hybrid" and not ml_used:
        if strict_lock_fast_path:
            strict_target = onset_grid_target.astype(np.float32, copy=True)
            strict_metrics = _candidate_metrics_fast(final_source, strict_target, grid, onsets_before)
            candidate_metrics["requested_mode_projected"] = strict_metrics
            return strict_target, strict_metrics, "strict_onset_grid", baseline_groove_preserve
        if hybrid_skip_reason and hybrid_skip_reason != "ml_disagreement_too_high_for_long_track":
            return baseline_target, baseline_metrics, "baseline", baseline_groove_preserve

    requested_metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
    candidate_metrics["requested_mode"] = requested_metrics
    return final_target, requested_metrics, "baseline", baseline_groove_preserve


def _compute_pipeline(job_id: str, request: QuantizeRequest) -> dict:
    pipeline_start = time.perf_counter()
    stage_start = pipeline_start
    processing_timing: dict[str, object] = {"stages": {}}

    def finish_stage(stage_name: str) -> None:
        nonlocal stage_start
        now = time.perf_counter()
        stages = processing_timing["stages"]
        if isinstance(stages, dict):
            stages[stage_name] = round(float(now - stage_start), 4)
        stage_start = now

    upload_dir = UPLOADS_DIR / job_id
    output_dir = OUTPUTS_DIR / job_id
    input_path = upload_dir / "input.wav"
    upload_state = read_json(upload_dir / "job.json")
    source_meta = upload_state.get("source_meta", {})

    if not input_path.exists():
        raise FileNotFoundError(f"Input for job '{job_id}' not found")

    _update_job(job_id, "processing", "load_audio", 0.05, "Loading input audio")
    audio, sr = load_audio(input_path)
    mono = mixdown_mono(audio)
    finish_stage("load_audio")

    _update_job(job_id, "processing", "analyze", 0.15, "Extracting audio features")
    feat = extract_features(mono, sr, include_mel=False)
    times = feat["times"]
    duration_sec = float(len(mono) / sr)
    resolution = _normalize_resolution(request.resolution)
    try:
        _, beat_frames = librosa.beat.beat_track(
            onset_envelope=feat["onset"],
            sr=sr,
            hop_length=int(feat["hop_length"]),
            start_bpm=float(request.target_bpm),
            tightness=240.0,
            trim=False,
            units="frames",
        )
        beat_phase_source_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=int(feat["hop_length"])).astype(np.float32)
    except Exception:
        beat_phase_source_times = np.array([], dtype=np.float32)
    finish_stage("extract_features")

    _update_job(job_id, "processing", "segment", 0.25, "Finding musical segments")
    boundaries = find_segments(feat["novelty"], times)
    segments = [
        {"start_sec": float(boundaries[i]), "end_sec": float(boundaries[i + 1])}
        for i in range(max(0, len(boundaries) - 1))
    ]
    detected_events = detect_events(mono, sr, features=feat)
    event_summary = summarize_events(detected_events, duration_sec)
    tempo_drift = estimate_tempo_drift(detected_events, request.target_bpm)
    style_profile = infer_style_profile(event_summary, tempo_drift["summary"])
    learned_behavior = summarize_feedback_memory(
        read_json(_feedback_memory_path, default={}),
        str(style_profile.get("profile", "balanced")),
        request.groove_preserve,
    )
    style_guided_groove_preserve = _style_guided_groove_request(
        _learned_groove_request(request.groove_preserve, learned_behavior),
        style_profile,
    )
    style_guided_groove_preserve = _strict_lock_groove_request(
        request.groove_preserve,
        style_guided_groove_preserve,
        duration_sec=duration_sec,
        event_summary=event_summary,
    )
    strict_first_pass_floor = 20 if (
        duration_sec >= 45.0
        and float(event_summary.get("event_density_per_sec", 0.0)) >= 0.45
        and int(event_summary.get("strong_event_count", 0)) >= 48
    ) else 0
    prefer_segmented_hybrid = _should_prefer_segmented_hybrid(style_profile)
    finish_stage("segment_and_style")

    _update_job(job_id, "processing", "grid", 0.35, "Building quantization grid")
    # DAW-lock export must target one fixed whole-BPM ruler; drift windows are diagnostics, not the output grid.
    grid = build_grid(duration_sec, request.target_bpm, resolution)
    segments = _refine_segments_for_strict_lock(segments, grid, duration_sec, event_summary)
    finish_stage("build_grid")

    _update_job(job_id, "processing", "dtw", 0.5, "Computing quantization warp curve")
    base_curve = onset_quantize_curve_from_features(
        feat["onset"],
        feat["hop_length"],
        sr,
        duration_sec,
        request.target_bpm,
        resolution,
    )
    final_source = base_curve["source_times"]
    final_target = base_curve["target_times"]
    ml_blend_alpha = 0.0
    ml_blend_diagnostics: dict[str, float] = {}
    grid = base_curve["grid_times"]
    warp_selection = "baseline"
    candidate_metrics: dict[str, dict] = {}

    ml_used = False
    ml_confidence = 0.0
    ml_curves: list[dict] = []
    ml_candidate_info: dict[str, object] = {}
    model_available = model_exists(MODELS_DIR)
    effective_groove_preserve = int(request.groove_preserve)
    hybrid_inference_skip_reason = _hybrid_inference_skip_reason(
        request.mode,
        duration_sec,
        event_summary,
        style_profile,
    )
    strict_lock_fast_path = hybrid_inference_skip_reason == "strict_lock_dense_long_track"
    finish_stage("build_onset_curve")

    onsets_before = detect_onsets_from_envelope(feat["onset"], int(feat["hop_length"]), sr, units="time")
    baseline_target, baseline_groove_preserve, baseline_candidate_metrics, proxy_audio, proxy_sr, onsets_before_proxy = _build_baseline_candidate(
        audio,
        sr,
        final_source,
        base_curve["target_times"],
        style_guided_groove_preserve,
        request.groove_preserve,
        strict_first_pass_floor,
        grid,
        onsets_before,
        segments,
        request.target_bpm,
        resolution,
        strict_lock_fast_path,
    )
    candidate_metrics.update(baseline_candidate_metrics)
    baseline_metrics = candidate_metrics.get("baseline") or _candidate_metrics_fast(final_source, baseline_target, grid, onsets_before)
    candidate_metrics["baseline"] = baseline_metrics
    effective_groove_preserve = baseline_groove_preserve
    finish_stage("baseline_candidate")

    if hybrid_inference_skip_reason is None:
        raw_precheck_reason, raw_precheck_diagnostics = _hybrid_baseline_grid_skip_reason(
            request.mode,
            final_source,
            base_curve["target_times"],
            grid,
            onsets_before,
            segments,
            duration_sec,
            request.target_bpm,
            resolution,
        )
        precheck_reason, precheck_diagnostics = _hybrid_baseline_grid_skip_reason(
            request.mode,
            final_source,
            baseline_target,
            grid,
            onsets_before,
            segments,
            duration_sec,
            request.target_bpm,
            resolution,
        )
        if raw_precheck_diagnostics:
            candidate_metrics["hybrid_precheck_raw"] = dict(raw_precheck_diagnostics)
        if precheck_diagnostics:
            candidate_metrics["hybrid_precheck"] = dict(precheck_diagnostics)
        chosen_reason = precheck_reason
        chosen_diagnostics = precheck_diagnostics
        continuity_smooth_precheck_target = baseline_target
        repair_precheck_target = baseline_target
        repair_precheck_diagnostics = precheck_diagnostics
        if raw_precheck_reason is not None and precheck_reason is None:
            chosen_reason = raw_precheck_reason
            chosen_diagnostics = raw_precheck_diagnostics
            continuity_smooth_precheck_target = base_curve["target_times"]
        if raw_precheck_diagnostics:
            raw_avg = float(raw_precheck_diagnostics.get("avg_abs_error_after_sec", 999.0) or 999.0)
            raw_fixed = float(raw_precheck_diagnostics.get("fixed_locked_ratio", 0.0) or 0.0)
            raw_segment = float(raw_precheck_diagnostics.get("segment_locked_ratio", 0.0) or 0.0)
            baseline_avg = float(precheck_diagnostics.get("avg_abs_error_after_sec", 999.0) or 999.0)
            if raw_avg + 0.003 < baseline_avg and raw_fixed >= 0.93 and raw_segment >= 0.95:
                repair_precheck_target = base_curve["target_times"]
                repair_precheck_diagnostics = raw_precheck_diagnostics
        if chosen_reason is not None:
            hybrid_inference_skip_reason = chosen_reason
            candidate_metrics["hybrid_skipped"] = {
                "reason": chosen_reason,
                "duration_sec": duration_sec,
                **chosen_diagnostics,
            }
        elif (
            request.mode == "hybrid"
            and duration_sec >= 180.0
            and float(repair_precheck_diagnostics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.047
            and float(repair_precheck_diagnostics.get("fixed_locked_ratio", 0.0) or 0.0) >= 0.93
            and float(repair_precheck_diagnostics.get("segment_locked_ratio", 0.0) or 0.0) >= 0.94
            and int(repair_precheck_diagnostics.get("fixed_unstable_windows", 0) or 0) == 0
            and int(repair_precheck_diagnostics.get("fixed_meltdown_windows", 0) or 0) == 0
            and (
                str(repair_precheck_diagnostics.get("continuity_verdict", "")) != "jump_risk"
                or bool(repair_precheck_diagnostics.get("continuity_subdivision_alias_jump", False))
            )
        ):
            repaired_baseline_target, repaired_windows, repaired_diagnostics = _fixed_window_repair_target(
                final_source,
                repair_precheck_target,
                grid,
                onsets_before,
                duration_sec,
                request.target_bpm,
            )
            if repaired_windows:
                repaired_reason, repaired_precheck_diagnostics = _hybrid_baseline_grid_skip_reason(
                    request.mode,
                    final_source,
                    repaired_baseline_target,
                    grid,
                    onsets_before,
                    segments,
                    duration_sec,
                    request.target_bpm,
                    resolution,
                )
                candidate_metrics["hybrid_precheck_repaired"] = {
                    "windows": repaired_windows,
                    "diagnostics": repaired_precheck_diagnostics,
                }
                continuity_smooth_precheck_target = repaired_baseline_target
                if repaired_reason is not None:
                    baseline_target = repaired_baseline_target
                    baseline_metrics = _candidate_metrics_fast(final_source, baseline_target, grid, onsets_before)
                    candidate_metrics["baseline"] = baseline_metrics
                    candidate_metrics["fixed_window_repair"] = {"windows": repaired_windows}
                    candidate_metrics["fixed_window_repair_diagnostics"] = repaired_diagnostics
                    hybrid_inference_skip_reason = repaired_reason
                    candidate_metrics["hybrid_skipped"] = {
                        "reason": f"{repaired_reason}_after_fixed_window_repair",
                        "duration_sec": duration_sec,
                        **repaired_precheck_diagnostics,
                    }
        if (
            request.mode == "hybrid"
            and hybrid_inference_skip_reason is None
            and duration_sec >= 180.0
            and float(precheck_diagnostics.get("avg_abs_error_after_sec", 999.0) or 999.0) <= 0.035
            and float(precheck_diagnostics.get("fixed_locked_ratio", 0.0) or 0.0) >= 0.95
            and int(precheck_diagnostics.get("fixed_unstable_windows", 0) or 0) == 0
            and int(precheck_diagnostics.get("fixed_meltdown_windows", 0) or 0) == 0
            and str(precheck_diagnostics.get("continuity_verdict", "")) == "jump_risk"
        ):
            continuity_smoothed_target, continuity_smooth_decisions = _continuity_smooth_target(
                final_source,
                continuity_smooth_precheck_target,
                grid,
                onsets_before,
                duration_sec,
                request.target_bpm,
                resolution,
            )
            if continuity_smooth_decisions:
                smoothed_reason, smoothed_precheck_diagnostics = _hybrid_baseline_grid_skip_reason(
                    request.mode,
                    final_source,
                    continuity_smoothed_target,
                    grid,
                    onsets_before,
                    segments,
                    duration_sec,
                    request.target_bpm,
                    resolution,
                )
                candidate_metrics["hybrid_precheck_smoothed"] = {
                    "windows": continuity_smooth_decisions,
                    "diagnostics": smoothed_precheck_diagnostics,
                }
                if smoothed_reason is not None:
                    baseline_target = continuity_smoothed_target
                    baseline_metrics = _candidate_metrics_fast(final_source, baseline_target, grid, onsets_before)
                    candidate_metrics["baseline"] = baseline_metrics
                    candidate_metrics["continuity_smooth_precheck"] = {"windows": continuity_smooth_decisions}
                    hybrid_inference_skip_reason = smoothed_reason
                    candidate_metrics["hybrid_skipped"] = {
                        "reason": f"{smoothed_reason}_after_continuity_smooth",
                        "duration_sec": duration_sec,
                        **smoothed_precheck_diagnostics,
                    }

    if request.mode in {"ml", "hybrid"} and hybrid_inference_skip_reason is None:
        interactive_device = resolve_device("cuda")
        if "mel" not in feat:
            feat["mel"] = compute_mel_feature(mono, sr, hop=int(feat["hop_length"]))
        ml_curves = infer_curve_candidates(
            feat["mel"],
            feat["onset"],
            duration_sec,
            MODELS_DIR,
            device=interactive_device,
            prefer_openvino=False,
            y_mono=mono,
            sr=sr,
            bpm=request.target_bpm,
            resolution=resolution,
        )
        ml_curve = next((curve for curve in ml_curves if curve.get("candidate_name") == "routed"), None)
        if ml_curve is None and ml_curves:
            ml_curve = max(ml_curves, key=lambda curve: float(curve.get("confidence", 0.0)))
        if ml_curve is not None:
            ml_used = True
            ml_confidence = float(ml_curve["confidence"])
            ml_candidate_info = {
                "candidate_name": str(ml_curve.get("candidate_name") or ml_curve.get("model_file") or "ml"),
                "model_file": str(ml_curve.get("model_file", "")),
                "accelerator": str(ml_curve.get("accelerator", "torch")),
                "routing_weights": ml_curve.get("routing_weights"),
                "routing_percussive_ratio": ml_curve.get("routing_percussive_ratio"),
            }
            if request.mode == "ml":
                final_target = np.interp(final_source, ml_curve["source_times"], ml_curve["target_times"])
                final_target = np.maximum.accumulate(final_target)
            else:
                final_target, ml_blend_alpha, ml_blend_diagnostics = _blend_ml_with_baseline(
                    final_source,
                    final_target,
                    ml_curve,
                    grid,
                )
    finish_stage("ml_inference")

    if request.mode == "ml" and not ml_used:
        final_target = base_curve["target_times"]
    if hybrid_inference_skip_reason is not None and "hybrid_skipped" not in candidate_metrics:
        candidate_metrics["hybrid_skipped"] = {
            "reason": hybrid_inference_skip_reason,
            "duration_sec": duration_sec,
            "event_density_per_sec": float(event_summary.get("event_density_per_sec", 0.0)),
            "strong_event_count": int(event_summary.get("strong_event_count", 0)),
            "style_profile": str(style_profile.get("profile", "balanced")),
        }

    warped_audio: np.ndarray | None = None
    warp_method = ""
    if proxy_audio is None:
        proxy_sr = sr

    if request.mode == "ml":
        final_target, effective_groove_preserve, _ = _optimize_groove_target(
            final_source,
            final_target,
            style_guided_groove_preserve,
            grid,
            onsets_before,
            segments,
            allow_safer=False,
            max_preserve=request.groove_preserve,
            min_preserve=strict_first_pass_floor,
        )

    if request.mode == "hybrid" and ml_used and _should_skip_hybrid_search(duration_sec, ml_blend_alpha, ml_blend_diagnostics):
        candidate_metrics["hybrid_skipped"] = {
            "reason": "ml_disagreement_too_high_for_long_track",
            "duration_sec": duration_sec,
            "ml_blend_alpha": ml_blend_alpha,
            "ml_agreement_scale": float(ml_blend_diagnostics.get("ml_agreement_scale", 0.0)),
            "ml_baseline_disagreement_ratio": float(ml_blend_diagnostics.get("ml_baseline_disagreement_ratio", 0.0)),
        }
        ml_used = False
        ml_blend_alpha = 0.0

    if request.mode == "hybrid" and ml_used:
        ml_curves = _hybrid_search_curves(ml_curves)
        ranked_hybrid = _rank_hybrid_candidates(
            final_source,
            baseline_target,
            ml_curves,
            grid,
            onsets_before,
            ml_blend_alpha,
            top_k=3,
        )
        segmented_target: np.ndarray | None = None
        segmented_decisions: list[dict[str, object]] = []
        if prefer_segmented_hybrid:
            segmented_target, segmented_decisions = _build_segmented_hybrid_target(
                final_source,
                baseline_target,
                ranked_hybrid,
                grid,
                onsets_before,
                segments,
            )
        if segmented_target is not None and segmented_decisions:
            ranked_hybrid.append(
                (
                    segmented_target.astype(np.float32),
                    _candidate_metrics_fast(final_source, segmented_target, grid, onsets_before),
                    float(np.mean([float(item["selected_alpha"]) for item in segmented_decisions])),
                    {
                        "candidate_name": "segmented_hybrid",
                        "model_file": "segment_router",
                        "confidence": float(np.mean([float(max(item["gain_sec"], 0.0)) for item in segmented_decisions])),
                        "accelerator": "torch",
                        "segment_decisions": segmented_decisions,
                    },
                )
            )
        hybrid_target: np.ndarray | None = None
        hybrid_metrics: dict | None = None
        hybrid_proxy_metrics: dict | None = None
        hybrid_groove_preserve = baseline_groove_preserve
        for candidate_target, _, candidate_alpha, candidate_info in ranked_hybrid:
            optimized_target, candidate_groove_preserve, _ = _optimize_groove_target(
                final_source,
                candidate_target,
                style_guided_groove_preserve,
                grid,
                onsets_before,
                segments,
                allow_safer=False,
                max_preserve=request.groove_preserve,
            )
            if proxy_audio is None or onsets_before_proxy is None:
                proxy_audio, proxy_sr, onsets_before_proxy = _selection_proxy(audio, sr)
            actual_metrics = _candidate_metrics_proxy(proxy_audio, proxy_sr, final_source, optimized_target, grid, onsets_before_proxy)
            if hybrid_proxy_metrics is None or float(actual_metrics["avg_abs_error_after_sec"]) < float(hybrid_proxy_metrics["avg_abs_error_after_sec"]):
                hybrid_target = optimized_target
                hybrid_proxy_metrics = actual_metrics
                ml_blend_alpha = candidate_alpha
                hybrid_groove_preserve = candidate_groove_preserve
                ml_candidate_info = {**candidate_info, "groove_preserve": candidate_groove_preserve}
        if hybrid_target is None or hybrid_proxy_metrics is None:
            raise RuntimeError("No verified hybrid candidate generated")
        candidate_metrics["hybrid_candidate_proxy"] = hybrid_proxy_metrics
        hybrid_metrics = _candidate_metrics_fast(final_source, hybrid_target, grid, onsets_before)
        candidate_metrics["hybrid_candidate"] = hybrid_metrics
        warp_selection, selected_metrics, ml_blend_alpha = _select_hybrid_candidate(
            baseline_metrics,
            hybrid_metrics,
            ml_blend_alpha,
        )
        if warp_selection == "hybrid_candidate":
            final_target = hybrid_target
            effective_groove_preserve = hybrid_groove_preserve
            metrics = selected_metrics
        else:
            metrics = selected_metrics
            final_target = baseline_target
            effective_groove_preserve = baseline_groove_preserve
        finish_stage("hybrid_candidate_selection")
    elif request.mode == "dtw":
        metrics = baseline_metrics
        final_target = baseline_target
        warp_selection = "baseline"
        effective_groove_preserve = baseline_groove_preserve
        warped_audio = None
        warp_method = ""
    elif request.mode in {"ml", "hybrid"} and not ml_used:
        final_target, metrics, warp_selection, effective_groove_preserve = _select_non_ml_target(
            request.mode,
            ml_used,
            strict_lock_fast_path,
            hybrid_inference_skip_reason,
            baseline_target,
            baseline_metrics,
            base_curve["target_times"],
            final_target,
            final_source,
            grid,
            onsets_before,
            candidate_metrics,
            baseline_groove_preserve,
        )
        warped_audio = None
        warp_method = ""
    else:
        metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
        candidate_metrics["requested_mode"] = metrics
    finish_stage("selected_candidate_render")

    needs_final_render = False
    beat_phase_shift_applied = False
    beat_shifted_target, beat_phase_shift = _beat_phase_shift_target(
        final_source,
        final_target,
        grid,
        onsets_before,
        beat_phase_source_times,
        duration_sec,
        request.target_bpm,
    )
    if beat_phase_shift is not None:
        final_target = beat_shifted_target
        metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
        candidate_metrics["beat_phase_shift"] = beat_phase_shift
        needs_final_render = True
        beat_phase_shift_applied = True

    preview_segment_summaries = _summarize_segments(
        segments,
        final_source,
        baseline_target,
        final_target,
        grid,
        onsets_before,
    )
    preview_metronome_lock = _summarize_metronome_lock(metrics, preview_segment_summaries)
    preview_fixed_window_lock = _summarize_fixed_window_lock(
        final_source,
        final_target,
        grid,
        onsets_before,
        duration_sec,
        request.target_bpm,
    )
    allow_local_fixed_window_repair = os.getenv("BOXBOX_DISABLE_LOCAL_FIXED_REPAIR", "").strip().lower() not in {"1", "true", "yes"}
    if (
        not beat_phase_shift_applied
        and
        allow_local_fixed_window_repair
        and duration_sec >= 45.0
        and float(preview_fixed_window_lock.get("effective_locked_ratio", preview_fixed_window_lock.get("locked_ratio", 0.0))) < 0.999
    ):
        fixed_repaired_target, fixed_window_repair_decisions, fixed_window_repair_diagnostics = _fixed_window_repair_target(
            final_source,
            final_target,
            grid,
            onsets_before,
            duration_sec,
            request.target_bpm,
        )
        if fixed_window_repair_decisions:
            final_target = fixed_repaired_target
            metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
            candidate_metrics["fixed_window_repair"] = {"windows": fixed_window_repair_decisions}
            needs_final_render = True
            preview_segment_summaries = _summarize_segments(
                segments,
                final_source,
                baseline_target,
                final_target,
                grid,
                onsets_before,
            )
            preview_metronome_lock = _summarize_metronome_lock(metrics, preview_segment_summaries)
            preview_fixed_window_lock = _summarize_fixed_window_lock(
                final_source,
                final_target,
                grid,
                onsets_before,
                duration_sec,
                request.target_bpm,
            )
        else:
            candidate_metrics["fixed_window_repair_diagnostics"] = fixed_window_repair_diagnostics
    elif duration_sec >= 45.0 and float(preview_fixed_window_lock.get("effective_locked_ratio", preview_fixed_window_lock.get("locked_ratio", 0.0))) < 0.999:
        candidate_metrics["fixed_window_repair_skipped"] = {
            "reason": "beat_phase_shift_preserves_constant_offset" if beat_phase_shift_applied else "local_window_phase_shifts_can_create_audible_bar_phase_jumps",
            "locked_ratio": float(preview_fixed_window_lock.get("locked_ratio", 0.0)),
            "strong_locked_ratio": float(preview_fixed_window_lock.get("strong_locked_ratio", 0.0)),
        }
    preview_metronome_lock = _apply_fixed_window_gate(preview_metronome_lock, preview_fixed_window_lock)
    preview_beat_phase_lock = _summarize_beat_phase_lock(
        final_source,
        final_target,
        beat_phase_source_times,
        target_bpm=request.target_bpm,
        duration_sec=duration_sec,
    )
    preview_metronome_lock = _apply_beat_phase_gate(preview_metronome_lock, preview_beat_phase_lock)
    preview_warp_continuity = _summarize_warp_continuity(final_source, final_target, request.target_bpm, resolution)
    preview_metronome_lock = _apply_warp_continuity_gate(preview_metronome_lock, preview_warp_continuity)
    if beat_phase_shift_applied and str(preview_warp_continuity.get("verdict", "")) != "jump_risk":
        candidate_metrics["post_selection_skipped"] = {"reason": "beat_phase_shift_preserves_constant_offset"}
    elif _should_skip_post_selection_repairs(duration_sec, preview_metronome_lock):
        candidate_metrics["post_selection_skipped"] = {"reason": str(preview_metronome_lock.get("verdict", "locked_enough"))}
    else:
        phase_snap_decisions: list[dict[str, object]] = []
        if segments:
            snapped_target, phase_snap_decisions = _phase_snap_target(
                final_source,
                final_target,
                grid,
                onsets_before,
                segments,
                request.target_bpm,
                resolution,
            )
            if phase_snap_decisions:
                final_target = snapped_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["phase_snap"] = {"segments": phase_snap_decisions}
                needs_final_render = True

        coverage_snap_decisions: list[dict[str, object]] = []
        if segments:
            coverage_target, coverage_snap_decisions = _coverage_snap_target(
                final_source,
                final_target,
                grid,
                onsets_before,
                segments,
            )
            if coverage_snap_decisions:
                final_target = coverage_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_snap"] = {"segments": coverage_snap_decisions}
                needs_final_render = True

        coverage_tighten_decisions: list[dict[str, object]] = []
        if segments:
            tightened_target, coverage_tighten_decisions = _coverage_tighten_target(
                final_source,
                final_target,
                base_curve["target_times"],
                grid,
                onsets_before,
                segments,
            )
            if coverage_tighten_decisions:
                final_target = tightened_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_tighten"] = {"segments": coverage_tighten_decisions}
                needs_final_render = True

        coverage_rebuild_decisions: list[dict[str, object]] = []
        if segments:
            rebuilt_target, coverage_rebuild_decisions = _coverage_rebuild_target(
                final_source,
                final_target,
                base_curve["target_times"],
                grid,
                onsets_before,
                segments,
            )
            if coverage_rebuild_decisions:
                final_target = rebuilt_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_rebuild"] = {"segments": coverage_rebuild_decisions}
                needs_final_render = True

        coverage_bridge_decisions: list[dict[str, object]] = []
        if segments:
            bridged_target, coverage_bridge_decisions = _coverage_bridge_target(
                final_source,
                final_target,
                base_curve["target_times"],
                grid,
                onsets_before,
                segments,
            )
            if coverage_bridge_decisions:
                final_target = bridged_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_bridge"] = {"segments": coverage_bridge_decisions}
                needs_final_render = True

        coverage_context_reanchor_decisions: list[dict[str, object]] = []
        if segments:
            context_reanchored_target, coverage_context_reanchor_decisions = _coverage_context_reanchor_target(
                final_source,
                final_target,
                grid,
                onsets_before,
                segments,
            )
            if coverage_context_reanchor_decisions:
                final_target = context_reanchored_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_context_reanchor"] = {"segments": coverage_context_reanchor_decisions}
                needs_final_render = True

        coverage_replace_decisions: list[dict[str, object]] = []
        if segments:
            replaced_target, coverage_replace_decisions = _coverage_replace_target(
                final_source,
                final_target,
                base_curve["target_times"],
                grid,
                onsets_before,
                segments,
            )
            if coverage_replace_decisions:
                final_target = replaced_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_replace"] = {"segments": coverage_replace_decisions}
                needs_final_render = True

        coverage_reanchor_decisions: list[dict[str, object]] = []
        if segments:
            reanchored_target, coverage_reanchor_decisions = _coverage_reanchor_target(
                final_source,
                final_target,
                grid,
                onsets_before,
                segments,
            )
            if coverage_reanchor_decisions:
                final_target = reanchored_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_reanchor"] = {"segments": coverage_reanchor_decisions}
                needs_final_render = True

        coverage_commit_decisions: list[dict[str, object]] = []
        if segments:
            committed_target, coverage_commit_decisions = _coverage_commit_target(
                final_source,
                final_target,
                base_curve["target_times"],
                grid,
                onsets_before,
                segments,
            )
            if coverage_commit_decisions:
                final_target = committed_target
                metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
                candidate_metrics["coverage_commit"] = {"segments": coverage_commit_decisions}
                needs_final_render = True

        continuity_smoothed_target, continuity_smooth_decisions = _continuity_smooth_target(
            final_source,
            final_target,
            grid,
            onsets_before,
            duration_sec,
            request.target_bpm,
            resolution,
        )
        if continuity_smooth_decisions:
            final_target = continuity_smoothed_target
            metrics = _candidate_metrics_fast(final_source, final_target, grid, onsets_before)
            candidate_metrics["continuity_smooth"] = {"windows": continuity_smooth_decisions}
            needs_final_render = True
    finish_stage("post_selection_repairs")

    _update_job(job_id, "processing", "warp", 0.7, "Applying stereo-safe time warp")
    _update_job(job_id, "processing", "metrics", 0.82, "Computing timing metrics")
    if needs_final_render or warped_audio is None:
        warped_audio, warp_method, metrics = _candidate_metrics(audio, sr, final_source, final_target, grid, onsets_before)
        if warp_selection == "strict_onset_grid":
            candidate_metrics["requested_mode"] = metrics
    finish_stage("final_render")

    _update_job(job_id, "processing", "export", 0.92, "Exporting output files")
    report_path = output_dir / "report.json"
    midi_path = output_dir / "tempo_map.mid"
    metronome_check_path = output_dir / "metronome_check.wav"

    audio_files = export_quantized_wav(output_dir / "quantized.wav", warped_audio, sr, source_meta=source_meta)
    export_tempo_midi(midi_path, request.target_bpm, float(len(warped_audio) / sr))
    metronome_check = export_metronome_check_wav(metronome_check_path, warped_audio, sr, request.target_bpm)
    finish_stage("export_audio_and_midi")
    segment_summaries = _summarize_segments(
        segments,
        final_source,
        baseline_target,
        final_target,
        grid,
        onsets_before,
    )
    metronome_lock = _summarize_metronome_lock(metrics, segment_summaries)
    fixed_window_lock = _summarize_fixed_window_lock(
        final_source,
        final_target,
        grid,
        onsets_before,
        duration_sec,
        request.target_bpm,
    )
    metronome_lock = _apply_fixed_window_gate(metronome_lock, fixed_window_lock)
    beat_phase_lock = _summarize_beat_phase_lock(
        final_source,
        final_target,
        beat_phase_source_times,
        target_bpm=request.target_bpm,
        duration_sec=duration_sec,
    )
    metronome_lock = _apply_beat_phase_gate(metronome_lock, beat_phase_lock)
    warp_continuity = _summarize_warp_continuity(final_source, final_target, request.target_bpm, resolution)
    metronome_lock = _apply_warp_continuity_gate(metronome_lock, warp_continuity)
    metronome_lock = _apply_fixed_grid_authority_gate(metronome_lock, metrics)
    daw_lock_diagnostics = _build_daw_lock_diagnostics(metronome_lock)
    raw_position_guidance = summarize_position_feedback_memory(
        read_json(_segment_feedback_memory_path, default={}),
        style_profile_name=str(style_profile.get("profile", "balanced")),
        segment_feedback=segment_summaries,
        mode_requested=request.mode,
        target_bpm=request.target_bpm,
        resolution=resolution,
        groove_preserve=request.groove_preserve,
        effective_groove_preserve=effective_groove_preserve,
        ml_used=ml_used,
    )
    segment_feedback = build_segment_feedback(
        mode_requested=request.mode,
        target_bpm=request.target_bpm,
        resolution=resolution,
        groove_preserve=request.groove_preserve,
        effective_groove_preserve=effective_groove_preserve,
        ml_used=ml_used,
        segment_summaries=segment_summaries,
        total_duration_sec=duration_sec,
        position_guidance=raw_position_guidance,
    )
    best_next_pass = build_best_next_pass(segment_feedback)
    segment_feedback_summary = summarize_segment_feedback(segment_feedback, [])
    position_guidance = summarize_position_feedback_memory(
        read_json(_segment_feedback_memory_path, default={}),
        style_profile_name=str(style_profile.get("profile", "balanced")),
        segment_feedback=segment_feedback,
        mode_requested=request.mode,
        target_bpm=request.target_bpm,
        resolution=resolution,
        groove_preserve=request.groove_preserve,
        effective_groove_preserve=effective_groove_preserve,
        ml_used=ml_used,
    )
    recommendation_behavior = summarize_recommendation_memory(
        read_json(_recommendation_memory_path, default={}),
        style_profile_name=str(style_profile.get("profile", "balanced")),
    )
    finish_stage("report_analysis")
    processing_timing["elapsed_before_report_write_sec"] = round(float(time.perf_counter() - pipeline_start), 4)
    files = {
        **audio_files,
        "metronome_check_audio": str(metronome_check_path.name),
        "report_json": str(report_path.name),
        "tempo_map_midi": str(midi_path.name),
    }

    report = {
        "job_id": job_id,
        "output_files": files,
        "mode_requested": request.mode,
        "mode_effective": "ml" if request.mode == "ml" and ml_used else ("hybrid" if request.mode == "hybrid" and ml_used else "onset_grid"),
        "model_available": model_available,
        "ml_used": ml_used,
        "ml_confidence": ml_confidence,
        "ml_candidate": ml_candidate_info,
        "runtime_config": _runtime_config_report(),
        "ml_blend_alpha": ml_blend_alpha,
        "ml_blend_diagnostics": ml_blend_diagnostics,
        "warp_selection": warp_selection,
        "target_bpm": request.target_bpm,
        "resolution": resolution,
        "groove_preserve": request.groove_preserve,
        "style_guided_groove_preserve": style_guided_groove_preserve,
        "effective_groove_preserve": effective_groove_preserve,
        "duration_input_sec": duration_sec,
        "duration_output_sec": float(len(warped_audio) / sr),
        "sample_rate": sr,
        "input_channels": int(audio.shape[1]),
        "output_channels": int(warped_audio.shape[1]),
        "is_stereo_output": bool(warped_audio.shape[1] == 2 if audio.shape[1] == 2 else warped_audio.shape[1] == 1),
        "warp_method": warp_method,
        "quantize_method": base_curve["method"],
        "anchor_count": base_curve["anchor_count"],
        "source_meta": source_meta,
        "segments": segment_summaries,
        "metronome_lock": metronome_lock,
        "daw_lock_diagnostics": daw_lock_diagnostics,
        "metronome_check": metronome_check,
        "event_detection": {
            "summary": event_summary,
            "preview": detected_events[:128],
        },
        "tempo_drift": {
            "summary": tempo_drift["summary"],
            "preview": tempo_drift["windows"][:128],
        },
        "style_adaptation": {
            **style_profile,
            "learned_behavior": learned_behavior,
            "guided_groove_preserve": style_guided_groove_preserve,
            "segmented_hybrid_applied": bool(prefer_segmented_hybrid and request.mode == "hybrid" and ml_used),
        },
        "feedback_loop": build_feedback_loop(
            mode_requested=request.mode,
            target_bpm=request.target_bpm,
            resolution=resolution,
            groove_preserve=request.groove_preserve,
            effective_groove_preserve=effective_groove_preserve,
            style_profile=style_profile,
            timing_metrics=metrics,
            warp_selection=warp_selection,
            ml_used=ml_used,
            learned_behavior=learned_behavior,
            recommendation_behavior=recommendation_behavior,
            segment_feedback=segment_feedback,
            best_next_pass=best_next_pass,
        ),
        "timing_metrics": metrics,
        "candidate_metrics": candidate_metrics,
        "processing_timing": processing_timing,
        "notes": [
            "Hybrid ML inference skipped for long dense strict-lock material; onset-grid path used"
            if request.mode == "hybrid"
            and not ml_used
            and str((candidate_metrics.get("hybrid_skipped") or {}).get("reason", "")).startswith("strict_lock_dense_long_track")
            else "ML unavailable; onset-grid fallback used"
            if request.mode in {"ml", "hybrid"} and not ml_used
            else f"ML curve blended conservatively with onset-grid quantization (alpha={ml_blend_alpha:.3f}, agreement={ml_blend_diagnostics.get('ml_agreement_scale', 0.0):.3f}, selected={warp_selection})"
            if ml_used and request.mode == "hybrid"
            else "Onset-grid quantization"
        ],
    }
    report["feedback_loop"]["segment_feedback_summary"] = segment_feedback_summary
    report["feedback_loop"]["position_guidance"] = position_guidance
    export_report(report_path, report)

    _update_job(job_id, "completed", "done", 1.0, "Quantization complete", {"output_files": files})
    return files


@app.post("/api/upload")
async def upload_audio(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    upload_dir, _ = ensure_job_dirs(UPLOADS_DIR, OUTPUTS_DIR, job_id)

    suffix = Path(file.filename or "").suffix or ".bin"
    temp_input = upload_dir / f"uploaded{suffix}"
    await file.seek(0)
    with temp_input.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
    await file.close()

    if temp_input.stat().st_size == 0:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty (0 bytes). Re-export the audio and try again.",
        )

    input_wav = upload_dir / "input.wav"
    try:
        source_meta = convert_to_wav(temp_input, input_wav)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unsupported or unreadable audio file: {exc}") from exc

    info = audio_info_from_meta(source_meta)
    metadata_bpm = _metadata_bpm_from_source_meta(source_meta)
    if metadata_bpm is not None:
        info["estimated_bpm"] = _normalize_target_bpm(metadata_bpm)
        info["estimated_bpm_raw"] = float(metadata_bpm)
        info["estimated_bpm_source"] = "metadata"
    info["source_meta"] = source_meta

    _update_job(job_id, "uploaded", "uploaded", 0.0, "Upload complete", info)
    return {"job_id": job_id, **info}


@app.post("/api/quantize")
async def quantize_audio(req: QuantizeRequest):
    global _active_quantize_job
    req = req.model_copy(update={"target_bpm": _normalize_target_bpm(req.target_bpm)})
    job_id = req.job_id
    upload_dir = UPLOADS_DIR / job_id
    if not upload_dir.exists():
        raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")

    with _quantize_lock:
        if _active_quantize_job is not None:
            if _active_quantize_job == job_id:
                raise HTTPException(status_code=409, detail=f"Job {job_id} is already processing")
            raise HTTPException(status_code=409, detail=f"Another job is processing: {_active_quantize_job}")
        _active_quantize_job = job_id
    return await run_in_threadpool(_run_quantize_request, job_id, req)


@app.get("/api/status/{job_id}")
async def status(job_id: str):
    state = job_manager.get(job_id)
    if not state:
        persisted = read_json(UPLOADS_DIR / job_id / "job.json")
        if not persisted:
            raise HTTPException(status_code=404, detail=f"Unknown job_id: {job_id}")
        state = persisted
    return state


@app.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    job_id = req.job_id
    report_path = OUTPUTS_DIR / job_id / "report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"No report found for job_id: {job_id}")

    report = read_json(report_path)
    feedback_loop = dict(report.get("feedback_loop") or {})
    segment_feedback = list(feedback_loop.get("segment_feedback") or [])
    selected_segment = None
    if req.segment_index is None:
        quick_actions = list(feedback_loop.get("quick_actions") or [])
        action = next((item for item in quick_actions if item.get("feedback") == req.feedback), None)
    else:
        selected_segment = next(
            (item for item in segment_feedback if int(item.get("segment_index", -1)) == int(req.segment_index)),
            None,
        )
        if selected_segment is None:
            raise HTTPException(status_code=400, detail="Unknown segment index")
        quick_actions = list(selected_segment.get("quick_actions") or [])
        action = next((item for item in quick_actions if item.get("feedback") == req.feedback), None)
    if action is None:
        raise HTTPException(status_code=400, detail="Unsupported feedback option")

    feedback_path = OUTPUTS_DIR / job_id / "feedback.json"
    feedback_store = read_json(feedback_path, default={"job_id": job_id, "entries": []})
    entries = list(feedback_store.get("entries") or [])
    entry = record_feedback_entry(
        feedback=req.feedback,
        notes=req.notes,
        suggested_controls=action.get("suggested_controls") or {},
        style_profile=str((report.get("style_adaptation") or {}).get("profile", "balanced")),
        mode_requested=str(report.get("mode_requested", "hybrid")),
        effective_groove_preserve=int(report.get("effective_groove_preserve", 0)),
        scope="segment" if req.segment_index is not None else "global",
        segment_index=req.segment_index,
        segment_start_sec=None if selected_segment is None else float(selected_segment.get("start_sec", 0.0)),
        segment_end_sec=None if selected_segment is None else float(selected_segment.get("end_sec", 0.0)),
        position_bucket=None if selected_segment is None else str(selected_segment.get("position_bucket", "")),
    )
    entries.append(entry)
    feedback_store["entries"] = entries
    write_json(feedback_path, feedback_store)

    feedback_memory = read_json(_feedback_memory_path, default={})
    updated_memory = feedback_memory
    segment_feedback_memory = read_json(_segment_feedback_memory_path, default={})
    updated_segment_feedback_memory = segment_feedback_memory
    recommendation_memory = read_json(_recommendation_memory_path, default={})
    updated_recommendation_memory = recommendation_memory
    latest_recommendation_outcome = None
    if req.segment_index is None:
        updated_memory = update_feedback_memory(feedback_memory, entry["style_profile"], req.feedback)
        write_json(_feedback_memory_path, updated_memory)
        selection_path = OUTPUTS_DIR / job_id / "recommendation_selection.json"
        selection_store = read_json(selection_path, default={"job_id": job_id, "entries": []})
        selection_entries = list(selection_store.get("entries") or [])
        pending_index = next(
            (
                idx
                for idx in range(len(selection_entries) - 1, -1, -1)
                if not selection_entries[idx].get("outcome_feedback")
            ),
            None,
        )
        if pending_index is not None:
            selection_entry = dict(selection_entries[pending_index])
            selection_entry["outcome_feedback"] = req.feedback
            selection_entry["outcome_scope"] = "global"
            selection_entries[pending_index] = selection_entry
            selection_store["entries"] = selection_entries
            write_json(selection_path, selection_store)
            updated_recommendation_memory = update_recommendation_outcome_memory(
                recommendation_memory,
                style_profile_name=str(selection_entry.get("style_profile", entry["style_profile"])),
                recommendation_kind=str(selection_entry.get("recommendation_kind", "")),
                feedback=req.feedback,
            )
            write_json(_recommendation_memory_path, updated_recommendation_memory)
            latest_recommendation_outcome = {
                "recommendation_kind": str(selection_entry.get("recommendation_kind", "")),
                "label": str(selection_entry.get("label", "")),
                "feedback": req.feedback,
                "style_profile": str(selection_entry.get("style_profile", entry["style_profile"])),
            }
    elif entry.get("position_bucket"):
        updated_segment_feedback_memory = update_segment_feedback_memory(
            segment_feedback_memory,
            style_profile_name=entry["style_profile"],
            position_bucket=str(entry["position_bucket"]),
            feedback=req.feedback,
        )
        write_json(_segment_feedback_memory_path, updated_segment_feedback_memory)

    feedback_loop["latest"] = entry
    feedback_loop["history_count"] = len(entries)
    if req.segment_index is None:
        feedback_loop["learned_behavior"] = summarize_feedback_memory(
            updated_memory,
            entry["style_profile"],
            int(report.get("groove_preserve", 0)),
        )
        feedback_loop["recommendation_behavior"] = summarize_recommendation_memory(
            updated_recommendation_memory,
            style_profile_name=entry["style_profile"],
        )
    else:
        updated_segments: list[dict[str, object]] = []
        for segment in segment_feedback:
            if int(segment.get("segment_index", -1)) == int(req.segment_index):
                segment_entry_count = sum(
                    1
                    for item in entries
                    if item.get("scope") == "segment" and item.get("segment_index") == int(req.segment_index)
                )
                updated_segments.append({**segment, "latest": entry, "history_count": segment_entry_count})
            else:
                updated_segments.append(segment)
        feedback_loop["segment_feedback"] = updated_segments
        feedback_loop["recommendation_behavior"] = summarize_recommendation_memory(
            recommendation_memory,
            style_profile_name=entry["style_profile"],
        )
    feedback_loop["segment_feedback_summary"] = summarize_segment_feedback(
        list(feedback_loop.get("segment_feedback") or []),
        entries,
    )
    feedback_loop["best_next_pass"] = build_best_next_pass(list(feedback_loop.get("segment_feedback") or []))
    feedback_loop["position_guidance"] = summarize_position_feedback_memory(
        updated_segment_feedback_memory,
        style_profile_name=entry["style_profile"],
        segment_feedback=list(feedback_loop.get("segment_feedback") or []),
        mode_requested=str(report.get("mode_requested", "hybrid")),
        target_bpm=float(report.get("target_bpm", 100.0)),
        resolution=int(report.get("resolution", 8)),
        groove_preserve=int(report.get("groove_preserve", 0)),
        effective_groove_preserve=int(report.get("effective_groove_preserve", 0)),
        ml_used=bool(report.get("ml_used", False)),
    )
    feedback_loop["recommendation_rank"] = rank_next_pass_recommendations(
        feedback_loop.get("learned_default"),
        dict(feedback_loop.get("learned_behavior") or {}),
        feedback_loop.get("best_next_pass"),
        dict(feedback_loop.get("recommendation_behavior") or {}),
    )
    if latest_recommendation_outcome is not None:
        feedback_loop["latest_recommendation_outcome"] = latest_recommendation_outcome
    report["feedback_loop"] = feedback_loop
    style_adaptation = dict(report.get("style_adaptation") or {})
    style_adaptation["learned_behavior"] = feedback_loop["learned_behavior"]
    report["style_adaptation"] = style_adaptation
    write_json(report_path, report)

    return {
        "job_id": job_id,
        "status": "recorded",
        "entry": entry,
        "history_count": len(entries),
    }


@app.post("/api/recommendation-selection")
async def record_recommendation_selection(req: RecommendationSelectionRequest):
    job_id = req.job_id
    report_path = OUTPUTS_DIR / job_id / "report.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail=f"No report found for job_id: {job_id}")

    report = read_json(report_path)
    feedback_loop = dict(report.get("feedback_loop") or {})
    target = feedback_loop.get(req.recommendation_kind)
    if not isinstance(target, dict):
        raise HTTPException(status_code=400, detail="Recommendation not available for this job")

    selection_path = OUTPUTS_DIR / job_id / "recommendation_selection.json"
    selection_store = read_json(selection_path, default={"job_id": job_id, "entries": []})
    entries = list(selection_store.get("entries") or [])
    entry = {
        "recommendation_kind": req.recommendation_kind,
        "label": str(target.get("label", req.recommendation_kind)),
        "style_profile": str((report.get("style_adaptation") or {}).get("profile", "balanced")),
        "suggested_controls": dict(target.get("suggested_controls") or {}),
        "control_bias": dict(target.get("control_bias") or {}),
    }
    entries.append(entry)
    selection_store["entries"] = entries
    write_json(selection_path, selection_store)

    memory = read_json(_recommendation_memory_path, default={})
    updated_memory = update_recommendation_memory(
        memory,
        style_profile_name=entry["style_profile"],
        recommendation_kind=req.recommendation_kind,
    )
    write_json(_recommendation_memory_path, updated_memory)

    feedback_loop["recommendation_behavior"] = summarize_recommendation_memory(
        updated_memory,
        style_profile_name=entry["style_profile"],
    )
    feedback_loop["recommendation_rank"] = rank_next_pass_recommendations(
        feedback_loop.get("learned_default"),
        dict(feedback_loop.get("learned_behavior") or {}),
        feedback_loop.get("best_next_pass"),
        dict(feedback_loop.get("recommendation_behavior") or {}),
    )
    feedback_loop["latest_recommendation_selection"] = entry
    report["feedback_loop"] = feedback_loop
    write_json(report_path, report)

    return {
        "job_id": job_id,
        "status": "recorded",
        "entry": entry,
        "selection_count": len(entries),
    }


@app.get("/api/download/{job_id}/{file_name}")
async def download(job_id: str, file_name: str):
    state = read_json(OUTPUTS_DIR / job_id / "job.json")
    safe_names = {"report.json", "tempo_map.mid"}
    for value in (state.get("output_files") or {}).values():
        if isinstance(value, str):
            safe_names.add(value)
    if file_name not in safe_names:
        raise HTTPException(status_code=400, detail="Unsupported file")

    path = OUTPUTS_DIR / job_id / file_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media = {
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".aif": "audio/aiff",
        ".aiff": "audio/aiff",
        ".json": "application/json",
        ".mid": "audio/midi",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/runtime-config")
async def runtime_config():
    return {
        "runtime_config": _runtime_config_report(),
        "defaults": {
            "target_bpm": DEFAULT_TARGET_BPM,
            "resolution": DEFAULT_RESOLUTION,
            "groove_preserve": DEFAULT_GROOVE_PRESERVE,
            "mode": "hybrid",
        },
        "model_available": model_exists(MODELS_DIR),
    }


@app.get("/", include_in_schema=False)
async def frontend_index():
    index_path = _frontend_dist_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index_path)


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend_spa_fallback(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")
    candidate = _frontend_dist_dir / full_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    index_path = _frontend_dist_dir / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(index_path)

