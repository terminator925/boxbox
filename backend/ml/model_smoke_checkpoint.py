from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import torch

from backend.ml.model import BoxBoxWarpNet
from backend.ml.train import build_training_manifest, default_manifest_path, write_training_manifest


def _dataset_summary(*, examples: int, real_pct: float, unknown_pct: float) -> dict[str, Any]:
    generated_pct = max(0.0, 100.0 - float(real_pct) - float(unknown_pct))
    return {
        "examples": int(examples),
        "families": {"smoke": int(examples)} if int(examples) else {},
        "source_mix": {
            "examples": int(examples),
            "by_family": {"smoke": int(examples)} if int(examples) else {},
            "by_source_kind": {"smoke": int(examples)} if int(examples) else {},
            "by_source_group": {"real": int(examples)} if float(real_pct) > 0 and int(examples) else {},
            "generated_examples": int(round(int(examples) * generated_pct / 100.0)),
            "real_examples": int(round(int(examples) * float(real_pct) / 100.0)),
            "unknown_examples": int(round(int(examples) * float(unknown_pct) / 100.0)),
            "generated_pct": round(generated_pct, 2),
            "real_pct": round(float(real_pct), 2),
            "unknown_pct": round(float(unknown_pct), 2),
        },
    }


def create_smoke_checkpoint(
    output_model: Path,
    *,
    status: str = "smoke_test",
    feature_dim: int = 82,
    train_examples: int = 4,
    val_examples: int = 4,
    avg_mae: float = 0.5,
    baseline_avg_mae: float = 1.0,
    real_pct: float = 50.0,
    unknown_pct: float = 0.0,
    manifest_output: Path | None = None,
    seed: int = 7,
) -> dict[str, Any]:
    torch.manual_seed(int(seed))
    model = BoxBoxWarpNet(feature_dim=int(feature_dim))
    output_model.parent.mkdir(parents=True, exist_ok=True)

    train_summary = _dataset_summary(examples=train_examples, real_pct=real_pct, unknown_pct=unknown_pct)
    val_summary = _dataset_summary(examples=val_examples, real_pct=real_pct, unknown_pct=unknown_pct)
    manifest = build_training_manifest(
        examples_dir="smoke",
        output_model=output_model,
        train_summary=train_summary,
        val_summary=val_summary,
        config={
            "smoke_checkpoint": True,
            "feature_dim": int(feature_dim),
            "seed": int(seed),
            "status": status,
        },
        epoch_losses=[],
        val_metrics={
            "examples": float(val_examples),
            "avg_mae": float(avg_mae),
            "baseline_avg_mae": float(baseline_avg_mae),
            "avg_mse": float(avg_mae) ** 2,
            "avg_monotonic_violation": 0.0,
        },
        status=status,
    )
    torch.save(
        {
            "model": model.state_dict(),
            "feature_dim": int(feature_dim),
            "architecture": "baseline_residual_v1",
            "training_manifest": manifest,
        },
        output_model,
    )
    write_training_manifest(manifest, manifest_output or default_manifest_path(output_model))
    print(f"Saved smoke checkpoint: {output_model}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("models/boxbox_smoke.pt"))
    parser.add_argument("--status", type=str, default="smoke_test")
    parser.add_argument("--feature-dim", type=int, default=82)
    parser.add_argument("--train-examples", type=int, default=4)
    parser.add_argument("--val-examples", type=int, default=4)
    parser.add_argument("--avg-mae", type=float, default=0.5)
    parser.add_argument("--baseline-avg-mae", type=float, default=1.0)
    parser.add_argument("--real-pct", type=float, default=50.0)
    parser.add_argument("--unknown-pct", type=float, default=0.0)
    parser.add_argument("--manifest-output", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    create_smoke_checkpoint(
        args.output,
        status=args.status,
        feature_dim=args.feature_dim,
        train_examples=args.train_examples,
        val_examples=args.val_examples,
        avg_mae=args.avg_mae,
        baseline_avg_mae=args.baseline_avg_mae,
        real_pct=args.real_pct,
        unknown_pct=args.unknown_pct,
        manifest_output=args.manifest_output,
        seed=args.seed,
    )


if __name__ == "__main__":
    main()
