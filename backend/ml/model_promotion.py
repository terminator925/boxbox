from __future__ import annotations

import argparse
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.ml.model_compare import compare_models
from backend.ml.model_registry import DEFAULT_MIN_REAL_PCT, DEFAULT_MIN_VAL_EXAMPLES, registry_entries


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def promotion_plan(
    models_dir: Path,
    *,
    target_name: str = "boxbox_latest.pt",
    min_score_improvement_pct: float = 1.0,
    min_val_examples: int = DEFAULT_MIN_VAL_EXAMPLES,
    min_real_pct: float = DEFAULT_MIN_REAL_PCT,
) -> dict[str, Any]:
    target = models_dir / target_name
    entries = registry_entries(models_dir, min_val_examples=min_val_examples, min_real_pct=min_real_pct)
    scored_entries = [entry for entry in entries if not math.isinf(float(entry.get("score", math.inf)))]
    best = scored_entries[0] if scored_entries else None
    if best is None:
        return {
            "action": "none",
            "reason": "No scored training manifests found.",
            "target_model": str(target),
            "candidate": None,
            "comparison": None,
        }

    best_path = Path(str(best["model"]))
    if best_path.resolve() == target.resolve():
        return {
            "action": "none",
            "reason": "Top ranked model is already the target model.",
            "target_model": str(target),
            "candidate": best,
            "comparison": None,
            "rejected_candidates": [],
        }

    rejected_candidates: list[dict[str, Any]] = []
    selected: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    candidate: Path | None = None
    for entry in scored_entries:
        candidate_path = Path(str(entry["model"]))
        if candidate_path.resolve() == target.resolve():
            continue
        candidate_comparison = compare_models(
            models_dir,
            active_name=target_name,
            candidate_name=candidate_path.name,
            min_score_improvement_pct=min_score_improvement_pct,
            min_val_examples=min_val_examples,
            min_real_pct=min_real_pct,
        )
        if bool(candidate_comparison.get("passed", False)):
            selected = entry
            comparison = candidate_comparison
            candidate = candidate_path
            break
        rejected_candidates.append({"candidate": entry, "comparison": candidate_comparison})

    if selected is None or comparison is None or candidate is None:
        first_rejection = rejected_candidates[0] if rejected_candidates else {}
        return {
            "action": "none",
            "reason": "No ranked candidate cleared the manifest comparison gate.",
            "target_model": str(target),
            "candidate": (first_rejection.get("candidate") if first_rejection else best),
            "comparison": first_rejection.get("comparison"),
            "rejected_candidates": rejected_candidates,
        }

    backup = target.with_name(f"{target.stem}.backup_{_timestamp()}{target.suffix}")
    manifest_target = target.with_suffix(".training.json")
    candidate_manifest = Path(str(selected["manifest"]))
    manifest_backup = manifest_target.with_name(f"{manifest_target.stem}.backup_{_timestamp()}{manifest_target.suffix}")
    return {
        "action": "promote",
        "reason": "Highest ranked passing model differs from target model.",
        "target_model": str(target),
        "target_manifest": str(manifest_target),
        "backup_model": str(backup),
        "backup_manifest": str(manifest_backup),
        "candidate": selected,
        "candidate_model": str(candidate),
        "candidate_manifest": str(candidate_manifest),
        "comparison": comparison,
        "rejected_candidates": rejected_candidates,
    }


def apply_promotion(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("action") != "promote":
        return {**plan, "applied": False}

    target = Path(str(plan["target_model"]))
    candidate = Path(str(plan["candidate_model"]))
    if not candidate.exists():
        raise FileNotFoundError(f"Candidate model does not exist: {candidate}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.copy2(target, Path(str(plan["backup_model"])))
    shutil.copy2(candidate, target)

    candidate_manifest = Path(str(plan["candidate_manifest"]))
    target_manifest = Path(str(plan["target_manifest"]))
    if candidate_manifest.exists():
        if target_manifest.exists():
            shutil.copy2(target_manifest, Path(str(plan["backup_manifest"])))
        shutil.copy2(candidate_manifest, target_manifest)

    return {**plan, "applied": True}


def print_plan(plan: dict[str, Any], *, applied: bool = False) -> None:
    print(f"action={plan.get('action')}")
    print(f"reason={plan.get('reason')}")
    print(f"target_model={plan.get('target_model')}")
    candidate = plan.get("candidate") or {}
    if candidate:
        print(f"candidate_model={candidate.get('model_name')}")
        print(f"candidate_score={candidate.get('score')}")
        print(f"candidate_avg_mae={candidate.get('avg_mae')}")
        print(f"candidate_generated_pct={candidate.get('generated_pct')}")
        print(f"candidate_real_pct={candidate.get('real_pct')}")
    comparison = plan.get("comparison") or {}
    if comparison:
        print(f"comparison_verdict={comparison.get('verdict')}")
        print(f"comparison_passed={1 if comparison.get('passed') else 0}")
        print(f"comparison_score_improvement_pct={comparison.get('score_improvement_pct')}")
    rejected = plan.get("rejected_candidates") or []
    print(f"rejected_candidate_count={len(rejected)}")
    if plan.get("action") == "promote":
        print(f"backup_model={plan.get('backup_model')}")
        print(f"target_manifest={plan.get('target_manifest')}")
    print(f"applied={1 if applied else 0}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--target-name", type=str, default="boxbox_latest.pt")
    parser.add_argument("--min-score-improvement-pct", type=float, default=1.0)
    parser.add_argument("--min-val-examples", type=int, default=DEFAULT_MIN_VAL_EXAMPLES)
    parser.add_argument("--min-real-pct", type=float, default=DEFAULT_MIN_REAL_PCT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    plan = promotion_plan(
        args.models_dir,
        target_name=args.target_name,
        min_score_improvement_pct=args.min_score_improvement_pct,
        min_val_examples=args.min_val_examples,
        min_real_pct=args.min_real_pct,
    )
    result = apply_promotion(plan) if args.apply else {**plan, "applied": False}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print_plan(result, applied=bool(result.get("applied", False)))


if __name__ == "__main__":
    main()
