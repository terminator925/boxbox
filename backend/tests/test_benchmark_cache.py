from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from backend.ml import benchmark as bench


def _expected_runtime_config() -> dict[str, object]:
    return {
        "inference_accelerator": "torch",
        "inference_candidate_strategy": "core4_adaptive_plus",
        "hybrid_search_strategy": "core4",
        "hybrid_verify_top_k": 3,
    }


def test_benchmark_real_audio_cached_reuses_saved_result(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "example.wav"
    audio_path.write_bytes(b"not-a-real-audio-file")
    cache_dir = tmp_path / "cache"

    calls = {"count": 0}

    def fake_benchmark_real_audio(
        audio_path: Path,
        target_bpm: float | None = None,
        resolution: int = 8,
        clip_start: float = 0.0,
        clip_duration: float | None = None,
        max_inference_models=None,
    ) -> dict:
        calls["count"] += 1
        return {
            "audio_path": str(audio_path),
            "target_bpm": target_bpm or 120.0,
            "resolution": resolution,
            "baseline": {"avg_abs_error_after_sec": 0.1},
            "ml": {"avg_abs_error_after_sec": 0.2},
            "hybrid": {"avg_abs_error_after_sec": 0.09},
            "hybrid_effective": {"avg_abs_error_after_sec": 0.09},
            "hybrid_selected": "hybrid_candidate",
            "hybrid_alpha": 0.05,
            "hybrid_diagnostics": {},
            "ml_confidence": 0.5,
            "ml_beats_baseline": False,
            "hybrid_beats_baseline": True,
        }

    monkeypatch.setattr(bench, "benchmark_real_audio", fake_benchmark_real_audio)

    first = bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir)
    second = bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir)

    assert calls["count"] == 1
    assert first == second
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1
    assert json.loads(cache_files[0].read_text(encoding="utf-8")) == first


def test_benchmark_real_audio_cached_separates_proxy_and_clip_cache_keys(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "example.wav"
    audio_path.write_bytes(b"not-a-real-audio-file")
    cache_dir = tmp_path / "cache"

    calls = {"full": 0, "proxy": 0}

    def fake_full(audio_path: Path, target_bpm=None, resolution=8, clip_start=0.0, clip_duration=None, max_inference_models=None):
        calls["full"] += 1
        return {
            "audio_path": str(audio_path),
            "benchmark_mode": "full",
            "clip_start_sec": clip_start,
            "clip_duration_sec": clip_duration,
            "baseline": {"avg_abs_error_after_sec": 0.1},
            "ml": {"avg_abs_error_after_sec": 0.2},
            "hybrid": {"avg_abs_error_after_sec": 0.09},
            "hybrid_effective": {"avg_abs_error_after_sec": 0.09},
            "ml_beats_baseline": False,
            "hybrid_beats_baseline": True,
        }
    monkeypatch.setattr(bench, "benchmark_real_audio", fake_full)
    def fake_proxy(audio_path: Path, target_bpm=None, resolution=8, clip_start=0.0, clip_duration=None, fast_hybrid_scoring=False, max_inference_models=None):
        calls["proxy"] += 1
        return {
            "audio_path": str(audio_path),
            "benchmark_mode": "proxy",
            "clip_start_sec": clip_start,
            "clip_duration_sec": clip_duration,
            "baseline": {"avg_abs_error_after_sec": 0.11},
            "ml": {"avg_abs_error_after_sec": 0.21},
            "hybrid": {"avg_abs_error_after_sec": 0.1},
            "hybrid_effective": {"avg_abs_error_after_sec": 0.1},
            "ml_beats_baseline": False,
            "hybrid_beats_baseline": True,
        }
    monkeypatch.setattr(bench, "benchmark_real_audio_proxy", fake_proxy)

    full = bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, clip_start=10.0, clip_duration=20.0)
    proxy = bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, clip_start=10.0, clip_duration=20.0, proxy_only=True)

    assert full["benchmark_mode"] == "full"
    assert proxy["benchmark_mode"] == "proxy"
    assert calls["full"] == 1
    assert calls["proxy"] == 1
    assert len(list(cache_dir.glob("*.json"))) == 2


def test_benchmark_cache_key_includes_inference_accelerator(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "example.wav"
    audio_path.write_bytes(b"not-a-real-audio-file")
    cache_dir = tmp_path / "cache"
    calls = {"count": 0}

    def fake_proxy(audio_path: Path, target_bpm=None, resolution=8, clip_start=0.0, clip_duration=None, fast_hybrid_scoring=False, max_inference_models=None):
        calls["count"] += 1
        return {
            "audio_path": str(audio_path),
            "benchmark_mode": "proxy",
            "baseline": {"avg_abs_error_after_sec": 0.1},
            "ml": {"avg_abs_error_after_sec": 0.2},
            "hybrid": {"avg_abs_error_after_sec": 0.09},
            "hybrid_effective": {"avg_abs_error_after_sec": 0.09},
            "ml_beats_baseline": False,
            "hybrid_beats_baseline": True,
        }

    monkeypatch.setattr(bench, "benchmark_real_audio_proxy", fake_proxy)
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "torch")
    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True)
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "auto")
    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True)

    assert calls["count"] == 2
    assert len(list(cache_dir.glob("*.json"))) == 2

    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True, fast_proxy_hybrid_scoring=True)
    assert calls["count"] == 3
    assert len(list(cache_dir.glob("*.json"))) == 3

    monkeypatch.setenv("BOXBOX_HYBRID_SEARCH_STRATEGY", "routed")
    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True, fast_proxy_hybrid_scoring=True)
    assert calls["count"] == 4
    assert len(list(cache_dir.glob("*.json"))) == 4

    monkeypatch.setenv("BOXBOX_INFER_CANDIDATE_STRATEGY", "core4")
    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True, fast_proxy_hybrid_scoring=True)
    assert calls["count"] == 5
    assert len(list(cache_dir.glob("*.json"))) == 5

    monkeypatch.setenv("BOXBOX_INFER_CANDIDATE_STRATEGY", "core4_adaptive")
    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True, fast_proxy_hybrid_scoring=True)
    assert calls["count"] == 6
    assert len(list(cache_dir.glob("*.json"))) == 6

    monkeypatch.setenv("BOXBOX_HYBRID_VERIFY_TOPK", "2")
    bench.benchmark_real_audio_cached(audio_path, resolution=8, cache_dir=cache_dir, proxy_only=True, fast_proxy_hybrid_scoring=True)
    assert calls["count"] == 7
    assert len(list(cache_dir.glob("*.json"))) == 7


def test_benchmark_cache_key_defaults_to_promoted_hybrid_strategy(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "example.wav"
    audio_path.write_bytes(b"not-a-real-audio-file")
    cache_dir = tmp_path / "cache"
    monkeypatch.delenv("BOXBOX_INFER_ACCELERATOR", raising=False)
    monkeypatch.delenv("BOXBOX_INFER_CANDIDATE_STRATEGY", raising=False)
    monkeypatch.delenv("BOXBOX_HYBRID_SEARCH_STRATEGY", raising=False)
    monkeypatch.delenv("BOXBOX_HYBRID_VERIFY_TOPK", raising=False)

    cache_file = bench._cache_file(
        cache_dir,
        audio_path,
        bench.MODELS_DIR,
        8,
        None,
        proxy_only=True,
    )

    assert "_core4_k3" in cache_file.name


def test_proxy_benchmark_scores_hybrid_candidates_with_fast_projection(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "example.wav"
    audio_path.write_bytes(b"x")
    source_times = np.array([0.0, 0.5, 1.0], dtype=np.float32)
    target_times = np.array([0.0, 0.45, 1.0], dtype=np.float32)
    grid = source_times.copy()
    calls = {"proxy": 0, "fast": 0}
    monkeypatch.delenv("BOXBOX_INFER_ACCELERATOR", raising=False)
    monkeypatch.delenv("BOXBOX_INFER_CANDIDATE_STRATEGY", raising=False)
    monkeypatch.delenv("BOXBOX_HYBRID_SEARCH_STRATEGY", raising=False)
    monkeypatch.delenv("BOXBOX_HYBRID_VERIFY_TOPK", raising=False)

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.ones((20, 1), dtype=np.float32), 10))
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 120.0)
    monkeypatch.setattr(bench, "build_grid", lambda duration_sec, bpm, resolution: grid)
    monkeypatch.setattr(bench, "extract_features", lambda mono, sr: {"mel": np.ones((2, 3), dtype=np.float32), "onset": np.ones(3, dtype=np.float32)})
    monkeypatch.setattr(bench, "detect_onsets", lambda audio, sr, units="time": np.array([0.1, 0.5], dtype=np.float32))
    monkeypatch.setattr(
        bench,
        "onset_quantize_curve",
        lambda mono, sr, bpm, resolution: {"source_times": source_times, "target_times": target_times, "grid_times": grid},
    )
    monkeypatch.setattr(bench, "_selection_proxy", lambda audio, sr: (audio, sr, np.array([0.1, 0.5], dtype=np.float32)))
    monkeypatch.setattr(bench, "_optimize_groove_target", lambda source, target, preserve, grid_arg, before: (target, preserve, {}))
    monkeypatch.setattr(
        bench,
        "infer_curve_candidates",
        lambda *args, **kwargs: [
            {
                "candidate_name": "routed",
                "source_times": source_times,
                "target_times": target_times,
                "confidence": 0.8,
            }
        ],
    )
    monkeypatch.setattr(bench, "_blend_ml_with_baseline", lambda *args, **kwargs: (target_times, 1.0, {}))
    monkeypatch.setattr(
        bench,
        "_rank_hybrid_candidates",
        lambda *args, **kwargs: [(target_times, {"avg_abs_error_after_sec": 0.03}, 1.0, {"candidate_name": "routed"})],
    )
    monkeypatch.setattr(bench, "_select_hybrid_candidate", lambda baseline, hybrid, alpha: ("hybrid_candidate", hybrid, alpha))

    def fake_proxy_metrics(*args, **kwargs):
        calls["proxy"] += 1
        return {"avg_abs_error_after_sec": 0.05, "num_events_before": 2, "num_events_after": 2}

    def fake_fast_metrics(*args, **kwargs):
        calls["fast"] += 1
        return {"avg_abs_error_after_sec": 0.04, "num_events_before": 2, "num_events_after": 2}

    monkeypatch.setattr(bench, "_candidate_metrics_proxy", fake_proxy_metrics)
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_fast_metrics)

    result = bench.benchmark_real_audio_proxy(audio_path, fast_hybrid_scoring=True)

    assert calls == {"proxy": 2, "fast": 1}
    assert result["proxy_hybrid_scoring"] == "projected_onsets"
    assert result["runtime_config"]["inference_accelerator"] == "torch"
    assert result["runtime_config"]["inference_candidate_strategy"] == "core4_adaptive_plus"
    assert result["runtime_config"]["hybrid_search_strategy"] == "core4"
    assert result["runtime_config"]["hybrid_verify_top_k"] == 3
    assert result["hybrid_search_strategy"] == "core4"


def test_full_benchmark_reports_runtime_config(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "example.wav"
    audio_path.write_bytes(b"x")
    source_times = np.array([0.0, 0.5, 1.0], dtype=np.float32)
    target_times = np.array([0.0, 0.5, 1.0], dtype=np.float32)
    grid = source_times.copy()
    monkeypatch.delenv("BOXBOX_INFER_ACCELERATOR", raising=False)
    monkeypatch.delenv("BOXBOX_INFER_CANDIDATE_STRATEGY", raising=False)
    monkeypatch.delenv("BOXBOX_HYBRID_SEARCH_STRATEGY", raising=False)
    monkeypatch.delenv("BOXBOX_HYBRID_VERIFY_TOPK", raising=False)

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.ones((20, 1), dtype=np.float32), 10))
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 120.0)
    monkeypatch.setattr(bench, "build_grid", lambda duration_sec, bpm, resolution: grid)
    monkeypatch.setattr(bench, "extract_features", lambda mono, sr: {"mel": np.ones((2, 3), dtype=np.float32), "onset": np.ones(3, dtype=np.float32)})
    monkeypatch.setattr(bench, "detect_onsets", lambda audio, sr, units="time": np.array([0.1, 0.5], dtype=np.float32))
    monkeypatch.setattr(
        bench,
        "onset_quantize_curve",
        lambda mono, sr, bpm, resolution: {"source_times": source_times, "target_times": target_times, "grid_times": grid},
    )
    monkeypatch.setattr(bench, "_selection_proxy", lambda audio, sr: (audio, sr, np.array([0.1, 0.5], dtype=np.float32)))
    monkeypatch.setattr(bench, "_optimize_groove_target", lambda source, target, preserve, grid_arg, before: (target, preserve, {}))
    monkeypatch.setattr(bench, "apply_warp", lambda audio, sr, source, target: (audio, sr))
    monkeypatch.setattr(
        bench,
        "timing_metrics",
        lambda before, after, grid_arg: {"avg_abs_error_after_sec": 0.04, "num_events_before": 2, "num_events_after": 2},
    )
    monkeypatch.setattr(
        bench,
        "_candidate_metrics_proxy",
        lambda *args, **kwargs: {"avg_abs_error_after_sec": 0.03, "num_events_before": 2, "num_events_after": 2},
    )
    monkeypatch.setattr(
        bench,
        "infer_curve_candidates",
        lambda *args, **kwargs: [
            {
                "candidate_name": "routed",
                "source_times": source_times,
                "target_times": target_times,
                "confidence": 0.8,
            }
        ],
    )
    monkeypatch.setattr(bench, "_blend_ml_with_baseline", lambda *args, **kwargs: (target_times, 1.0, {}))
    monkeypatch.setattr(
        bench,
        "_rank_hybrid_candidates",
        lambda *args, **kwargs: [(target_times, {"avg_abs_error_after_sec": 0.03}, 1.0, {"candidate_name": "routed"})],
    )
    monkeypatch.setattr(bench, "_select_hybrid_candidate", lambda baseline, hybrid, alpha: ("hybrid_candidate", hybrid, alpha))

    result = bench.benchmark_real_audio(audio_path)

    assert result["benchmark_mode"] == "full"
    assert result["runtime_config"] == _expected_runtime_config()
    assert result["hybrid_search_strategy"] == "core4"


def test_benchmark_audio_files_builds_summary_from_cached_results(tmp_path: Path, monkeypatch):
    files = [tmp_path / "a.wav", tmp_path / "b.wav"]
    for path in files:
        path.write_bytes(b"x")

    reports = {
        str(files[0]): {
            "audio_path": str(files[0]),
            "baseline": {"avg_abs_error_after_sec": 0.10},
            "ml": {"avg_abs_error_after_sec": 0.20},
            "hybrid": {"avg_abs_error_after_sec": 0.09},
            "hybrid_effective": {"avg_abs_error_after_sec": 0.09},
            "hybrid_candidate": {
                "candidate_name": "routed",
                "routing_weights": {
                    "boxbox_latest.pt": 0.2,
                    "boxbox_mixed_legacy_candidate_r1884.pt": 0.3,
                    "boxbox_legacy_specialist_r1730.pt": 0.5,
                },
            },
            "ml_beats_baseline": False,
            "hybrid_beats_baseline": True,
        },
        str(files[1]): {
            "audio_path": str(files[1]),
            "baseline": {"avg_abs_error_after_sec": 0.20},
            "ml": {"avg_abs_error_after_sec": 0.30},
            "hybrid": {"avg_abs_error_after_sec": 0.20},
            "hybrid_effective": {"avg_abs_error_after_sec": 0.20},
            "hybrid_candidate": {
                "candidate_name": "boxbox_latest",
            },
            "ml_beats_baseline": False,
            "hybrid_beats_baseline": False,
        },
    }

    monkeypatch.setattr(
        bench,
        "benchmark_real_audio_cached",
        lambda audio_path, target_bpm=None, resolution=8, cache_dir=Path("outputs/benchmark_cache"), clip_start=0.0, clip_duration=None, proxy_only=False, fast_proxy_hybrid_scoring=False, max_inference_models=None: reports[str(audio_path)],
    )

    result = bench.benchmark_audio_files(files, resolution=8, cache_dir=tmp_path / "cache")

    assert result["summary"]["num_files"] == 2
    assert result["summary"]["baseline_avg_error_after_sec"] == pytest.approx(0.15)
    assert result["summary"]["ml_avg_error_after_sec"] == pytest.approx(0.25)
    assert result["summary"]["hybrid_avg_error_after_sec"] == pytest.approx(0.145)
    assert result["summary"]["ml_wins"] == 0
    assert result["summary"]["hybrid_wins"] == 1
    assert result["routed_comparison"]["selected_candidate_counts"]["routed"] == 1
    assert result["routed_comparison"]["selected_candidate_counts"]["boxbox_latest"] == 1
    assert result["routed_comparison"]["avg_mixed_legacy_weight"] == pytest.approx(0.3)


def test_benchmark_legacy_suite_uses_named_subset(tmp_path: Path, monkeypatch):
    files = [tmp_path / "koromogo-e-1930.ogg", tmp_path / "popular-song-1931.ogg", tmp_path / "tico-tico-1943.ogg", tmp_path / "ute-1950.ogg"]
    for path in files:
        path.write_bytes(b"x")

    monkeypatch.setattr(bench, "LEGACY_BENCHMARK_SUITE", tuple(files))
    monkeypatch.setattr(
        bench,
        "benchmark_audio_files",
        lambda files, resolution=8, target_bpm=None, cache_dir=Path("outputs/benchmark_cache"), clip_start=0.0, clip_duration=None, proxy_only=False, fast_proxy_hybrid_scoring=False, max_inference_models=None: {
            "summary": {"num_files": len(files)},
            "files": [{"audio_path": str(path)} for path in files],
            "proxy_only": proxy_only,
        },
    )

    result = bench.benchmark_legacy_suite(proxy_only=True, clip_duration=30.0)

    assert result["summary"]["num_files"] == 4
    assert result["proxy_only"] is True


def test_benchmark_mixed_suite_uses_named_subset(tmp_path: Path, monkeypatch):
    files = [tmp_path / "hale-makame-1930.ogg", tmp_path / "mickey-1918.ogg", tmp_path / "ragged-but-right.ogg", tmp_path / "stayin-alive-serban-mix.wav"]
    for path in files:
        path.write_bytes(b"x")

    monkeypatch.setattr(bench, "MIXED_BENCHMARK_SUITE", tuple(files))
    monkeypatch.setattr(
        bench,
        "benchmark_audio_files",
        lambda files, resolution=8, target_bpm=None, cache_dir=Path("outputs/benchmark_cache"), clip_start=0.0, clip_duration=None, proxy_only=False, fast_proxy_hybrid_scoring=False, max_inference_models=None: {
            "summary": {"num_files": len(files)},
            "files": [{"audio_path": str(path)} for path in files],
            "proxy_only": proxy_only,
        },
    )

    result = bench.benchmark_mixed_suite(proxy_only=True, clip_duration=30.0)

    assert result["summary"]["num_files"] == 4
    assert result["proxy_only"] is True


def test_benchmark_audio_manifest_uses_filename_bpm_per_track(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track 120 BPM.wav"
    audio.write_bytes(b"x")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "path": str(audio),
                        "relative_path": audio.name,
                        "filename_bpm": 120.0,
                        "duration_sec": 180.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_benchmark(path, target_bpm=None, resolution=8, cache_dir=Path("outputs/benchmark_cache"), clip_start=0.0, clip_duration=None, proxy_only=False, fast_proxy_hybrid_scoring=False, max_inference_models=None):
        calls.append((path, target_bpm, proxy_only))
        return {
            "audio_path": str(path),
            "hybrid_effective": {"avg_abs_error_after_sec": 0.01},
            "candidate_metrics": {},
        }

    monkeypatch.setattr(bench, "benchmark_real_audio_cached", fake_benchmark)
    monkeypatch.setattr(bench, "_suite_summary", lambda files: {"num_files": len(files)})
    monkeypatch.setattr(bench, "_routed_comparison_summary", lambda files: {"routed_file_count": len(files)})

    result = bench.benchmark_audio_manifest(manifest, proxy_only=True)

    assert result["summary"]["num_files"] == 1
    assert calls == [(audio, 120.0, True)]
    assert result["files"][0]["manifest_track"]["target_bpm_source"] == "filename"


def test_main_metronome_manifest_does_not_run_standard_manifest(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": []}), encoding="utf-8")
    output = tmp_path / "report.json"
    calls = {"canary": 0, "standard": 0}

    def fake_canary_manifest(*args, **kwargs):
        calls["canary"] += 1
        return {"summary": {"num_files": 0}, "files": []}

    def fake_standard_manifest(*args, **kwargs):
        calls["standard"] += 1
        raise AssertionError("standard manifest should not run during metronome canary mode")

    monkeypatch.setattr(bench, "benchmark_metronome_canary_manifest", fake_canary_manifest)
    monkeypatch.setattr(bench, "benchmark_audio_manifest", fake_standard_manifest)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark.py",
            "--metronome-canary",
            "--audio-manifest",
            str(manifest),
            "--skip-validation",
            "--output",
            str(output),
        ],
    )

    bench.main()

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert calls == {"canary": 1, "standard": 0}
    assert "metronome_canary_suite" in saved
    assert "real_audio_suite" not in saved


def test_load_audio_manifest_tracks_accepts_utf8_bom(tmp_path: Path):
    audio = tmp_path / "track.wav"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [{"path": str(audio)}]}), encoding="utf-8-sig")

    tracks = bench.load_audio_manifest_tracks(manifest)

    assert tracks == [{"path": str(audio)}]


def test_benchmark_audio_manifest_writes_incremental_report(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "track-a.wav"
    audio_b = tmp_path / "track-b.wav"
    audio_a.write_bytes(b"x")
    audio_b.write_bytes(b"x")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"path": str(audio_a), "relative_path": audio_a.name},
                    {"path": str(audio_b), "relative_path": audio_b.name},
                ]
            }
        ),
        encoding="utf-8",
    )
    partial = tmp_path / "partial.json"

    def fake_benchmark(path, target_bpm=None, resolution=8, cache_dir=Path("outputs/benchmark_cache"), clip_start=0.0, clip_duration=None, proxy_only=False, fast_proxy_hybrid_scoring=False, max_inference_models=None):
        return {
            "audio_path": str(path),
            "hybrid_effective": {"avg_abs_error_after_sec": 0.01},
            "candidate_metrics": {},
        }

    monkeypatch.setattr(bench, "benchmark_real_audio_cached", fake_benchmark)
    monkeypatch.setattr(bench, "_suite_summary", lambda files: {"num_files": len(files)})
    monkeypatch.setattr(bench, "_routed_comparison_summary", lambda files: {"routed_file_count": len(files)})

    result = bench.benchmark_audio_manifest(manifest, incremental_output=partial)
    saved = json.loads(partial.read_text(encoding="utf-8"))

    assert result["summary"]["num_files"] == 2
    assert saved["real_audio_suite"]["summary"]["num_files"] == 2
    assert [item["audio_path"] for item in saved["real_audio_suite"]["files"]] == [str(audio_a), str(audio_b)]


def test_benchmark_audio_manifest_resumes_existing_results(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "track-a.wav"
    audio_b = tmp_path / "track-b.wav"
    audio_a.write_bytes(b"x")
    audio_b.write_bytes(b"x")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"path": str(audio_a), "relative_path": audio_a.name},
                    {"path": str(audio_b), "relative_path": audio_b.name},
                ]
            }
        ),
        encoding="utf-8",
    )
    partial = tmp_path / "partial.json"
    existing = {
        "audio_path": str(audio_a),
        "hybrid_effective": {"avg_abs_error_after_sec": 0.02},
        "manifest_track": {"relative_path": audio_a.name},
    }
    partial.write_text(
        json.dumps({"real_audio_suite": {"summary": {"num_files": 1}, "files": [existing]}}),
        encoding="utf-8",
    )
    calls = []

    def fake_benchmark(path, target_bpm=None, resolution=8, cache_dir=Path("outputs/benchmark_cache"), clip_start=0.0, clip_duration=None, proxy_only=False, fast_proxy_hybrid_scoring=False, max_inference_models=None):
        calls.append(path)
        return {
            "audio_path": str(path),
            "hybrid_effective": {"avg_abs_error_after_sec": 0.01},
            "candidate_metrics": {},
        }

    monkeypatch.setattr(bench, "benchmark_real_audio_cached", fake_benchmark)
    monkeypatch.setattr(bench, "_suite_summary", lambda files: {"num_files": len(files)})
    monkeypatch.setattr(bench, "_routed_comparison_summary", lambda files: {"routed_file_count": len(files)})

    result = bench.benchmark_audio_manifest(manifest, incremental_output=partial, resume=True)

    assert calls == [audio_b]
    assert result["files"][0]["hybrid_effective"]["avg_abs_error_after_sec"] == 0.02
    assert result["files"][1]["audio_path"] == str(audio_b)


def test_benchmark_audio_manifest_can_skip_bad_audio(tmp_path: Path, monkeypatch):
    good = tmp_path / "good.wav"
    bad = tmp_path / "bad.mp3"
    good.write_bytes(b"good")
    bad.write_bytes(b"bad")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"path": str(good), "relative_path": good.name},
                    {"path": str(bad), "relative_path": bad.name},
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_benchmark(path, **kwargs):
        if Path(path) == bad:
            raise RuntimeError("No audio stream found in uploaded file")
        return {
            "audio_path": str(path),
            "hybrid_effective": {"avg_abs_error_after_sec": 0.01},
            "candidate_metrics": {},
        }

    monkeypatch.setattr(bench, "benchmark_real_audio_cached", fake_benchmark)
    monkeypatch.setattr(bench, "_suite_summary", lambda files: {"num_files": len(files)})
    monkeypatch.setattr(bench, "_routed_comparison_summary", lambda files: {"routed_file_count": len(files)})

    result = bench.benchmark_audio_manifest(manifest, skip_audio_errors=True)

    assert result["summary"]["num_files"] == 1
    assert [item["audio_path"] for item in result["files"]] == [str(good)]
    assert len(result["failed_files"]) == 1
    assert result["failed_files"][0]["relative_path"] == bad.name
    assert result["failed_files"][0]["error"] == "RuntimeError"


def test_standard_benchmark_skips_single_audio_when_manifest_requested(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"x")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [{"path": str(audio), "relative_path": audio.name}]}), encoding="utf-8")
    output = tmp_path / "report.json"
    calls = {"single": 0, "manifest": 0}

    def fake_single(*args, **kwargs):
        calls["single"] += 1
        return {}

    def fake_manifest(*args, **kwargs):
        calls["manifest"] += 1
        return {"summary": {"num_files": 1}, "files": []}

    monkeypatch.setattr(bench, "benchmark_real_audio_proxy", fake_single)
    monkeypatch.setattr(bench, "benchmark_audio_manifest", fake_manifest)
    monkeypatch.setattr(
        "sys.argv",
        [
            "benchmark",
            "--audio-manifest",
            str(manifest),
            "--proxy-only",
            "--skip-validation",
            "--output",
            str(output),
        ],
    )

    bench.main()

    assert calls == {"single": 0, "manifest": 1}


def test_compare_benchmark_suites_reports_wins_and_deltas():
    baseline = {
        "summary": {"hybrid_avg_error_after_sec": 0.05},
        "files": [
            {"audio_path": "a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.05}},
            {"audio_path": "b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.04}},
        ],
    }
    candidate = {
        "summary": {"hybrid_avg_error_after_sec": 0.047},
        "files": [
            {"audio_path": "a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.045}},
            {"audio_path": "b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.049}},
        ],
    }

    result = bench.compare_benchmark_suites(baseline, candidate, baseline_label="active", candidate_label="candidate")

    assert result["shared_file_count"] == 2
    assert result["candidate_wins"] == 1
    assert result["baseline_wins"] == 1
    assert result["ties"] == 0
    assert result["avg_delta_candidate_minus_baseline_sec"] == pytest.approx(0.002)
    assert result["max_candidate_regression_sec"] == pytest.approx(0.009)
    assert result["max_candidate_improvement_sec"] == pytest.approx(-0.005)


def test_evaluate_promotion_gate_passes_with_strong_legacy_gain_and_small_mixed_regression():
    legacy_baseline = {
        "summary": {"hybrid_avg_error_after_sec": 0.06},
        "files": [
            {"audio_path": "legacy_a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.06}},
            {"audio_path": "legacy_b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.05}},
        ],
    }
    legacy_candidate = {
        "summary": {"hybrid_avg_error_after_sec": 0.057},
        "files": [
            {"audio_path": "legacy_a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.058}},
            {"audio_path": "legacy_b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.049}},
        ],
    }
    mixed_baseline = {
        "summary": {"hybrid_avg_error_after_sec": 0.03},
        "files": [
            {"audio_path": "mix_a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.03}},
            {"audio_path": "mix_b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.031}},
        ],
    }
    mixed_candidate = {
        "summary": {"hybrid_avg_error_after_sec": 0.0304},
        "files": [
            {"audio_path": "mix_a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.0301}},
            {"audio_path": "mix_b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.0315}},
        ],
    }

    result = bench.evaluate_promotion_gate(
        legacy_baseline,
        legacy_candidate,
        mixed_baseline,
        mixed_candidate,
        min_legacy_improvement_sec=0.001,
        max_mixed_regression_sec=0.0005,
        max_per_file_regression_sec=0.003,
    )

    assert result["passed"] is True
    assert result["legacy"]["avg_improvement_sec"] == pytest.approx(0.0015)
    assert result["mixed"]["avg_regression_sec"] == pytest.approx(0.0003)


def test_evaluate_promotion_gate_fails_when_legacy_gain_is_too_small_or_regression_too_large():
    legacy_baseline = {
        "summary": {"hybrid_avg_error_after_sec": 0.06},
        "files": [
            {"audio_path": "legacy_a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.06}},
            {"audio_path": "legacy_b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.05}},
        ],
    }
    legacy_candidate = {
        "summary": {"hybrid_avg_error_after_sec": 0.05975},
        "files": [
            {"audio_path": "legacy_a.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.0645}},
            {"audio_path": "legacy_b.wav", "hybrid_effective": {"avg_abs_error_after_sec": 0.045}},
        ],
    }

    result = bench.evaluate_promotion_gate(
        legacy_baseline,
        legacy_candidate,
        min_legacy_improvement_sec=0.001,
        max_per_file_regression_sec=0.003,
    )

    assert result["passed"] is False
    assert result["legacy"]["avg_improvement_sec"] == pytest.approx(0.00025)
    assert result["legacy"]["max_regression_sec"] == pytest.approx(0.0045)


def test_evaluate_benchmark_candidate_gate_passes_with_small_regression_and_speedup():
    baseline = {
        "summary": {"hybrid_avg_error_after_sec": 0.045},
        "files": [
            {
                "audio_path": "a.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.05},
                "processing_timing": {"elapsed_sec": 10.0},
            },
            {
                "audio_path": "b.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.04},
                "processing_timing": {"elapsed_sec": 10.0},
            },
        ],
    }
    candidate = {
        "summary": {"hybrid_avg_error_after_sec": 0.0455},
        "files": [
            {
                "audio_path": "a.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.0505},
                "processing_timing": {"elapsed_sec": 6.0},
            },
            {
                "audio_path": "b.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.0405},
                "processing_timing": {"elapsed_sec": 6.0},
            },
        ],
    }

    result = bench.evaluate_benchmark_candidate_gate(
        baseline,
        candidate,
        max_avg_regression_sec=0.001,
        max_per_file_regression_sec=0.003,
        min_speedup_pct=20.0,
    )

    assert result["passed"] is True
    assert result["observed"]["avg_regression_sec"] == pytest.approx(0.0005)
    assert result["observed"]["max_per_file_regression_sec"] == pytest.approx(0.0005)
    assert result["observed"]["speedup_pct"] == pytest.approx(40.0)
    assert result["failure_summary"] == []


def test_evaluate_benchmark_candidate_gate_fails_large_regression_or_slowdown():
    baseline = {
        "summary": {"hybrid_avg_error_after_sec": 0.045},
        "files": [
            {
                "audio_path": "a.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.05},
                "processing_timing": {"elapsed_sec": 10.0},
            },
            {
                "audio_path": "b.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.04},
                "processing_timing": {"elapsed_sec": 10.0},
            },
        ],
    }
    candidate = {
        "summary": {"hybrid_avg_error_after_sec": 0.048},
        "files": [
            {
                "audio_path": "a.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.055},
                "processing_timing": {"elapsed_sec": 10.5},
            },
            {
                "audio_path": "b.wav",
                "hybrid_effective": {"avg_abs_error_after_sec": 0.041},
                "processing_timing": {"elapsed_sec": 10.5},
            },
        ],
    }

    result = bench.evaluate_benchmark_candidate_gate(
        baseline,
        candidate,
        max_avg_regression_sec=0.001,
        max_per_file_regression_sec=0.003,
        min_speedup_pct=20.0,
    )

    assert result["passed"] is False
    assert result["observed"]["avg_regression_sec"] == pytest.approx(0.003)
    assert result["observed"]["max_per_file_regression_sec"] == pytest.approx(0.005)
    assert result["observed"]["speedup_pct"] == pytest.approx(-5.0)
    assert result["failure_summary"] == ["avg_regression", "max_per_file_regression", "speedup"]


def test_benchmark_metronome_canary_returns_report_summary(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "canary.wav"
    audio_path.write_bytes(b"fake")
    outputs_dir = Path("outputs")
    job_id = "test-canary-job"
    report_dir = outputs_dir / job_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "target_bpm": 104.0,
        "mode_effective": "hybrid",
        "warp_selection": "baseline",
        "effective_groove_preserve": 20,
        "output_files": {"report_json": "report.json", "metronome_check_audio": "metronome_check.wav"},
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": _expected_runtime_config(),
        "timing_metrics": {"avg_abs_error_after_sec": 0.04},
        "metronome_lock": {"verdict": "drifts_late", "first_meltdown_sec": 94.4},
        "candidate_metrics": {"baseline": {"avg_abs_error_after_sec": 0.04}},
        "processing_timing": {"stages": {"load_audio": 1.23}},
    }
    (report_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")

    class FakeResponse:
        def __init__(self, status_code, payload=None, text=""):
            self.status_code = status_code
            self._payload = payload or {}
            self.text = text

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, app):
            self.app = app

        def post(self, path, files=None, json=None):
            if path == "/api/upload":
                return FakeResponse(200, {"job_id": job_id})
            if path == "/api/quantize":
                return FakeResponse(200, {"output_files": {"report_json": "report.json"}})
            raise AssertionError(path)

    monkeypatch.setattr(bench, "TestClient", FakeClient)

    result = bench.benchmark_metronome_canary(audio_path, target_bpm=104.0)

    assert result["job_id"] == job_id
    assert result["metronome_lock"]["verdict"] == "drifts_late"
    assert result["output_files"]["metronome_check_audio"] == "metronome_check.wav"
    assert result["metronome_check"]["target_bpm"] == 104.0
    assert result["daw_lock_diagnostics"]["issue_count"] == 0
    assert result["runtime_config"] == _expected_runtime_config()
    assert result["effective_groove_preserve"] == 20
    assert result["processing_timing"]["stages"]["load_audio"] == 1.23
    assert result["wall_time_sec"] >= 0.0


def test_benchmark_metronome_canary_manifest_uses_filename_bpm_and_gates(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track 104 bpm.wav"
    audio.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "path": str(audio),
                        "relative_path": audio.name,
                        "filename_bpm": 104.0,
                        "duration_sec": 120.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append((path, target_bpm, resolution, groove_preserve, mode))
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "fixed_windows": {
                    "locked_ratio": 1.0,
                    "unstable_windows": 0,
                    "meltdown_windows": 0,
                },
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, resolution=16, groove_preserve=45)

    assert calls == [(audio, 104.0, 16, 45, "hybrid")]
    assert result["summary"]["all_passed"] is True
    assert result["summary"]["requested_track_count"] == 1
    assert result["summary"]["pass_count"] == 1
    assert result["files"][0]["manifest_track"]["target_bpm_source"] == "filename"
    assert result["files"][0]["metronome_canary_gate"]["passed"] is True


def test_benchmark_metronome_canary_manifest_skips_obvious_non_song_montages(tmp_path: Path, monkeypatch):
    song = tmp_path / "real song 104 bpm.wav"
    montage = tmp_path / "Evolution of Race Start & Goals in Mario Kart (1992-2017).wav"
    song.write_bytes(b"fake")
    montage.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"path": str(song), "relative_path": song.name, "filename_bpm": 104.0},
                    {"path": str(montage), "relative_path": montage.name, "filename_bpm": 128.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append(path)
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.02},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "fixed_windows": {"locked_ratio": 1.0, "unstable_windows": 0, "meltdown_windows": 0},
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest)

    assert calls == [song]
    assert result["summary"]["requested_track_count"] == 2
    assert result["summary"]["file_count"] == 1
    assert result["summary"]["pass_count"] == 1
    assert result["summary"]["skipped_non_song_count"] == 1
    assert result["summary"]["all_passed"] is True
    assert result["skipped_files"][0]["relative_path"] == montage.name
    assert result["skipped_files"][0]["reason"] == "game_cue_montage_not_full_song"


def test_benchmark_metronome_canary_manifest_detects_bpm_when_filename_bpm_missing(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [{"path": str(audio), "relative_path": audio.name}]}), encoding="utf-8")
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append((path, target_bpm))
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.02},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "beat_phase": {"locked": True, "intro_locked": True},
                "fixed_windows": {
                    "locked_ratio": 1.0,
                    "unstable_windows": 0,
                    "meltdown_windows": 0,
                },
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(
        bench,
        "_select_manifest_track_bpm_from_audio",
        lambda path, *, resolution=8, seed_bpms=None: (97.5, {"detected_bpm": 97.5, "selected_bpm": 97.5, "candidates": []}),
    )
    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, skip_audio_errors=True)

    assert calls == [(audio, 97.5)]
    assert result["summary"]["file_count"] == 1
    assert result["summary"]["failed_file_count"] == 0
    assert result["files"][0]["manifest_track"]["target_bpm_source"] == "detected"
    assert result["files"][0]["manifest_track"]["detected_bpm"] == 97.5
    assert result["files"][0]["manifest_track"]["selected_detected_bpm"] == 97.5


def test_benchmark_metronome_canary_manifest_verifies_metadata_bpm(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "path": str(audio),
                        "relative_path": audio.name,
                        "metadata_tags": {"tbpm": "163.01"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append((path, target_bpm))
        return {
            "audio_path": str(path),
            "target_bpm": round(float(target_bpm)),
            "timing_metrics": {"avg_abs_error_after_sec": 0.02},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": round(float(target_bpm))},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "beat_phase": {"locked": True, "intro_locked": True},
                "fixed_windows": {
                    "locked_ratio": 1.0,
                    "unstable_windows": 0,
                    "meltdown_windows": 0,
                },
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    def fake_select(path, *, resolution=8, seed_bpms=None):
        assert seed_bpms == [163.01]
        return 163.0, {
            "detected_bpm": 107.66,
            "selected_bpm": 163.0,
            "seed_bpms": [163.01, 107.66],
            "selected_seed_source": "seed",
            "candidates": [],
        }

    monkeypatch.setattr(bench, "_select_manifest_track_bpm_from_audio", fake_select)
    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, skip_audio_errors=True)

    assert calls == [(audio, 163.0)]
    assert result["summary"]["file_count"] == 1
    assert result["files"][0]["manifest_track"]["target_bpm_source"] == "metadata"
    assert result["files"][0]["manifest_track"]["metadata_bpm"] == 163.01


def test_benchmark_metronome_canary_manifest_reads_serato_autogain_bpm(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    audio = tmp_path / "track.m4a"
    audio.write_bytes(b"fake")
    autgain = "YXBwbGljYXRpb24vb2N0ZXQtc3RyZWFtAABTZXJhdG8gQXV0b3RhZ3MAAQExMjguMDAALTIuNTE2ADAuMDAA"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {
                        "path": str(audio),
                        "relative_path": "track.m4a",
                        "metadata_tags": {"autgain": autgain},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fake_select(path, *, resolution=8, seed_bpms=None):
        assert seed_bpms == [128.0]
        return 128.0, {"detected_bpm": 129.0, "selected_bpm": 128.0, "candidates": []}

    def fake_canary(audio_path, **kwargs):
        return {
            "target_bpm": kwargs["target_bpm"],
            "processing_timing": {"elapsed_before_report_write_sec": 5.0},
            "runtime_config": _expected_runtime_config(),
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": kwargs["target_bpm"]},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "timing_metrics": {"avg_abs_error_after_sec": 0.01},
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "beat_phase": {"locked": True, "intro_locked": True},
                "warp_continuity": {"verdict": "continuous"},
                "fixed_windows": {"locked_ratio": 1.0, "unstable_windows": 0, "meltdown_windows": 0},
            },
        }

    monkeypatch.setattr(bench, "_select_manifest_track_bpm_from_audio", fake_select)
    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, skip_audio_errors=True)

    assert result["files"][0]["manifest_track"]["target_bpm_source"] == "metadata"
    assert result["files"][0]["manifest_track"]["metadata_bpm"] == 128.0
    assert result["files"][0]["manifest_track"]["detected_bpm"] == 129.0
    assert result["files"][0]["manifest_track"]["selected_detected_bpm"] == 128.0


def test_benchmark_metronome_canary_manifest_allows_metadata_alias_when_grid_scores_better(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"tracks": [{"path": str(audio), "relative_path": audio.name, "metadata_tags": {"tbpm": "88.51"}}]}),
        encoding="utf-8",
    )
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append((path, target_bpm))
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.018},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "beat_phase": {"locked": True, "intro_locked": True},
                "fixed_windows": {"locked_ratio": 1.0, "unstable_windows": 0, "meltdown_windows": 0},
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(
        bench,
        "_select_manifest_track_bpm_from_audio",
        lambda path, *, resolution=8, seed_bpms=None: (
            177.0,
            {"detected_bpm": 89.0, "selected_bpm": 177.0, "seed_bpms": [88.51, 89.0], "candidates": []},
        ),
    )
    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, skip_audio_errors=True)

    assert calls == [(audio, 177.0)]
    assert result["files"][0]["manifest_track"]["target_bpm_source"] == "metadata_alias"
    assert result["files"][0]["manifest_track"]["metadata_bpm"] == 88.51


def test_select_manifest_track_bpm_from_audio_chooses_best_alias(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.zeros((1000, 1), dtype=np.float32), 1000))
    monkeypatch.setattr(bench, "mixdown_mono", lambda audio: audio[:, 0])
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 99.384)
    monkeypatch.setattr(bench, "onset_envelope", lambda mono, sr: (np.ones(8, dtype=np.float32), 512))
    monkeypatch.setattr(bench, "detect_onsets_from_envelope", lambda envelope, hop, sr, units="time": np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(
        bench,
        "onset_quantize_curve",
        lambda mono, sr, bpm, resolution: {
            "source_times": np.array([0.0, 1.0], dtype=np.float32),
            "target_times": np.array([0.0, 1.0], dtype=np.float32),
        },
    )
    monkeypatch.setattr(bench, "build_grid", lambda duration, bpm, resolution: np.array([0.0, 0.5, 1.0], dtype=np.float32))

    def fake_metrics(source_times, target_times, grid, onsets_before):
        return {
            "avg_abs_error_after_sec": 0.015 if getattr(fake_metrics, "bpm", 99.0) == 149.0 else 0.07,
            "improvement_pct": 70.0,
            "phase_window_abs_max_after_sec": 0.01,
            "phase_window_span_after_sec": 0.005,
        }

    def fake_curve(mono, sr, bpm, resolution):
        fake_metrics.bpm = float(bpm)
        return {"source_times": np.array([0.0, 1.0], dtype=np.float32), "target_times": np.array([0.0, 1.0], dtype=np.float32)}

    monkeypatch.setattr(bench, "onset_quantize_curve_from_features", lambda onset_env, hop, sr, duration_sec, bpm, resolution: fake_curve(None, sr, bpm, resolution))
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_metrics)

    bpm, diagnostics = bench._select_manifest_track_bpm_from_audio(audio, resolution=8)

    assert bpm == 149.0
    assert diagnostics["detected_bpm"] == 99.384
    assert diagnostics["selected_bpm"] == 149.0


def test_select_manifest_track_bpm_from_audio_keeps_seed_when_alias_gain_is_tiny(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.zeros((1000, 1), dtype=np.float32), 1000))
    monkeypatch.setattr(bench, "mixdown_mono", lambda audio: audio[:, 0])
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 156.605)
    monkeypatch.setattr(bench, "onset_envelope", lambda mono, sr: (np.ones(8, dtype=np.float32), 512))
    monkeypatch.setattr(bench, "detect_onsets_from_envelope", lambda envelope, hop, sr, units="time": np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(
        bench,
        "onset_quantize_curve",
        lambda mono, sr, bpm, resolution: {
            "source_times": np.array([0.0, 1.0], dtype=np.float32),
            "target_times": np.array([0.0, 1.0], dtype=np.float32),
        },
    )
    monkeypatch.setattr(bench, "build_grid", lambda duration, bpm, resolution: np.array([0.0, 0.5, 1.0], dtype=np.float32))

    def fake_metrics(source_times, target_times, grid, onsets_before):
        bpm = getattr(fake_metrics, "bpm", 155.0)
        return {
            "avg_abs_error_after_sec": 0.0128 if bpm == 157.0 else 0.0134,
            "improvement_pct": 70.0,
            "phase_window_abs_max_after_sec": 0.011,
            "phase_window_span_after_sec": 0.006,
        }

    def fake_curve(mono, sr, bpm, resolution):
        fake_metrics.bpm = float(bpm)
        return {"source_times": np.array([0.0, 1.0], dtype=np.float32), "target_times": np.array([0.0, 1.0], dtype=np.float32)}

    monkeypatch.setattr(bench, "onset_quantize_curve_from_features", lambda onset_env, hop, sr, duration_sec, bpm, resolution: fake_curve(None, sr, bpm, resolution))
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_metrics)

    bpm, diagnostics = bench._select_manifest_track_bpm_from_audio(audio, resolution=8, seed_bpms=[155.0])

    assert bpm == 155.0
    assert diagnostics["selected_bpm"] == 155.0


def test_select_manifest_track_bpm_from_audio_keeps_primary_seed_over_tiny_seed_alias_gain(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.zeros((1000, 1), dtype=np.float32), 1000))
    monkeypatch.setattr(bench, "mixdown_mono", lambda audio: audio[:, 0])
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 129.2)
    monkeypatch.setattr(bench, "onset_envelope", lambda mono, sr: (np.ones(8, dtype=np.float32), 512))
    monkeypatch.setattr(bench, "detect_onsets_from_envelope", lambda envelope, hop, sr, units="time": np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(bench, "build_grid", lambda duration, bpm, resolution: np.array([0.0, 0.5, 1.0], dtype=np.float32))

    def fake_curve(mono, sr, bpm, resolution):
        fake_metrics.bpm = float(bpm)
        return {"source_times": np.array([0.0, 1.0], dtype=np.float32), "target_times": np.array([0.0, 1.0], dtype=np.float32)}

    def fake_metrics(source_times, target_times, grid, onsets_before):
        bpm = getattr(fake_metrics, "bpm", 128.0)
        return {
            "avg_abs_error_after_sec": 0.0390 if bpm == 192.0 else (0.0401 if bpm == 128.0 else 0.06),
            "improvement_pct": 8.0,
            "phase_window_abs_max_after_sec": 0.011,
            "phase_window_span_after_sec": 0.006,
        }

    monkeypatch.setattr(bench, "onset_quantize_curve_from_features", lambda onset_env, hop, sr, duration_sec, bpm, resolution: fake_curve(None, sr, bpm, resolution))
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_metrics)

    bpm, diagnostics = bench._select_manifest_track_bpm_from_audio(audio, resolution=8, seed_bpms=[128.0])

    assert bpm == 128.0
    assert diagnostics["selected_bpm"] == 128.0


def test_select_manifest_track_bpm_from_audio_prefers_stable_fixed_windows_over_average_error(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.zeros((1000, 1), dtype=np.float32), 1000))
    monkeypatch.setattr(bench, "mixdown_mono", lambda audio: audio[:, 0])
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 172.2)
    monkeypatch.setattr(bench, "onset_envelope", lambda mono, sr: (np.ones(8, dtype=np.float32), 512))
    monkeypatch.setattr(bench, "detect_onsets_from_envelope", lambda envelope, hop, sr, units="time": np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(bench, "build_grid", lambda duration, bpm, resolution: np.array([0.0, 0.5, 1.0], dtype=np.float32))

    def fake_curve(onset_env, hop, sr, duration_sec, bpm, resolution):
        fake_metrics.bpm = float(bpm)
        return {"source_times": np.array([0.0, 1.0], dtype=np.float32), "target_times": np.array([0.0, 1.0], dtype=np.float32)}

    def fake_metrics(source_times, target_times, grid, onsets_before):
        bpm = getattr(fake_metrics, "bpm", 172.0)
        return {
            "avg_abs_error_after_sec": 0.018 if bpm == 172.0 else (0.057 if bpm == 86.0 else 0.08),
            "improvement_pct": 40.0,
            "phase_window_abs_max_after_sec": 0.012,
            "phase_window_span_after_sec": 0.006,
        }

    def fake_fixed_ratio(source_times, target_times, grid, onsets_before, duration_sec):
        bpm = getattr(fake_metrics, "bpm", 172.0)
        return 0.65 if bpm == 86.0 else 0.05

    monkeypatch.setattr(bench, "onset_quantize_curve_from_features", fake_curve)
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_metrics)
    monkeypatch.setattr(bench, "_fixed_window_lock_ratio_proxy", fake_fixed_ratio)

    bpm, diagnostics = bench._select_manifest_track_bpm_from_audio(audio, resolution=8, seed_bpms=[86.0])

    assert bpm == 86.0
    assert diagnostics["selected_bpm"] == 86.0
    selected = next(item for item in diagnostics["candidates"] if item["bpm"] == 86.0)
    assert selected["fixed_locked_ratio_proxy"] == 0.65


def test_select_manifest_track_bpm_from_audio_keeps_stable_metadata_grid_over_small_alias_gain(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.zeros((1000, 1), dtype=np.float32), 1000))
    monkeypatch.setattr(bench, "mixdown_mono", lambda audio: audio[:, 0])
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 126.0)
    monkeypatch.setattr(bench, "onset_envelope", lambda mono, sr: (np.ones(8, dtype=np.float32), 512))
    monkeypatch.setattr(bench, "detect_onsets_from_envelope", lambda envelope, hop, sr, units="time": np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(bench, "build_grid", lambda duration, bpm, resolution: np.array([0.0, 0.5, 1.0], dtype=np.float32))

    def fake_curve(onset_env, hop, sr, duration_sec, bpm, resolution):
        fake_metrics.bpm = float(bpm)
        return {"source_times": np.array([0.0, 1.0], dtype=np.float32), "target_times": np.array([0.0, 1.0], dtype=np.float32)}

    def fake_metrics(source_times, target_times, grid, onsets_before):
        bpm = getattr(fake_metrics, "bpm", 125.0)
        return {
            "avg_abs_error_after_sec": 0.045 if bpm == 125.0 else (0.039 if bpm == 189.0 else 0.06),
            "improvement_pct": 35.0,
            "phase_window_abs_max_after_sec": 0.012,
            "phase_window_span_after_sec": 0.006,
        }

    def fake_fixed_ratio(source_times, target_times, grid, onsets_before, duration_sec):
        bpm = getattr(fake_metrics, "bpm", 125.0)
        return 1.0 if bpm == 125.0 else (0.28 if bpm == 189.0 else 0.2)

    monkeypatch.setattr(bench, "onset_quantize_curve_from_features", fake_curve)
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_metrics)
    monkeypatch.setattr(bench, "_fixed_window_lock_ratio_proxy", fake_fixed_ratio)

    bpm, diagnostics = bench._select_manifest_track_bpm_from_audio(audio, resolution=8, seed_bpms=[124.84])

    assert bpm == 125.0
    assert diagnostics["selected_bpm"] == 125.0


def test_select_manifest_track_bpm_from_audio_prefers_seed_alias_when_scores_are_tied(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")

    monkeypatch.setattr(bench, "load_audio", lambda path: (np.zeros((1000, 1), dtype=np.float32), 1000))
    monkeypatch.setattr(bench, "mixdown_mono", lambda audio: audio[:, 0])
    monkeypatch.setattr(bench, "estimate_tempo", lambda mono, sr: 89.103)
    monkeypatch.setattr(bench, "onset_envelope", lambda mono, sr: (np.ones(8, dtype=np.float32), 512))
    monkeypatch.setattr(bench, "detect_onsets_from_envelope", lambda envelope, hop, sr, units="time": np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(bench, "build_grid", lambda duration, bpm, resolution: np.array([0.0, 0.5, 1.0], dtype=np.float32))

    def fake_curve(mono, sr, bpm, resolution):
        fake_metrics.bpm = float(bpm)
        return {"source_times": np.array([0.0, 1.0], dtype=np.float32), "target_times": np.array([0.0, 1.0], dtype=np.float32)}

    def fake_metrics(source_times, target_times, grid, onsets_before):
        bpm = getattr(fake_metrics, "bpm", 178.0)
        error = 0.0241 if bpm == 178.0 else (0.0243 if bpm == 177.0 else 0.06)
        return {
            "avg_abs_error_after_sec": error,
            "improvement_pct": 44.0,
            "phase_window_abs_max_after_sec": 0.003 if bpm == 178.0 else 0.006,
            "phase_window_span_after_sec": 0.006,
        }

    monkeypatch.setattr(bench, "onset_quantize_curve_from_features", lambda onset_env, hop, sr, duration_sec, bpm, resolution: fake_curve(None, sr, bpm, resolution))
    monkeypatch.setattr(bench, "_candidate_metrics_fast", fake_metrics)

    bpm, diagnostics = bench._select_manifest_track_bpm_from_audio(audio, resolution=8, seed_bpms=[88.51])

    assert bpm == 177.0
    assert diagnostics["selected_bpm"] == 177.0
    assert diagnostics["selected_seed_source"] == "seed"


def test_benchmark_metronome_canary_manifest_records_bpm_detection_failure_when_skipping(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tracks": [{"path": str(audio), "relative_path": audio.name}]}), encoding="utf-8")
    monkeypatch.setattr(bench, "_select_manifest_track_bpm_from_audio", lambda path, *, resolution=8, seed_bpms=None: (_ for _ in ()).throw(RuntimeError("no tempo")))
    monkeypatch.setattr(bench, "benchmark_metronome_canary", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("skip")))

    result = bench.benchmark_metronome_canary_manifest(manifest, skip_audio_errors=True)

    assert result["summary"]["file_count"] == 0
    assert result["summary"]["failed_file_count"] == 1
    assert result["failed_files"][0]["error"] == "detect_target_bpm_failed"


def test_benchmark_metronome_canary_manifest_can_limit_track_count(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "a 100 bpm.wav"
    audio_b = tmp_path / "b 101 bpm.wav"
    audio_a.write_bytes(b"fake")
    audio_b.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"path": str(audio_a), "relative_path": audio_a.name, "filename_bpm": 100.0},
                    {"path": str(audio_b), "relative_path": audio_b.name, "filename_bpm": 101.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append(path)
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "fixed_windows": {"locked_ratio": 1.0, "unstable_windows": 0, "meltdown_windows": 0},
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, max_files=1)

    assert calls == [audio_a]
    assert result["summary"]["requested_track_count"] == 1
    assert result["summary"]["file_count"] == 1


def test_benchmark_metronome_canary_manifest_writes_incremental_report(tmp_path: Path, monkeypatch):
    audio = tmp_path / "track 104 bpm.wav"
    audio.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    partial = tmp_path / "partial.json"
    manifest.write_text(json.dumps({"tracks": [{"path": str(audio), "relative_path": audio.name, "filename_bpm": 104.0}]}), encoding="utf-8")

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "fixed_windows": {"locked_ratio": 1.0, "unstable_windows": 0, "meltdown_windows": 0},
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    bench.benchmark_metronome_canary_manifest(manifest, incremental_output=partial)

    saved = json.loads(partial.read_text(encoding="utf-8"))
    suite = saved["metronome_canary_suite"]
    assert suite["summary"]["file_count"] == 1
    assert suite["files"][0]["metronome_canary_gate"]["passed"] is True


def test_benchmark_metronome_canary_manifest_resumes_existing_results(tmp_path: Path, monkeypatch):
    audio_a = tmp_path / "a 100 bpm.wav"
    audio_b = tmp_path / "b 101 bpm.wav"
    audio_a.write_bytes(b"fake")
    audio_b.write_bytes(b"fake")
    manifest = tmp_path / "manifest.json"
    partial = tmp_path / "partial.json"
    existing = {
        "audio_path": str(audio_a),
        "target_bpm": 100.0,
        "metronome_canary_gate": {"passed": True},
    }
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    {"path": str(audio_a), "relative_path": audio_a.name, "filename_bpm": 100.0},
                    {"path": str(audio_b), "relative_path": audio_b.name, "filename_bpm": 101.0},
                ]
            }
        ),
        encoding="utf-8",
    )
    partial.write_text(json.dumps({"metronome_canary_suite": {"files": [existing]}}), encoding="utf-8")
    calls = []

    def fake_canary(path, *, target_bpm, resolution=8, groove_preserve=50, mode="hybrid"):
        calls.append(path)
        return {
            "audio_path": str(path),
            "target_bpm": target_bpm,
            "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": target_bpm},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "warp_continuity": {"verdict": "continuous"},
                "fixed_windows": {"locked_ratio": 1.0, "unstable_windows": 0, "meltdown_windows": 0},
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.0},
        }

    monkeypatch.setattr(bench, "benchmark_metronome_canary", fake_canary)

    result = bench.benchmark_metronome_canary_manifest(manifest, incremental_output=partial, resume=True)

    assert calls == [audio_b]
    assert result["files"][0] == existing
    assert result["summary"]["file_count"] == 2


def test_evaluate_metronome_canary_gate_requires_full_daw_lock():
    report = {
        "metronome_canary": {
            "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
            "target_bpm": 104.0,
            "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
            "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
            "runtime_config": _expected_runtime_config(),
            "metronome_lock": {
                "verdict": "daw_locked",
                "locked_ratio": 1.0,
                "unstable_segments": 0,
                "meltdown_segments": 0,
                "fixed_windows": {
                    "locked_ratio": 1.0,
                    "unstable_windows": 0,
                    "meltdown_windows": 0,
                },
            },
            "processing_timing": {"elapsed_before_report_write_sec": 82.1},
        },
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["failure_summary"] == "Metronome canary gate passed."
    assert result["failed_checks"] == []
    assert result["checks"]["segment_locked_ratio"] is True
    assert result["checks"]["fixed_locked_ratio"] is True
    assert result["checks"]["metronome_check"] is True
    assert result["checks"]["daw_lock_diagnostics"] is True
    assert result["checks"]["runtime_config"] is True


def test_evaluate_metronome_canary_gate_fails_on_partial_lock_or_slow_run():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "target_bpm": 104.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 0.9831,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 144.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is False
    assert result["checks"]["segment_locked_ratio"] is False
    assert result["checks"]["elapsed_sec"] is False
    assert result["failed_checks"] == ["segment_locked_ratio", "elapsed_sec"]
    assert result["failure_summary"] == "Metronome canary gate failed checks: segment_locked_ratio, elapsed_sec."


def test_evaluate_metronome_canary_gate_uses_effective_segment_ratio():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0234},
        "target_bpm": 171.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 171.0},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 0.8182,
            "effective_locked_ratio": 1.0,
            "excluded_segments": 6,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "avg_abs_error_after_sec": 0.001,
                "intro_avg_abs_error_after_sec": 0.001,
                "locked": True,
                "intro_locked": True,
            },
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 63.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["segment_locked_ratio"] is True
    assert result["observed"]["segment_locked_ratio"] == 1.0
    assert result["observed"]["raw_segment_locked_ratio"] == 0.8182
    assert result["observed"]["excluded_segments"] == 6


def test_evaluate_metronome_canary_gate_allows_fixed_grid_authoritative_segment_ratio():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0234},
        "target_bpm": 171.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 171.0},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 0.8182,
            "effective_locked_ratio": 0.8929,
            "excluded_segments": 5,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "avg_abs_error_after_sec": 0.001,
                "intro_avg_abs_error_after_sec": 0.001,
                "locked": True,
                "intro_locked": True,
            },
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 63.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["segment_locked_ratio"] is True
    assert result["observed"]["segment_locked_ratio"] == 0.8929
    assert result["observed"]["segment_lock_fixed_grid_authoritative"] is True


def test_evaluate_metronome_canary_gate_allows_fixed_grid_authority_with_sparse_segment_warnings():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.011},
        "target_bpm": 172.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 172.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_grid_authoritative", "severity": "info"}],
            "summary": "DAW-lock note: fixed-grid lock is authoritative; beat tracker appears ambiguous.",
        },
        "runtime_config": _expected_runtime_config(),
        "processing_timing": {"elapsed_before_report_write_sec": 88.0},
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 0.8,
            "effective_locked_ratio": 0.7917,
            "unstable_segments": 1,
            "meltdown_segments": 1,
            "fixed_grid_authoritative": True,
            "beat_phase": {
                "avg_abs_error_after_sec": 0.044,
                "intro_avg_abs_error_after_sec": 0.014,
                "grid_authoritative": True,
                "locked": False,
                "intro_locked": True,
            },
            "warp_continuity": {"verdict": "watch"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["unstable_segments"] is True
    assert result["checks"]["meltdown_segments"] is True
    assert result["observed"]["segment_lock_fixed_grid_authoritative"] is True


def test_evaluate_metronome_canary_gate_allows_small_avg_error_overage_with_perfect_fixed_grid():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0392},
        "target_bpm": 128.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 128.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_offbeat_alias", "severity": "info"}],
            "summary": "DAW-lock note: beat tracker is following an eighth-note offbeat alias.",
        },
        "runtime_config": _expected_runtime_config(),
        "processing_timing": {"elapsed_before_report_write_sec": 118.0},
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 0.9778,
            "effective_locked_ratio": 0.9778,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "beat_count": 530,
                "avg_abs_error_after_sec": 0.185,
                "intro_avg_abs_error_after_sec": 0.234,
                "median_signed_error_after_sec": 0.234,
                "offbeat_alias": True,
                "locked": False,
                "intro_locked": False,
            },
            "warp_continuity": {"verdict": "watch"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["avg_error_after"] is True
    assert result["observed"]["segment_lock_fixed_grid_authoritative"] is True


def test_evaluate_metronome_canary_gate_allows_authoritative_alias_with_larger_avg_overage():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0423},
        "target_bpm": 125.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 125.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_grid_authoritative", "severity": "info"}],
            "summary": "DAW-lock note: fixed-grid lock is authoritative; beat tracker appears ambiguous.",
        },
        "runtime_config": _expected_runtime_config(),
        "processing_timing": {"elapsed_before_report_write_sec": 119.0},
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "effective_locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "fixed_grid_authoritative": True,
            "beat_phase": {
                "beat_count": 487,
                "avg_abs_error_after_sec": 0.091,
                "intro_avg_abs_error_after_sec": 0.018,
                "median_signed_error_after_sec": 0.0,
                "grid_authoritative": True,
                "locked": False,
                "intro_locked": True,
            },
            "warp_continuity": {
                "verdict": "subdivision_alias",
                "subdivision_alias_jump": True,
                "max_window_offset_jump_sec": 0.218,
                "grid_step_sec": 0.24,
            },
            "fixed_windows": {
                "locked_ratio": 1.0,
                "effective_locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["avg_error_after"] is True
    assert result["checks"]["warp_continuity"] is True
    assert result["observed"]["segment_lock_fixed_grid_authoritative"] is True


def test_evaluate_metronome_canary_gate_allows_low_error_near_full_lock():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0103},
        "target_bpm": 186.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 186.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_offbeat_alias", "severity": "info"}],
            "summary": "DAW-lock note: beat tracker is following an eighth-note offbeat alias.",
        },
        "runtime_config": _expected_runtime_config(),
        "processing_timing": {"elapsed_before_report_write_sec": 118.0},
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 0.9756,
            "effective_locked_ratio": 0.9744,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "beat_count": 320,
                "avg_abs_error_after_sec": 0.149,
                "intro_avg_abs_error_after_sec": 0.077,
                "median_signed_error_after_sec": 0.161,
                "offbeat_alias": True,
                "locked": False,
                "intro_locked": False,
            },
            "warp_continuity": {"verdict": "watch"},
            "fixed_windows": {
                "locked_ratio": 0.9714,
                "effective_locked_ratio": 0.9714,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["segment_locked_ratio"] is True
    assert result["observed"]["segment_lock_fixed_grid_authoritative"] is True


def test_evaluate_metronome_canary_gate_fails_on_warp_jump_risk():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "target_bpm": 104.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "warp_continuity_jump", "severity": "high"}],
            "summary": "DAW-lock risk: a warp-continuity jump was detected; inspect the reported timestamp in the grid-check render.",
        },
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {
                "verdict": "jump_risk",
                "max_window_offset_jump_sec": 0.35,
                "max_window_offset_jump_from_sec": 46.1,
                "max_window_offset_jump_to_sec": 50.7,
                "max_window_offset_before_sec": 0.0,
                "max_window_offset_after_sec": 0.35,
            },
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is False
    assert result["failed_checks"] == ["warp_continuity", "daw_lock_diagnostics"]
    assert result["failure_summary"] == "Metronome canary gate failed checks: warp_continuity, daw_lock_diagnostics."
    assert result["checks"]["warp_continuity"] is False
    assert result["checks"]["daw_lock_diagnostics"] is False
    assert result["observed"]["max_window_offset_jump_from_sec"] == 46.1
    assert result["observed"]["max_window_offset_jump_to_sec"] == 50.7
    assert result["observed"]["max_window_offset_after_sec"] == 0.35
    assert result["observed"]["first_daw_lock_diagnostic_type"] == "warp_continuity_jump"
    assert result["observed"]["first_daw_lock_diagnostic_severity"] == "high"


def test_evaluate_metronome_canary_gate_fails_on_beat_phase_shift():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "target_bpm": 104.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_shift", "severity": "medium"}],
            "summary": "DAW-lock risk: musical beat phase is shifted against the quarter-note metronome.",
        },
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "mostly_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "avg_abs_error_after_sec": 0.28845,
                "intro_avg_abs_error_after_sec": 0.28368,
                "median_signed_error_after_sec": 0.28845,
                "locked": False,
                "intro_locked": False,
            },
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is False
    assert result["failed_checks"] == ["verdict", "beat_phase", "daw_lock_diagnostics"]
    assert result["checks"]["beat_phase"] is False
    assert result["observed"]["beat_phase_median_signed_sec"] == 0.28845


def test_evaluate_metronome_canary_gate_allows_offbeat_alias():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "target_bpm": 104.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_offbeat_alias", "severity": "info"}],
            "summary": "DAW-lock note: beat tracker is following an eighth-note offbeat alias.",
        },
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "avg_abs_error_after_sec": 0.28845,
                "intro_avg_abs_error_after_sec": 0.28368,
                "median_signed_error_after_sec": 0.28845,
                "offbeat_alias": True,
                "locked": False,
                "intro_locked": False,
            },
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["beat_phase"] is True
    assert result["checks"]["daw_lock_diagnostics"] is True
    assert result["observed"]["beat_phase_offbeat_alias"] is True
    assert result["observed"]["daw_lock_blocking_diagnostic_count"] == 0


def test_evaluate_metronome_canary_gate_allows_grid_authoritative_beat_phase():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0248},
        "target_bpm": 128.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 128.0},
        "daw_lock_diagnostics": {
            "issue_count": 1,
            "items": [{"type": "beat_phase_grid_authoritative", "severity": "info"}],
            "summary": "DAW-lock note: fixed-grid lock is authoritative; beat tracker appears ambiguous.",
        },
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "beat_phase": {
                "avg_abs_error_after_sec": 0.164,
                "intro_avg_abs_error_after_sec": 0.094,
                "median_signed_error_after_sec": 0.154,
                "grid_authoritative": True,
                "locked": False,
                "intro_locked": False,
            },
            "warp_continuity": {"verdict": "watch"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 120.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["checks"]["beat_phase"] is True
    assert result["observed"]["beat_phase_grid_authoritative"] is True
    assert result["observed"]["daw_lock_blocking_diagnostic_count"] == 0


def test_evaluate_metronome_canary_gate_uses_effective_fixed_window_ratio():
    report = {
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "target_bpm": 104.0,
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 0.9688,
                "effective_locked_ratio": 1.0,
                "excluded_windows": 1,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, min_fixed_locked_ratio=1.0, max_elapsed_sec=130.0)

    assert result["passed"] is True
    assert result["observed"]["fixed_locked_ratio"] == 1.0
    assert result["observed"]["raw_fixed_locked_ratio"] == 0.9688
    assert result["observed"]["excluded_fixed_windows"] == 1
    assert result["observed"]["metronome_check_generated"] is True
    assert result["observed"]["daw_lock_diagnostic_issue_count"] == 0


def test_evaluate_metronome_canary_gate_fails_when_grid_check_missing():
    report = {
        "target_bpm": 104.0,
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is False
    assert result["checks"]["metronome_check"] is False


def test_evaluate_metronome_canary_gate_fails_when_diagnostics_missing():
    report = {
        "target_bpm": 104.0,
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "runtime_config": _expected_runtime_config(),
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is False
    assert result["checks"]["daw_lock_diagnostics"] is False
    assert result["observed"]["daw_lock_diagnostic_issue_count"] == 999


def test_evaluate_metronome_canary_gate_fails_on_runtime_config_mismatch():
    stale_runtime = {
        **_expected_runtime_config(),
        "inference_candidate_strategy": "all",
    }
    report = {
        "target_bpm": 104.0,
        "timing_metrics": {"avg_abs_error_after_sec": 0.0338},
        "metronome_check": {"generated": True, "filename": "metronome_check.wav", "target_bpm": 104.0},
        "daw_lock_diagnostics": {"issue_count": 0, "items": [], "summary": "No DAW-lock diagnostic issues detected."},
        "runtime_config": stale_runtime,
        "metronome_lock": {
            "verdict": "daw_locked",
            "locked_ratio": 1.0,
            "unstable_segments": 0,
            "meltdown_segments": 0,
            "warp_continuity": {"verdict": "continuous"},
            "fixed_windows": {
                "locked_ratio": 1.0,
                "unstable_windows": 0,
                "meltdown_windows": 0,
            },
        },
        "processing_timing": {"elapsed_before_report_write_sec": 82.0},
    }

    result = bench.evaluate_metronome_canary_gate(report, max_elapsed_sec=130.0)

    assert result["passed"] is False
    assert result["failed_checks"] == ["runtime_config"]
    assert result["checks"]["runtime_config"] is False
    assert result["thresholds"]["expected_runtime_config"] == _expected_runtime_config()
    assert result["observed"]["runtime_config"] == stale_runtime
