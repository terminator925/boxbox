from __future__ import annotations

import json
from pathlib import Path

from backend.ml.model_promotion import apply_promotion, promotion_plan


def _write_manifest(
    path: Path,
    *,
    avg_mae: float | None,
    val_examples: int = 4,
    real_pct: float = 50.0,
) -> None:
    val_metrics = {}
    if avg_mae is not None:
        val_metrics = {"avg_mae": avg_mae, "baseline_avg_mae": avg_mae * 2.0}
    generated_pct = 100.0 - real_pct
    path.write_text(
        json.dumps(
            {
                "status": "trained",
                "train_dataset": {
                    "examples": 8,
                    "source_mix": {"generated_pct": generated_pct, "real_pct": real_pct, "unknown_pct": 0.0},
                },
                "val_dataset": {"examples": val_examples},
                "val_metrics": val_metrics,
            }
        ),
        encoding="utf-8",
    )


def test_promotion_plan_reports_no_candidates(tmp_path: Path):
    (tmp_path / "unscored.pt").write_bytes(b"x")
    _write_manifest(tmp_path / "unscored.training.json", avg_mae=None)

    plan = promotion_plan(tmp_path)

    assert plan["action"] == "none"
    assert plan["reason"] == "No scored training manifests found."


def test_promotion_plan_noops_when_best_is_target(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    target.write_bytes(b"target")
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.01)

    plan = promotion_plan(tmp_path)

    assert plan["action"] == "none"
    assert plan["reason"] == "Top ranked model is already the target model."
    assert plan["candidate"]["model_name"] == "boxbox_latest.pt"


def test_apply_promotion_copies_best_model_and_backs_up_target(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    target_manifest = tmp_path / "boxbox_latest.training.json"
    candidate = tmp_path / "candidate.pt"
    candidate_manifest = tmp_path / "candidate.training.json"
    target.write_bytes(b"old")
    target_manifest.write_text('{"old": true}', encoding="utf-8")
    candidate.write_bytes(b"new")
    _write_manifest(candidate_manifest, avg_mae=0.01)

    plan = promotion_plan(tmp_path)
    result = apply_promotion(plan)

    assert result["applied"] is True
    assert target.read_bytes() == b"new"
    assert json.loads(target_manifest.read_text(encoding="utf-8"))["val_metrics"]["avg_mae"] == 0.01
    backup_models = list(tmp_path.glob("boxbox_latest.backup_*.pt"))
    backup_manifests = list(tmp_path.glob("boxbox_latest.training.backup_*.json"))
    assert len(backup_models) == 1
    assert backup_models[0].read_bytes() == b"old"
    assert len(backup_manifests) == 1
    assert json.loads(backup_manifests[0].read_text(encoding="utf-8")) == {"old": True}


def test_promotion_plan_requires_comparison_gate_when_active_has_manifest(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    candidate = tmp_path / "candidate.pt"
    target.write_bytes(b"old")
    candidate.write_bytes(b"new")
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.10)
    _write_manifest(tmp_path / "candidate.training.json", avg_mae=0.0995)

    plan = promotion_plan(tmp_path, min_score_improvement_pct=1.0)

    assert plan["action"] == "none"
    assert plan["reason"] == "No ranked candidate cleared the manifest comparison gate."
    assert plan["comparison"]["verdict"] == "candidate_not_better"


def test_promotion_plan_blocks_low_validation_candidate(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    candidate = tmp_path / "candidate.pt"
    target.write_bytes(b"old")
    candidate.write_bytes(b"new")
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.10)
    _write_manifest(tmp_path / "candidate.training.json", avg_mae=0.01, val_examples=1)

    plan = promotion_plan(tmp_path, min_val_examples=4)

    assert plan["action"] == "none"
    assert plan["comparison"]["verdict"] == "candidate_low_validation_evidence"


def test_promotion_plan_blocks_synthetic_only_candidate(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    candidate = tmp_path / "candidate.pt"
    target.write_bytes(b"old")
    candidate.write_bytes(b"new")
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.10)
    _write_manifest(tmp_path / "candidate.training.json", avg_mae=0.01, real_pct=0.0)

    plan = promotion_plan(tmp_path)

    assert plan["action"] == "none"
    assert plan["comparison"]["verdict"] == "candidate_training_data_quality_risk"


def test_promotion_plan_skips_blocked_candidate_and_promotes_next_safe(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    blocked = tmp_path / "blocked.pt"
    safe = tmp_path / "safe.pt"
    target.write_bytes(b"old")
    blocked.write_bytes(b"blocked")
    safe.write_bytes(b"safe")
    _write_manifest(tmp_path / "boxbox_latest.training.json", avg_mae=0.10)
    _write_manifest(tmp_path / "blocked.training.json", avg_mae=0.01, real_pct=0.0)
    _write_manifest(tmp_path / "safe.training.json", avg_mae=0.02, real_pct=50.0)

    plan = promotion_plan(tmp_path)

    assert plan["action"] == "promote"
    assert plan["candidate"]["model_name"] == "safe.pt"
    assert plan["comparison"]["verdict"] == "candidate_better"
    assert plan["rejected_candidates"][0]["candidate"]["model_name"] == "blocked.pt"
    assert plan["rejected_candidates"][0]["comparison"]["verdict"] == "candidate_training_data_quality_risk"


def test_promotion_plan_allows_scored_candidate_when_active_has_no_manifest(tmp_path: Path):
    target = tmp_path / "boxbox_latest.pt"
    candidate = tmp_path / "candidate.pt"
    target.write_bytes(b"old")
    candidate.write_bytes(b"new")
    _write_manifest(tmp_path / "candidate.training.json", avg_mae=0.01)

    plan = promotion_plan(tmp_path)

    assert plan["action"] == "promote"
    assert plan["comparison"]["verdict"] == "candidate_scored_no_active_manifest"
