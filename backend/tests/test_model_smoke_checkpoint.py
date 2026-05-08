from __future__ import annotations

import json
from pathlib import Path

import torch

from backend.ml.model_compare import compare_models
from backend.ml.model_registry import registry_entries
from backend.ml.model_smoke_checkpoint import create_smoke_checkpoint


def test_create_smoke_checkpoint_writes_model_sidecar_and_embedded_manifest(tmp_path: Path):
    output = tmp_path / "boxbox_smoke.pt"

    manifest = create_smoke_checkpoint(output, feature_dim=12)

    sidecar = tmp_path / "boxbox_smoke.training.json"
    assert output.exists()
    assert sidecar.exists()
    assert manifest["status"] == "smoke_test"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["config"]["smoke_checkpoint"] is True
    checkpoint = torch.load(output, map_location="cpu", weights_only=False)
    assert checkpoint["feature_dim"] == 12
    assert checkpoint["training_manifest"]["status"] == "smoke_test"


def test_smoke_checkpoint_is_ranked_but_blocked_from_comparison(tmp_path: Path):
    active = tmp_path / "boxbox_latest.pt"
    create_smoke_checkpoint(tmp_path / "candidate.pt", avg_mae=0.01)
    create_smoke_checkpoint(active, status="trained", avg_mae=0.10)

    entries = registry_entries(tmp_path)
    result = compare_models(tmp_path)

    assert entries[0]["model_name"] == "candidate.pt"
    assert "status_not_trained" in entries[0]["warnings"]
    assert result["passed"] is False
    assert result["verdict"] == "candidate_training_data_quality_risk"
