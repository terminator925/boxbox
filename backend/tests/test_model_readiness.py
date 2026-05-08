from __future__ import annotations

import json
from pathlib import Path

from backend.ml.model_readiness import build_readiness_report, print_readiness_report


def _write_example(root: Path, name: str) -> None:
    example = root / name
    example.mkdir(parents=True)
    (example / "original.wav").write_bytes(b"x")
    (example / "warped.wav").write_bytes(b"y")


def _write_manifest(path: Path, *, avg_mae: float, real_pct: float = 50.0) -> None:
    model = path.with_name(path.name[: -len(".training.json")] + ".pt")
    model.write_bytes(b"x")
    path.write_text(
        json.dumps(
            {
                "status": "trained",
                "train_dataset": {
                    "examples": 8,
                    "source_mix": {
                        "generated_pct": 100.0 - real_pct,
                        "real_pct": real_pct,
                        "unknown_pct": 0.0,
                    },
                },
                "val_dataset": {"examples": 4},
                "val_metrics": {"avg_mae": avg_mae, "baseline_avg_mae": avg_mae * 2.0},
            }
        ),
        encoding="utf-8",
    )


def test_build_readiness_report_combines_dataset_registry_compare_and_promotion(tmp_path: Path):
    examples = tmp_path / "examples"
    models = tmp_path / "models"
    models.mkdir()
    _write_example(examples, "legacy_audio_0000")
    _write_manifest(models / "boxbox_latest.training.json", avg_mae=0.10)
    _write_manifest(models / "candidate.training.json", avg_mae=0.02)

    report = build_readiness_report(examples=examples, models_dir=models)

    assert report["dataset"]["examples"] == 1
    assert report["manifest_validation"][0]["valid"] is True
    assert report["leaderboard"][0]["model_name"] == "candidate.pt"
    assert report["comparison"]["verdict"] == "candidate_better"
    assert report["promotion"]["action"] == "promote"


def test_print_readiness_report_outputs_compact_status(tmp_path: Path, capsys):
    examples = tmp_path / "examples"
    models = tmp_path / "models"
    models.mkdir()
    _write_example(examples, "legacy_audio_0000")
    _write_manifest(models / "candidate.training.json", avg_mae=0.02)

    report = build_readiness_report(examples=examples, models_dir=models)
    print_readiness_report(report)

    output = capsys.readouterr().out
    assert "dataset_examples=1" in output
    assert "leaderboard_top=candidate.pt" in output
    assert "promotion_action=promote" in output
