from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

DEFAULT_MIN_VAL_EXAMPLES = 4
DEFAULT_MIN_REAL_PCT = 1.0


def _as_float(value: Any, default: float = math.inf) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    if math.isnan(parsed):
        return default
    return parsed


def manifest_model_path(manifest_path: Path) -> Path:
    if manifest_path.name.endswith(".training.json"):
        return manifest_path.with_name(manifest_path.name[: -len(".training.json")] + ".pt")
    return manifest_path.with_suffix(".pt")


def load_training_manifest(manifest_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def model_score(manifest: dict[str, Any]) -> float:
    metrics = dict(manifest.get("val_metrics") or {})
    avg_mae = _as_float(metrics.get("avg_mae"))
    baseline_mae = _as_float(metrics.get("baseline_avg_mae"))
    if math.isinf(avg_mae):
        return math.inf

    train_source = dict((manifest.get("train_dataset") or {}).get("source_mix") or {})
    real_pct = _as_float(train_source.get("real_pct"), default=0.0)
    unknown_pct = _as_float(train_source.get("unknown_pct"), default=100.0)
    balance_penalty = abs(real_pct - 50.0) / 5000.0
    unknown_penalty = unknown_pct / 1000.0
    improvement_bonus = 0.0
    if not math.isinf(baseline_mae) and baseline_mae > 0:
        improvement_bonus = max(0.0, baseline_mae - avg_mae) / max(baseline_mae, 1e-9) * 0.01
    return max(0.0, avg_mae + balance_penalty + unknown_penalty - improvement_bonus)


def model_warnings(
    entry: dict[str, Any],
    *,
    min_val_examples: int = DEFAULT_MIN_VAL_EXAMPLES,
    min_real_pct: float = DEFAULT_MIN_REAL_PCT,
) -> list[str]:
    warnings: list[str] = []
    status = str(entry.get("status") or "")
    val_examples = int(entry.get("val_examples") or 0)
    train_examples = int(entry.get("train_examples") or 0)
    generated_pct = _as_float(entry.get("generated_pct"), default=0.0)
    real_pct = _as_float(entry.get("real_pct"), default=0.0)
    unknown_pct = _as_float(entry.get("unknown_pct"), default=0.0)
    if "status" in entry and status != "trained":
        warnings.append("status_not_trained")
    if val_examples < int(min_val_examples):
        warnings.append("low_val_examples")
    if train_examples <= 0:
        warnings.append("empty_train_dataset")
    if train_examples > 0 and real_pct <= 0 and generated_pct > 0:
        warnings.append("no_real_training_data")
    elif real_pct < float(min_real_pct):
        warnings.append("low_real_training_pct")
    if unknown_pct > 0:
        warnings.append("unknown_source_mix")
    return warnings


def validate_manifest(manifest_path: Path, *, require_model: bool = True) -> dict[str, Any]:
    issues: list[str] = []
    manifest = load_training_manifest(manifest_path)
    model_path = manifest_model_path(manifest_path)
    if manifest is None:
        issues.append("invalid_json_or_not_object")
        return {
            "manifest": str(manifest_path),
            "model": str(model_path),
            "valid": False,
            "issues": issues,
        }

    if require_model and not model_path.exists():
        issues.append("missing_model")
    if str(manifest.get("status") or "") != "trained":
        issues.append("status_not_trained")
    train_dataset = manifest.get("train_dataset")
    if not isinstance(train_dataset, dict):
        issues.append("missing_train_dataset")
    elif int(train_dataset.get("examples") or 0) <= 0:
        issues.append("empty_train_dataset")
    val_dataset = manifest.get("val_dataset")
    if not isinstance(val_dataset, dict):
        issues.append("missing_val_dataset")
    elif int(val_dataset.get("examples") or 0) <= 0:
        issues.append("empty_val_dataset")
    val_metrics = manifest.get("val_metrics")
    if not isinstance(val_metrics, dict) or _as_float(val_metrics.get("avg_mae")) == math.inf:
        issues.append("missing_val_avg_mae")
    source_mix = dict((train_dataset or {}).get("source_mix") or {}) if isinstance(train_dataset, dict) else {}
    if not source_mix:
        issues.append("missing_train_source_mix")
    elif _as_float(source_mix.get("unknown_pct"), default=100.0) >= 100.0:
        issues.append("all_source_mix_unknown")

    return {
        "manifest": str(manifest_path),
        "model": str(model_path),
        "valid": not issues,
        "issues": issues,
        "score": model_score(manifest),
    }


def validate_manifests(models_dir: Path, *, require_model: bool = True) -> list[dict[str, Any]]:
    return [
        validate_manifest(manifest_path, require_model=require_model)
        for manifest_path in sorted(models_dir.glob("*.training.json"))
    ]


def manifest_path_for_model(model_path: Path) -> Path:
    return model_path.with_suffix(".training.json")


def _load_embedded_training_manifest(model_path: Path) -> dict[str, Any] | None:
    try:
        import torch

        checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    except Exception:
        return None
    if not isinstance(checkpoint, dict):
        return None
    manifest = checkpoint.get("training_manifest")
    return manifest if isinstance(manifest, dict) else None


def sync_embedded_manifests(models_dir: Path, *, apply: bool = False) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for model_path in sorted(models_dir.glob("*.pt")):
        manifest_path = manifest_path_for_model(model_path)
        if manifest_path.exists():
            results.append(
                {
                    "model": str(model_path),
                    "manifest": str(manifest_path),
                    "action": "exists",
                    "applied": False,
                    "reason": "Sidecar manifest already exists.",
                }
            )
            continue
        embedded = _load_embedded_training_manifest(model_path)
        if embedded is None:
            results.append(
                {
                    "model": str(model_path),
                    "manifest": str(manifest_path),
                    "action": "skip",
                    "applied": False,
                    "reason": "No embedded training_manifest found.",
                }
            )
            continue
        if apply:
            manifest_path.write_text(json.dumps(embedded, indent=2, sort_keys=True), encoding="utf-8")
        results.append(
            {
                "model": str(model_path),
                "manifest": str(manifest_path),
                "action": "write",
                "applied": bool(apply),
                "reason": "Embedded training_manifest can be synced.",
            }
        )
    return results


def registry_entries(
    models_dir: Path,
    *,
    include_missing_models: bool = False,
    min_val_examples: int = DEFAULT_MIN_VAL_EXAMPLES,
    min_real_pct: float = DEFAULT_MIN_REAL_PCT,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for manifest_path in sorted(models_dir.glob("*.training.json")):
        manifest = load_training_manifest(manifest_path)
        if manifest is None:
            continue
        model_path = manifest_model_path(manifest_path)
        if not model_path.exists() and not include_missing_models:
            continue
        val_metrics = dict(manifest.get("val_metrics") or {})
        train_source = dict((manifest.get("train_dataset") or {}).get("source_mix") or {})
        entry = {
            "model": str(model_path),
            "model_name": model_path.name,
            "manifest": str(manifest_path),
            "status": str(manifest.get("status") or ""),
            "created_at": str(manifest.get("created_at") or ""),
            "score": model_score(manifest),
            "avg_mae": None if "avg_mae" not in val_metrics else _as_float(val_metrics.get("avg_mae"), default=0.0),
            "baseline_avg_mae": None
            if "baseline_avg_mae" not in val_metrics
            else _as_float(val_metrics.get("baseline_avg_mae"), default=0.0),
            "train_examples": int((manifest.get("train_dataset") or {}).get("examples") or 0),
            "val_examples": int((manifest.get("val_dataset") or {}).get("examples") or 0),
            "generated_pct": _as_float(train_source.get("generated_pct"), default=0.0),
            "real_pct": _as_float(train_source.get("real_pct"), default=0.0),
            "unknown_pct": _as_float(train_source.get("unknown_pct"), default=0.0),
        }
        entry["warnings"] = model_warnings(entry, min_val_examples=min_val_examples, min_real_pct=min_real_pct)
        entries.append(entry)
    entries.sort(
        key=lambda entry: (
            math.isinf(float(entry["score"])),
            float(entry["score"]),
            -int(entry["val_examples"]),
            str(entry["model_name"]),
        )
    )
    return entries


def ranked_model_paths(models_dir: Path, *, limit: int = 3) -> list[Path]:
    paths: list[Path] = []
    for entry in registry_entries(models_dir):
        if math.isinf(float(entry["score"])):
            continue
        path = Path(str(entry["model"]))
        if path.exists():
            paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def print_leaderboard(entries: list[dict[str, Any]], *, limit: int = 10) -> None:
    if not entries:
        print("No training manifests found.")
        return
    for rank, entry in enumerate(entries[:limit], start=1):
        score = float(entry["score"])
        score_text = "unscored" if math.isinf(score) else f"{score:.6f}"
        avg_mae = entry["avg_mae"]
        avg_mae_text = "" if avg_mae is None else f"{float(avg_mae):.6f}"
        warnings = ",".join(str(warning) for warning in entry.get("warnings", [])) or "none"
        print(
            "rank={rank} model={model} score={score} avg_mae={avg_mae} "
            "val_examples={val_examples} train_examples={train_examples} generated_pct={generated_pct:.2f} "
            "real_pct={real_pct:.2f} warnings={warnings}".format(
                rank=rank,
                model=entry["model_name"],
                score=score_text,
                avg_mae=avg_mae_text,
                val_examples=int(entry["val_examples"]),
                train_examples=int(entry["train_examples"]),
                generated_pct=float(entry["generated_pct"]),
                real_pct=float(entry["real_pct"]),
                warnings=warnings,
            )
        )


def print_validation(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No training manifests found.")
        return
    for result in results:
        issue_text = ",".join(result["issues"]) if result["issues"] else "none"
        print(
            "valid={valid} manifest={manifest} model={model} issues={issues}".format(
                valid=1 if result["valid"] else 0,
                manifest=Path(str(result["manifest"])).name,
                model=Path(str(result["model"])).name,
                issues=issue_text,
            )
        )


def print_sync_results(results: list[dict[str, Any]]) -> None:
    if not results:
        print("No model checkpoints found.")
        return
    for result in results:
        print(
            "action={action} applied={applied} model={model} manifest={manifest} reason={reason}".format(
                action=result["action"],
                applied=1 if result["applied"] else 0,
                model=Path(str(result["model"])).name,
                manifest=Path(str(result["manifest"])).name,
                reason=result["reason"],
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-missing-models", action="store_true")
    parser.add_argument("--min-val-examples", type=int, default=DEFAULT_MIN_VAL_EXAMPLES)
    parser.add_argument("--min-real-pct", type=float, default=DEFAULT_MIN_REAL_PCT)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--allow-missing-models", action="store_true")
    parser.add_argument("--sync-embedded", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.sync_embedded:
        results = sync_embedded_manifests(args.models_dir, apply=args.apply)
        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
            return
        print_sync_results(results)
        return

    if args.validate:
        results = validate_manifests(args.models_dir, require_model=not args.allow_missing_models)
        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
            return
        print_validation(results)
        return

    entries = registry_entries(
        args.models_dir,
        include_missing_models=args.include_missing_models,
        min_val_examples=args.min_val_examples,
        min_real_pct=args.min_real_pct,
    )
    if args.json:
        print(json.dumps(entries[: args.limit], indent=2, sort_keys=True))
        return
    print_leaderboard(entries, limit=args.limit)


if __name__ == "__main__":
    main()
