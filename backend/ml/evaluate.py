from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from backend.ml.dataset import WarpDataset, collate_batch
from backend.ml.device import autocast_context, resolve_device
from backend.ml.model import BoxBoxWarpNet


def evaluate(
    examples_dir: Path | str,
    model_file: Path,
    device: str = "auto",
    split: str = "all",
    val_ratio: float = 0.15,
    dataset_filters: str | None = None,
    min_improvement_pct: float | None = None,
    max_after_sec: float | None = None,
    min_event_count: int | None = None,
    max_examples: int | None = None,
) -> dict[str, float]:
    device = resolve_device(device)
    dataset = WarpDataset(
        examples_dir,
        split=split,
        val_ratio=val_ratio,
        dataset_filters=dataset_filters,
        min_improvement_pct=min_improvement_pct,
        max_after_sec=max_after_sec,
        min_event_count=min_event_count,
        max_examples=max_examples,
    )
    if len(dataset) == 0:
        raise RuntimeError(f"No evaluation pairs found in {examples_dir}")

    ckpt = torch.load(model_file, map_location=device, weights_only=False)
    feature_dim = int(ckpt.get("feature_dim") or dataset[0]["features"].shape[1])
    model = BoxBoxWarpNet(feature_dim=feature_dim)
    model.load_state_dict(ckpt["model"])
    model.to(device)
    model.eval()

    mse_values: list[float] = []
    mae_values: list[float] = []
    baseline_mae_values: list[float] = []
    monotonic_violations: list[float] = []

    with torch.inference_mode():
        for idx in range(len(dataset)):
            batch = collate_batch([dataset[idx]])
            x = batch["features"].to(device)
            y = batch["target"].to(device)
            mask = batch["mask"].to(device)

            with autocast_context(device):
                pred = model(x, mask=mask)
            diff = (pred - y) * mask
            mse_values.append(float((diff.pow(2).sum() / mask.sum().clamp_min(1.0)).item()))
            mae_values.append(float((diff.abs().sum() / mask.sum().clamp_min(1.0)).item()))
            baseline = x[:, :, -1]
            baseline_diff = (baseline - y) * mask
            pred_np = pred.squeeze(0).cpu().numpy()
            monotonic_violations.append(float(np.maximum(0.0, -(np.diff(pred_np))).sum()))
            baseline_mae_values.append(float((baseline_diff.abs().sum() / mask.sum().clamp_min(1.0)).item()))

    metrics = {
        "examples": float(len(dataset)),
        "avg_mse": float(np.mean(mse_values)),
        "avg_mae": float(np.mean(mae_values)),
        "baseline_avg_mae": float(np.mean(baseline_mae_values)),
        "avg_monotonic_violation": float(np.mean(monotonic_violations)),
    }
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=Path, default=Path("data/examples"))
    parser.add_argument("--model", type=Path, default=Path("models/boxbox_latest.pt"))
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--split", type=str, default="all")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--datasets", type=str, default="")
    parser.add_argument("--min-improvement-pct", type=float, default=None)
    parser.add_argument("--max-after-sec", type=float, default=None)
    parser.add_argument("--min-event-count", type=int, default=None)
    parser.add_argument("--max-examples", type=int, default=None)
    args = parser.parse_args()

    metrics = evaluate(
        args.examples,
        args.model,
        args.device,
        args.split,
        args.val_ratio,
        args.datasets or None,
        args.min_improvement_pct,
        args.max_after_sec,
        args.min_event_count,
        args.max_examples,
    )
    for key, value in metrics.items():
        print(f"{key}={value:.6f}")


if __name__ == "__main__":
    main()
