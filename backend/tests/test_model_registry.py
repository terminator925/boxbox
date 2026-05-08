from __future__ import annotations

import json
from pathlib import Path

from backend.ml import infer
from backend.ml.model_registry import (
    manifest_model_path,
    model_warnings,
    print_leaderboard,
    print_validation,
    ranked_model_paths,
    registry_entries,
    sync_embedded_manifests,
    validate_manifest,
    validate_manifests,
)


def _write_manifest(
    path: Path,
    *,
    avg_mae: float | None,
    real_pct: float = 50.0,
    val_examples: int = 4,
    unknown_pct: float = 0.0,
) -> None:
    generated_pct = max(0.0, 100.0 - real_pct - unknown_pct)
    payload = {
        "status": "trained",
        "created_at": "2026-04-21T00:00:00+00:00",
        "train_dataset": {
            "examples": 8,
            "source_mix": {"generated_pct": generated_pct, "real_pct": real_pct, "unknown_pct": unknown_pct},
        },
        "val_dataset": {"examples": val_examples},
        "val_metrics": {},
    }
    if avg_mae is not None:
        payload["val_metrics"]["avg_mae"] = avg_mae
        payload["val_metrics"]["baseline_avg_mae"] = avg_mae * 2.0
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_manifest_model_path_maps_training_json_to_checkpoint():
    assert manifest_model_path(Path("models/boxbox_latest.training.json")) == Path("models/boxbox_latest.pt")


def test_registry_entries_rank_scored_models_before_unscored(tmp_path: Path):
    best_model = tmp_path / "best.pt"
    ok_model = tmp_path / "ok.pt"
    unscored_model = tmp_path / "unscored.pt"
    for path in (best_model, ok_model, unscored_model):
        path.write_bytes(b"x")
    _write_manifest(tmp_path / "ok.training.json", avg_mae=0.04, real_pct=10.0)
    _write_manifest(tmp_path / "best.training.json", avg_mae=0.02, real_pct=50.0)
    _write_manifest(tmp_path / "unscored.training.json", avg_mae=None)

    entries = registry_entries(tmp_path)

    assert [entry["model_name"] for entry in entries] == ["best.pt", "ok.pt", "unscored.pt"]
    assert entries[0]["avg_mae"] == 0.02
    assert entries[0]["real_pct"] == 50.0
    assert entries[0]["warnings"] == []


def test_registry_entries_warns_on_low_validation_evidence(tmp_path: Path):
    model = tmp_path / "thin.pt"
    model.write_bytes(b"x")
    _write_manifest(tmp_path / "thin.training.json", avg_mae=0.01, val_examples=1)

    entries = registry_entries(tmp_path, min_val_examples=4)

    assert entries[0]["warnings"] == ["low_val_examples"]
    assert model_warnings(entries[0], min_val_examples=2) == ["low_val_examples"]


def test_registry_entries_warns_on_training_source_risk(tmp_path: Path):
    synthetic_only = tmp_path / "synthetic_only.pt"
    unknown = tmp_path / "unknown.pt"
    synthetic_only.write_bytes(b"x")
    unknown.write_bytes(b"x")
    _write_manifest(tmp_path / "synthetic_only.training.json", avg_mae=0.02, real_pct=0.0)
    _write_manifest(tmp_path / "unknown.training.json", avg_mae=0.03, real_pct=25.0, unknown_pct=10.0)

    entries = {entry["model_name"]: entry for entry in registry_entries(tmp_path, min_real_pct=1.0)}

    assert "no_real_training_data" in entries["synthetic_only.pt"]["warnings"]
    assert "unknown_source_mix" in entries["unknown.pt"]["warnings"]


def test_registry_entries_warns_on_non_trained_status(tmp_path: Path):
    model = tmp_path / "smoke.pt"
    model.write_bytes(b"x")
    _write_manifest(tmp_path / "smoke.training.json", avg_mae=0.01)
    payload = json.loads((tmp_path / "smoke.training.json").read_text(encoding="utf-8"))
    payload["status"] = "smoke_test"
    (tmp_path / "smoke.training.json").write_text(json.dumps(payload), encoding="utf-8")

    entries = registry_entries(tmp_path)

    assert entries[0]["warnings"] == ["status_not_trained"]


def test_ranked_model_paths_ignores_unscored_and_limits(tmp_path: Path):
    for name, mae in (("a", 0.03), ("b", None), ("c", 0.01)):
        (tmp_path / f"{name}.pt").write_bytes(b"x")
        _write_manifest(tmp_path / f"{name}.training.json", avg_mae=mae)

    assert [path.name for path in ranked_model_paths(tmp_path, limit=1)] == ["c.pt"]


def test_inference_candidates_prefer_registry_ranked_models(tmp_path: Path):
    ranked = tmp_path / "ranked.pt"
    latest = tmp_path / "boxbox_latest.pt"
    ranked.write_bytes(b"x")
    latest.write_bytes(b"y")
    _write_manifest(tmp_path / "ranked.training.json", avg_mae=0.01)

    candidates = infer.candidate_model_paths(tmp_path)

    assert candidates[0] == ranked
    assert latest in candidates


def test_print_leaderboard_has_empty_state(capsys):
    print_leaderboard([])

    assert "No training manifests found." in capsys.readouterr().out


def test_validate_manifest_reports_missing_model_and_metrics(tmp_path: Path):
    manifest = tmp_path / "broken.training.json"
    manifest.write_text(json.dumps({"status": "dry_run", "train_dataset": {"examples": 0}}), encoding="utf-8")

    result = validate_manifest(manifest)

    assert result["valid"] is False
    assert "missing_model" in result["issues"]
    assert "status_not_trained" in result["issues"]
    assert "empty_train_dataset" in result["issues"]
    assert "missing_val_dataset" in result["issues"]
    assert "missing_val_avg_mae" in result["issues"]


def test_validate_manifest_accepts_scored_trained_manifest(tmp_path: Path):
    model = tmp_path / "good.pt"
    manifest = tmp_path / "good.training.json"
    model.write_bytes(b"x")
    _write_manifest(manifest, avg_mae=0.01)

    result = validate_manifest(manifest)

    assert result["valid"] is True
    assert result["issues"] == []


def test_validate_manifests_lists_all_training_manifests(tmp_path: Path):
    (tmp_path / "a.training.json").write_text("{bad", encoding="utf-8")
    model = tmp_path / "b.pt"
    model.write_bytes(b"x")
    _write_manifest(tmp_path / "b.training.json", avg_mae=0.01)

    results = validate_manifests(tmp_path)

    assert [Path(result["manifest"]).name for result in results] == ["a.training.json", "b.training.json"]
    assert [result["valid"] for result in results] == [False, True]


def test_print_validation_has_empty_state_and_issue_text(capsys):
    print_validation([])
    assert "No training manifests found." in capsys.readouterr().out

    print_validation([{"valid": False, "manifest": "bad.training.json", "model": "bad.pt", "issues": ["missing_model"]}])
    assert "issues=missing_model" in capsys.readouterr().out


def test_sync_embedded_manifests_dry_run_and_apply(tmp_path: Path):
    import torch

    model = tmp_path / "candidate.pt"
    manifest = {
        "status": "trained",
        "train_dataset": {
            "examples": 2,
            "source_mix": {"generated_pct": 50.0, "real_pct": 50.0, "unknown_pct": 0.0},
        },
        "val_dataset": {"examples": 1},
        "val_metrics": {"avg_mae": 0.01},
    }
    torch.save({"model": {}, "training_manifest": manifest}, model)

    dry_run = sync_embedded_manifests(tmp_path, apply=False)

    assert dry_run[0]["action"] == "write"
    assert dry_run[0]["applied"] is False
    assert not (tmp_path / "candidate.training.json").exists()

    applied = sync_embedded_manifests(tmp_path, apply=True)

    assert applied[0]["action"] == "write"
    assert applied[0]["applied"] is True
    assert json.loads((tmp_path / "candidate.training.json").read_text(encoding="utf-8"))["val_metrics"]["avg_mae"] == 0.01


def test_sync_embedded_manifests_reports_missing_embedded_manifest(tmp_path: Path):
    (tmp_path / "legacy.pt").write_bytes(b"not a torch checkpoint")

    results = sync_embedded_manifests(tmp_path, apply=True)

    assert results[0]["action"] == "skip"
    assert results[0]["reason"] == "No embedded training_manifest found."
