from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.ml.model_compare import compare_model_entries, compare_models


def _entry(
    name: str,
    score: float,
    avg_mae: float = 0.02,
    val_examples: int = 4,
    real_pct: float = 50.0,
    generated_pct: float = 50.0,
    unknown_pct: float = 0.0,
) -> dict:
    return {
        "model_name": name,
        "model": name,
        "score": score,
        "avg_mae": avg_mae,
        "train_examples": 8,
        "val_examples": val_examples,
        "generated_pct": generated_pct,
        "real_pct": real_pct,
        "unknown_pct": unknown_pct,
    }


def _write_manifest(path: Path, *, avg_mae: float) -> None:
    path.with_name(path.name[: -len(".training.json")] + ".pt").write_bytes(b"x")
    path.write_text(
        json.dumps(
            {
                "status": "trained",
                "train_dataset": {
                    "examples": 8,
                    "source_mix": {"generated_pct": 50.0, "real_pct": 50.0, "unknown_pct": 0.0},
                },
                "val_dataset": {"examples": 4},
                "val_metrics": {"avg_mae": avg_mae, "baseline_avg_mae": avg_mae * 2.0},
            }
        ),
        encoding="utf-8",
    )


def test_compare_model_entries_passes_for_meaningful_score_gain():
    result = compare_model_entries(
        _entry("boxbox_latest.pt", 0.10, avg_mae=0.10),
        _entry("candidate.pt", 0.08, avg_mae=0.08),
        min_score_improvement_pct=5.0,
    )

    assert result["passed"] is True
    assert result["verdict"] == "candidate_better"
    assert result["score_improvement_pct"] == pytest.approx(20.0)
    assert result["avg_mae_improvement_pct"] == pytest.approx(20.0)


def test_compare_model_entries_fails_for_tiny_gain():
    result = compare_model_entries(
        _entry("boxbox_latest.pt", 0.10),
        _entry("candidate.pt", 0.0995),
        min_score_improvement_pct=1.0,
    )

    assert result["passed"] is False
    assert result["verdict"] == "candidate_not_better"


def test_compare_model_entries_blocks_low_validation_candidate():
    result = compare_model_entries(
        _entry("boxbox_latest.pt", 0.10),
        _entry("candidate.pt", 0.08, val_examples=1),
        min_val_examples=4,
    )

    assert result["passed"] is False
    assert result["verdict"] == "candidate_low_validation_evidence"
    assert result["candidate"]["warnings"] == ["low_val_examples"]


def test_compare_model_entries_blocks_training_source_risk():
    result = compare_model_entries(
        _entry("boxbox_latest.pt", 0.10),
        _entry("candidate.pt", 0.08, real_pct=0.0, generated_pct=100.0),
    )

    assert result["passed"] is False
    assert result["verdict"] == "candidate_training_data_quality_risk"
    assert result["candidate"]["warnings"] == ["no_real_training_data"]


def test_compare_model_entries_blocks_non_trained_candidate():
    candidate = _entry("candidate.pt", 0.08)
    candidate["status"] = "smoke_test"

    result = compare_model_entries(_entry("boxbox_latest.pt", 0.10), candidate)

    assert result["passed"] is False
    assert result["verdict"] == "candidate_training_data_quality_risk"
    assert result["candidate"]["warnings"] == ["status_not_trained"]


def test_compare_models_selects_top_non_active_candidate(tmp_path: Path):
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.05)
    _write_manifest(tmp_path / "candidate.training.json", avg_mae=0.03)
    _write_manifest(tmp_path / "weaker.training.json", avg_mae=0.04)

    result = compare_models(tmp_path, min_score_improvement_pct=10.0)

    assert result["passed"] is True
    assert result["candidate"]["model_name"] == "candidate.pt"


def test_compare_models_reports_no_active_manifest(tmp_path: Path):
    _write_manifest(tmp_path / "candidate.training.json", avg_mae=0.03)

    result = compare_models(tmp_path)

    assert result["passed"] is True
    assert result["verdict"] == "candidate_scored_no_active_manifest"


def test_compare_model_entries_treats_unscored_active_as_not_comparable():
    result = compare_model_entries(
        {"model_name": "boxbox_latest.pt", "score": float("inf"), "avg_mae": None},
        _entry("candidate.pt", 0.03, avg_mae=0.03),
    )

    assert result["passed"] is True
    assert result["verdict"] == "candidate_scored_no_active_manifest"


def test_compare_models_reports_missing_named_candidate(tmp_path: Path):
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.03)

    result = compare_models(tmp_path, candidate_name="missing.pt")

    assert result["passed"] is False
    assert result["verdict"] == "no_candidate"
