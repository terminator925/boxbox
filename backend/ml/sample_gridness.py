from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import librosa
import numpy as np

from backend.audio.features import extract_features
from backend.ml.sample_corpus import load_tracks, write_corpus_manifest


def load_audio_clip(path: Path, *, sr: int, clip_start: float, clip_duration: float) -> tuple[np.ndarray, int]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0.0, float(clip_start))),
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(int(sr)),
        "-f",
        "f32le",
    ]
    if clip_duration > 0:
        cmd.extend(["-t", str(float(clip_duration))])
    cmd.append("pipe:1")
    result = subprocess.run(cmd, check=False, capture_output=True, timeout=max(20, int(float(clip_duration) + 20)))
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace").strip() or "unknown ffmpeg error"
        raise RuntimeError(err)
    audio = np.frombuffer(result.stdout, dtype=np.float32)
    if len(audio) == 0:
        raise RuntimeError("empty audio clip")
    return audio, int(sr)


def summarize_beat_gridness(beat_times: np.ndarray) -> dict[str, Any]:
    beat_times = np.asarray(beat_times, dtype=np.float32)
    if len(beat_times) < 6:
        return {
            "gridness_class": "insufficient_beats",
            "beat_count": int(len(beat_times)),
            "median_bpm": 0.0,
            "beat_interval_cv": 0.0,
            "p90_abs_tempo_dev_pct": 0.0,
            "gridness_score": 0.0,
        }

    intervals = np.diff(beat_times)
    intervals = intervals[(intervals > 0.15) & (intervals < 2.0)]
    if len(intervals) < 5:
        return {
            "gridness_class": "insufficient_beats",
            "beat_count": int(len(beat_times)),
            "median_bpm": 0.0,
            "beat_interval_cv": 0.0,
            "p90_abs_tempo_dev_pct": 0.0,
            "gridness_score": 0.0,
        }

    median_interval = float(np.median(intervals))
    median_bpm = 60.0 / max(median_interval, 1e-6)
    interval_cv = float(np.std(intervals) / max(float(np.mean(intervals)), 1e-6))
    local_bpm = 60.0 / np.maximum(intervals, 1e-6)
    tempo_dev_pct = np.abs((local_bpm - median_bpm) / max(median_bpm, 1e-6)) * 100.0
    p90_abs_tempo_dev_pct = float(np.percentile(tempo_dev_pct, 90))
    gridness_score = float(max(0.0, 1.0 - ((interval_cv / 0.08) + (p90_abs_tempo_dev_pct / 8.0)) / 2.0))

    if interval_cv <= 0.035 and p90_abs_tempo_dev_pct <= 3.0:
        gridness_class = "likely_grid_quantized"
    elif interval_cv >= 0.07 or p90_abs_tempo_dev_pct >= 7.0:
        gridness_class = "likely_drifting_unquantized"
    else:
        gridness_class = "uncertain"

    return {
        "gridness_class": gridness_class,
        "beat_count": int(len(beat_times)),
        "median_bpm": round(float(median_bpm), 6),
        "beat_interval_cv": round(float(interval_cv), 6),
        "p90_abs_tempo_dev_pct": round(float(p90_abs_tempo_dev_pct), 6),
        "gridness_score": round(float(gridness_score), 6),
    }


def analyze_track_gridness(
    track: dict[str, Any],
    *,
    analysis_sr: int = 22050,
    clip_start: float = 0.0,
    clip_duration: float = 90.0,
) -> dict[str, Any]:
    path = Path(str(track.get("path") or ""))
    enriched = dict(track)
    try:
        y, sr = load_audio_clip(path, sr=int(analysis_sr), clip_start=float(clip_start), clip_duration=float(clip_duration))
        feat = extract_features(y.astype(np.float32), sr, include_mel=False)
        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=feat["onset"],
            sr=sr,
            hop_length=int(feat["hop_length"]),
            units="frames",
            trim=False,
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=int(feat["hop_length"]))
        metrics = summarize_beat_gridness(beat_times)
        if isinstance(tempo, np.ndarray):
            tempo = float(np.ravel(tempo)[0])
        metrics["estimated_tempo_bpm"] = round(float(tempo), 6)
        metrics["analysis_clip_start_sec"] = float(clip_start)
        metrics["analysis_clip_duration_sec"] = float(len(y) / max(sr, 1))
        metrics["analysis_error"] = ""
    except Exception as exc:
        metrics = {
            "gridness_class": "analysis_failed",
            "beat_count": 0,
            "median_bpm": 0.0,
            "beat_interval_cv": 0.0,
            "p90_abs_tempo_dev_pct": 0.0,
            "gridness_score": 0.0,
            "estimated_tempo_bpm": 0.0,
            "analysis_clip_start_sec": float(clip_start),
            "analysis_clip_duration_sec": 0.0,
            "analysis_error": str(exc),
        }
    enriched["gridness"] = metrics
    return enriched


def clip_starts_for_track(track: dict[str, Any], *, clip_duration: float, windows: int) -> list[float]:
    duration = float(track.get("duration_sec") or 0.0)
    clip_duration = float(clip_duration)
    windows = max(1, int(windows))
    if windows == 1 or duration <= clip_duration or clip_duration <= 0:
        return [0.0]
    max_start = max(0.0, duration - clip_duration)
    if windows == 2:
        return [0.0, max_start]
    return [round(float(value), 6) for value in np.linspace(0.0, max_start, windows)]


def aggregate_window_gridness(windows: list[dict[str, Any]]) -> dict[str, Any]:
    classes = [str(window.get("gridness_class") or "") for window in windows]
    counts = {name: classes.count(name) for name in sorted(set(classes))}
    valid = [window for window in windows if window.get("gridness_class") not in {"analysis_failed", "insufficient_beats"}]
    if not valid:
        gridness_class = "analysis_failed" if "analysis_failed" in counts else "insufficient_beats"
    elif counts.get("likely_drifting_unquantized", 0) > 0:
        gridness_class = "likely_drifting_unquantized"
    elif counts.get("likely_grid_quantized", 0) == len(valid) and len(valid) == len(windows):
        gridness_class = "likely_grid_quantized"
    else:
        gridness_class = "uncertain"

    p90_values = [float(window.get("p90_abs_tempo_dev_pct") or 0.0) for window in valid]
    cv_values = [float(window.get("beat_interval_cv") or 0.0) for window in valid]
    score_values = [float(window.get("gridness_score") or 0.0) for window in valid]
    bpm_values = [float(window.get("median_bpm") or 0.0) for window in valid if float(window.get("median_bpm") or 0.0) > 0]
    return {
        "gridness_class": gridness_class,
        "window_count": len(windows),
        "valid_window_count": len(valid),
        "window_class_counts": counts,
        "max_p90_abs_tempo_dev_pct": round(max(p90_values) if p90_values else 0.0, 6),
        "max_beat_interval_cv": round(max(cv_values) if cv_values else 0.0, 6),
        "min_gridness_score": round(min(score_values) if score_values else 0.0, 6),
        "median_bpm": round(float(np.median(bpm_values)) if bpm_values else 0.0, 6),
    }


def analyze_track_gridness_windows(
    track: dict[str, Any],
    *,
    analysis_sr: int = 22050,
    clip_duration: float = 90.0,
    windows: int = 1,
) -> dict[str, Any]:
    starts = clip_starts_for_track(track, clip_duration=clip_duration, windows=windows)
    analyzed_windows = [
        analyze_track_gridness(
            track,
            analysis_sr=analysis_sr,
            clip_start=start,
            clip_duration=clip_duration,
        )["gridness"]
        for start in starts
    ]
    enriched = dict(track)
    enriched["gridness_windows"] = analyzed_windows
    enriched["gridness"] = aggregate_window_gridness(analyzed_windows)
    return enriched


def analyze_manifest_gridness(
    manifest_path: Path,
    *,
    limit: int = 0,
    analysis_sr: int = 22050,
    clip_start: float = 0.0,
    clip_duration: float = 90.0,
    windows: int = 1,
) -> dict[str, Any]:
    tracks = load_tracks(manifest_path)
    if int(limit) > 0:
        tracks = tracks[: int(limit)]
    if int(windows) <= 1:
        analyzed = [
            analyze_track_gridness(track, analysis_sr=analysis_sr, clip_start=clip_start, clip_duration=clip_duration)
            for track in tracks
        ]
    else:
        analyzed = [
            analyze_track_gridness_windows(track, analysis_sr=analysis_sr, clip_duration=clip_duration, windows=windows)
            for track in tracks
        ]
    counts: dict[str, int] = {}
    for track in analyzed:
        key = str((track.get("gridness") or {}).get("gridness_class") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    likely_drifting = [
        track for track in analyzed if (track.get("gridness") or {}).get("gridness_class") == "likely_drifting_unquantized"
    ]
    likely_grid = [
        track for track in analyzed if (track.get("gridness") or {}).get("gridness_class") == "likely_grid_quantized"
    ]
    return {
        "source_manifest": str(manifest_path),
        "analyzed_count": len(analyzed),
        "analysis_sr": int(analysis_sr),
        "clip_start": float(clip_start),
        "clip_duration": float(clip_duration),
        "windows": int(windows),
        "gridness_counts": dict(sorted(counts.items())),
        "tracks": analyzed,
        "likely_drifting_unquantized": sorted(
            likely_drifting,
            key=lambda item: (
                -float((item.get("gridness") or {}).get("p90_abs_tempo_dev_pct") or 0.0),
                -float((item.get("gridness") or {}).get("max_p90_abs_tempo_dev_pct") or 0.0),
                str(item.get("relative_path") or item.get("name")),
            ),
        ),
        "likely_grid_quantized": sorted(
            likely_grid,
            key=lambda item: (
                -float((item.get("gridness") or {}).get("gridness_score") or 0.0),
                str(item.get("relative_path") or item.get("name")),
            ),
        ),
    }


def _corpus(name: str, source_manifest: Path, tracks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "source_manifest": str(source_manifest),
        "selected_count": len(tracks),
        "available_count": len(tracks),
        "classification_source": "sample_gridness",
        "tracks": tracks,
    }


def write_outputs(report: dict[str, Any], output: Path, drifting_output: Path | None, grid_output: Path | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved sample gridness report: {output}")
    source = Path(str(report["source_manifest"]))
    if drifting_output is not None:
        write_corpus_manifest(
            _corpus("stash_audio_likely_drifting_unquantized", source, list(report["likely_drifting_unquantized"])),
            drifting_output,
        )
    if grid_output is not None:
        write_corpus_manifest(
            _corpus("stash_audio_likely_grid_quantized", source, list(report["likely_grid_quantized"])),
            grid_output,
        )


def print_report(report: dict[str, Any]) -> None:
    print(f"analyzed_count={report['analyzed_count']}")
    print(f"gridness_counts={json.dumps(report['gridness_counts'], sort_keys=True)}")
    for idx, track in enumerate(report["likely_drifting_unquantized"][:10], start=1):
        gridness = track["gridness"]
        cv = gridness.get("beat_interval_cv", gridness.get("max_beat_interval_cv", 0.0))
        dev = gridness.get("p90_abs_tempo_dev_pct", gridness.get("max_p90_abs_tempo_dev_pct", 0.0))
        print(
            "drifting_{idx}=cv:{cv} p90_dev:{dev} bpm:{bpm} {path}".format(
                idx=idx,
                cv=cv,
                dev=dev,
                bpm=gridness["median_bpm"],
                path=track.get("relative_path") or track.get("name"),
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("data/benchmark_corpus_stash_likely_older_with_tags.json"))
    parser.add_argument("--output", type=Path, default=Path("data/sample_stash_gridness_report.json"))
    parser.add_argument("--drifting-output", type=Path, default=Path("data/benchmark_corpus_stash_audio_drifting.json"))
    parser.add_argument("--grid-output", type=Path, default=Path("data/benchmark_corpus_stash_audio_grid_quantized.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--analysis-sr", type=int, default=22050)
    parser.add_argument("--clip-start", type=float, default=0.0)
    parser.add_argument("--clip-duration", type=float, default=90.0)
    parser.add_argument("--windows", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = analyze_manifest_gridness(
        args.manifest,
        limit=args.limit,
        analysis_sr=args.analysis_sr,
        clip_start=args.clip_start,
        clip_duration=args.clip_duration,
        windows=args.windows,
    )
    write_outputs(report, args.output, args.drifting_output, args.grid_output)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print_report(report)


if __name__ == "__main__":
    main()
