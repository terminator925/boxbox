from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import torch
from torch.utils.data import DataLoader, WeightedRandomSampler

from backend.ml.dataset import WarpDataset, collate_batch, dataset_sample_weights, warm_dataset_cache
from backend.ml.dataset_inventory import inventory_dataset_items
from backend.ml.device import autocast_context, resolve_device, uses_cuda
from backend.ml.evaluate import evaluate
from backend.ml.model import BoxBoxWarpNet


def dataset_summary(dataset: WarpDataset) -> dict[str, object]:
    families = dict(sorted(Counter(dataset.dataset_families).items()))
    source_mix = inventory_dataset_items(dataset.items)
    return {"examples": len(dataset), "families": families, "source_mix": source_mix}


def print_dataset_summary(label: str, dataset: WarpDataset) -> None:
    summary = dataset_summary(dataset)
    source_mix = dict(summary["source_mix"])
    print(f"{label}_examples={summary['examples']}")
    print(f"{label}_families={json.dumps(summary['families'], sort_keys=True)}")
    print(f"{label}_source_group={json.dumps(source_mix['by_source_group'], sort_keys=True)}")
    print(f"{label}_source_kind={json.dumps(source_mix['by_source_kind'], sort_keys=True)}")
    print(f"{label}_generated_pct={source_mix['generated_pct']}")
    print(f"{label}_real_pct={source_mix['real_pct']}")
    print(f"{label}_unknown_pct={source_mix['unknown_pct']}")


def default_manifest_path(output_model: Path) -> Path:
    return output_model.with_suffix(".training.json")


def build_training_manifest(
    *,
    examples_dir: Path | str,
    output_model: Path,
    train_summary: dict[str, Any],
    val_summary: dict[str, Any],
    config: dict[str, Any],
    epoch_losses: list[float] | None = None,
    val_metrics: dict[str, float] | None = None,
    status: str = "trained",
    created_at: str | None = None,
) -> dict[str, Any]:
    return {
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "status": status,
        "examples": str(examples_dir),
        "output_model": str(output_model),
        "config": config,
        "train_dataset": train_summary,
        "val_dataset": val_summary,
        "epoch_losses": list(epoch_losses or []),
        "val_metrics": dict(val_metrics or {}),
    }


def write_training_manifest(manifest: dict[str, Any], manifest_output: Path) -> None:
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved training manifest: {manifest_output}")


def train(
    examples_dir: Path | str,
    output_model: Path,
    epochs: int = 12,
    batch_size: int = 2,
    lr: float = 1e-3,
    device: str = "auto",
    val_ratio: float = 0.15,
    init_model: Path | None = None,
    cache_dir: Path | None = None,
    warm_cache: bool = False,
    num_workers: int | None = None,
    dataset_filters: str | None = None,
    dataset_balance: str = "uniform",
    dataset_weight_overrides: str | None = None,
    min_improvement_pct: float | None = None,
    max_after_sec: float | None = None,
    min_event_count: int | None = None,
    max_examples: int | None = None,
    dry_run: bool = False,
    manifest_output: Path | None = None,
) -> None:
    device = resolve_device(device)
    if uses_cuda(device):
        torch.set_float32_matmul_precision("high")

    train_dataset = WarpDataset(
        examples_dir,
        split="train",
        val_ratio=val_ratio,
        cache_dir=cache_dir,
        dataset_filters=dataset_filters,
        min_improvement_pct=min_improvement_pct,
        max_after_sec=max_after_sec,
        min_event_count=min_event_count,
        max_examples=max_examples,
    )
    val_dataset = WarpDataset(
        examples_dir,
        split="val",
        val_ratio=val_ratio,
        cache_dir=cache_dir,
        dataset_filters=dataset_filters,
        min_improvement_pct=min_improvement_pct,
        max_after_sec=max_after_sec,
        min_event_count=min_event_count,
        max_examples=max_examples,
    )
    train_summary = dataset_summary(train_dataset)
    val_summary = dataset_summary(val_dataset)
    print_dataset_summary("train", train_dataset)
    print_dataset_summary("val", val_dataset)
    print(f"train_device={device}")
    print(f"dataset_filters={dataset_filters or ''}")
    print(f"dataset_balance={dataset_balance}")
    print(f"dataset_weight_overrides={dataset_weight_overrides or ''}")
    print(f"min_improvement_pct={'' if min_improvement_pct is None else min_improvement_pct}")
    print(f"max_after_sec={'' if max_after_sec is None else max_after_sec}")
    print(f"min_event_count={'' if min_event_count is None else min_event_count}")
    print(f"max_examples={'' if max_examples is None else max_examples}")
    print(f"dry_run={1 if dry_run else 0}")
    config = {
        "epochs": int(epochs),
        "batch_size": int(batch_size),
        "lr": float(lr),
        "device": str(device),
        "val_ratio": float(val_ratio),
        "init_model": "" if init_model is None else str(init_model),
        "cache_dir": "" if cache_dir is None else str(cache_dir),
        "warm_cache": bool(warm_cache),
        "num_workers": num_workers,
        "dataset_filters": dataset_filters or "",
        "dataset_balance": dataset_balance,
        "dataset_weight_overrides": dataset_weight_overrides or "",
        "min_improvement_pct": min_improvement_pct,
        "max_after_sec": max_after_sec,
        "min_event_count": min_event_count,
        "max_examples": max_examples,
    }
    if dry_run:
        if manifest_output is not None:
            manifest = build_training_manifest(
                examples_dir=examples_dir,
                output_model=output_model,
                train_summary=train_summary,
                val_summary=val_summary,
                config=config,
                status="dry_run",
            )
            write_training_manifest(manifest, manifest_output)
        return

    if len(train_dataset) == 0:
        raise RuntimeError(f"No training pairs found in {examples_dir}")

    if warm_cache:
        print("warming_train_cache=1")
        warm_dataset_cache(train_dataset)
        print("warming_val_cache=1")
        warm_dataset_cache(val_dataset)

    if num_workers is None:
        num_workers = 0 if os.name == "nt" else min(4, max(0, (os.cpu_count() or 2) // 2))
    sampler = None
    shuffle = True
    if dataset_balance != 'uniform':
        sample_weights = dataset_sample_weights(
            train_dataset,
            mode=dataset_balance,
            weight_overrides=dataset_weight_overrides,
        )
        if len(sample_weights):
            sampler = WeightedRandomSampler(sample_weights.tolist(), num_samples=len(sample_weights), replacement=True)
            shuffle = False
    loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        sampler=sampler,
        collate_fn=collate_batch,
        num_workers=num_workers,
        pin_memory=uses_cuda(device),
        persistent_workers=num_workers > 0,
    )

    sample = train_dataset[0]
    feat_dim = sample["features"].shape[1]
    model = BoxBoxWarpNet(feature_dim=feat_dim).to(device)
    if init_model is not None and init_model.exists():
        ckpt = torch.load(init_model, map_location=device, weights_only=False)
        model.load_state_dict(ckpt["model"], strict=False)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(epochs, 1), eta_min=lr * 0.1)
    scaler = torch.amp.GradScaler("cuda", enabled=uses_cuda(device))
    epoch_losses: list[float] = []

    for epoch in range(1, epochs + 1):
        model.train()
        running = 0.0
        for batch in loader:
            x = batch["features"].to(device, non_blocking=uses_cuda(device))
            y = batch["target"].to(device, non_blocking=uses_cuda(device))
            mask = batch["mask"].to(device, non_blocking=uses_cuda(device))

            with autocast_context(device):
                baseline = x[:, :, -1]
                pred = model(x, mask=mask)
                mse = ((pred - y) ** 2 * mask).sum() / mask.sum().clamp_min(1.0)
                mae = ((pred - y).abs() * mask).sum() / mask.sum().clamp_min(1.0)
                slope_pred = pred[:, 1:] - pred[:, :-1]
                slope_target = y[:, 1:] - y[:, :-1]
                slope_loss = ((slope_pred - slope_target).abs() * mask[:, 1:]).sum() / mask[:, 1:].sum().clamp_min(1.0)
                residual_penalty = (((pred - baseline) ** 2) * mask).sum() / mask.sum().clamp_min(1.0)
                loss = 0.45 * mse + 0.30 * mae + 0.20 * slope_loss + 0.05 * residual_penalty

            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            scaler.step(optimizer)
            scaler.update()
            running += float(loss.item())

        epoch_loss = running / max(len(loader), 1)
        epoch_losses.append(float(epoch_loss))
        print(f"epoch={epoch} loss={epoch_loss:.6f}")
        scheduler.step()

    output_model.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_metadata = build_training_manifest(
        examples_dir=examples_dir,
        output_model=output_model,
        train_summary=train_summary,
        val_summary=val_summary,
        config=config,
        epoch_losses=epoch_losses,
        status="trained",
    )
    torch.save(
        {
            "model": model.state_dict(),
            "feature_dim": feat_dim,
            "architecture": "baseline_residual_v1",
            "training_manifest": checkpoint_metadata,
        },
        output_model,
    )
    print(f"Saved model: {output_model}")
    val_metrics: dict[str, float] = {}
    if len(val_dataset) > 0:
        if dataset_filters:
            val_metrics = evaluate(
                examples_dir,
                output_model,
                device=device,
                split="val",
                val_ratio=val_ratio,
                dataset_filters=dataset_filters,
                min_improvement_pct=min_improvement_pct,
                max_after_sec=max_after_sec,
                min_event_count=min_event_count,
                max_examples=max_examples,
            )
        else:
            val_metrics = evaluate(
                examples_dir,
                output_model,
                device=device,
                split="val",
                val_ratio=val_ratio,
                min_improvement_pct=min_improvement_pct,
                max_after_sec=max_after_sec,
                min_event_count=min_event_count,
                max_examples=max_examples,
            )
        for key, value in val_metrics.items():
            print(f"val_{key}={value:.6f}")
    manifest = build_training_manifest(
        examples_dir=examples_dir,
        output_model=output_model,
        train_summary=train_summary,
        val_summary=val_summary,
        config=config,
        epoch_losses=epoch_losses,
        val_metrics=val_metrics,
        status="trained",
    )
    write_training_manifest(manifest, manifest_output or default_manifest_path(output_model))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=str, default="data/examples")
    parser.add_argument("--output", type=Path, default=Path("models/boxbox_latest.pt"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--device", type=str, default="auto")
    parser.add_argument("--val-ratio", type=float, default=0.15)
    parser.add_argument("--init-model", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache/warp_targets"))
    parser.add_argument("--warm-cache", action="store_true")
    parser.add_argument("--num-workers", type=int, default=None)
    parser.add_argument("--datasets", type=str, default="")
    parser.add_argument("--dataset-balance", type=str, default="uniform", choices=["uniform", "inverse"])
    parser.add_argument("--dataset-weight-overrides", type=str, default="")
    parser.add_argument("--min-improvement-pct", type=float, default=None)
    parser.add_argument("--max-after-sec", type=float, default=None)
    parser.add_argument("--min-event-count", type=int, default=None)
    parser.add_argument("--max-examples", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest-output", type=Path, default=None)
    args = parser.parse_args()

    train(
        args.examples,
        args.output,
        args.epochs,
        args.batch_size,
        args.lr,
        args.device,
        args.val_ratio,
        args.init_model,
        args.cache_dir,
        args.warm_cache,
        args.num_workers,
        args.datasets or None,
        args.dataset_balance,
        args.dataset_weight_overrides or None,
        args.min_improvement_pct,
        args.max_after_sec,
        args.min_event_count,
        args.max_examples,
        args.dry_run,
        args.manifest_output,
    )


if __name__ == "__main__":
    main()



