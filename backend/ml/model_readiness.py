from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.ml.dataset_inventory import inventory_examples
from backend.ml.model_compare import compare_models
from backend.ml.model_promotion import promotion_plan
from backend.ml.model_registry import (
    DEFAULT_MIN_REAL_PCT,
    DEFAULT_MIN_VAL_EXAMPLES,
    registry_entries,
    validate_manifests,
)


def build_readiness_report(
    *,
    examples: str | Path = "data/examples",
    models_dir: str | Path = "models",
    datasets: str | None = None,
    target_name: str = "boxbox_latest.pt",
    limit: int = 5,
    min_score_improvement_pct: float = 1.0,
    min_val_examples: int = DEFAULT_MIN_VAL_EXAMPLES,
    min_real_pct: float = DEFAULT_MIN_REAL_PCT,
    max_examples: int | None = None,
) -> dict[str, Any]:
    inventory = inventory_examples(examples, dataset_filters=datasets or None, max_examples=max_examples)
    inventory_payload = dict(inventory)
    inventory_payload.pop("records", None)

    model_root = Path(models_dir)
    validations = validate_manifests(model_root)
    entries = registry_entries(model_root, min_val_examples=min_val_examples, min_real_pct=min_real_pct)
    comparison = compare_models(
        model_root,
        active_name=target_name,
        min_score_improvement_pct=min_score_improvement_pct,
        min_val_examples=min_val_examples,
        min_real_pct=min_real_pct,
    )
    promotion = promotion_plan(
        model_root,
        target_name=target_name,
        min_score_improvement_pct=min_score_improvement_pct,
        min_val_examples=min_val_examples,
        min_real_pct=min_real_pct,
    )

    return {
        "dataset": inventory_payload,
        "manifest_validation": validations,
        "leaderboard": entries[: int(limit)],
        "comparison": comparison,
        "promotion": promotion,
        "thresholds": {
            "min_score_improvement_pct": float(min_score_improvement_pct),
            "min_val_examples": int(min_val_examples),
            "min_real_pct": float(min_real_pct),
        },
    }


def print_readiness_report(report: dict[str, Any]) -> None:
    dataset = report.get("dataset") or {}
    validations = list(report.get("manifest_validation") or [])
    leaderboard = list(report.get("leaderboard") or [])
    comparison = report.get("comparison") or {}
    promotion = report.get("promotion") or {}
    top = leaderboard[0] if leaderboard else {}

    print(f"dataset_examples={dataset.get('examples', 0)}")
    print(f"dataset_generated_pct={dataset.get('generated_pct', 0)}")
    print(f"dataset_real_pct={dataset.get('real_pct', 0)}")
    print(f"dataset_unknown_pct={dataset.get('unknown_pct', 0)}")
    print(f"manifest_count={len(validations)}")
    print(f"valid_manifest_count={sum(1 for item in validations if item.get('valid'))}")
    print(f"leaderboard_count={len(leaderboard)}")
    print(f"leaderboard_top={top.get('model_name', '')}")
    print(f"leaderboard_top_warnings={','.join(top.get('warnings') or []) if top else 'none'}")
    print(f"comparison_verdict={comparison.get('verdict')}")
    print(f"comparison_passed={1 if comparison.get('passed') else 0}")
    print(f"promotion_action={promotion.get('action')}")
    print(f"promotion_reason={promotion.get('reason')}")
    print(f"promotion_candidate={(promotion.get('candidate') or {}).get('model_name', '')}")
    print(f"rejected_candidate_count={len(promotion.get('rejected_candidates') or [])}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=str, default="data/examples")
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--datasets", type=str, default="")
    parser.add_argument("--target-name", type=str, default="boxbox_latest.pt")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--min-score-improvement-pct", type=float, default=1.0)
    parser.add_argument("--min-val-examples", type=int, default=DEFAULT_MIN_VAL_EXAMPLES)
    parser.add_argument("--min-real-pct", type=float, default=DEFAULT_MIN_REAL_PCT)
    parser.add_argument("--max-examples", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_readiness_report(
        examples=args.examples,
        models_dir=args.models_dir,
        datasets=args.datasets or None,
        target_name=args.target_name,
        limit=args.limit,
        min_score_improvement_pct=args.min_score_improvement_pct,
        min_val_examples=args.min_val_examples,
        min_real_pct=args.min_real_pct,
        max_examples=args.max_examples if args.max_examples > 0 else None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print_readiness_report(report)


if __name__ == "__main__":
    main()
