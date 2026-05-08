from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from backend.ml.model_registry import DEFAULT_MIN_REAL_PCT, DEFAULT_MIN_VAL_EXAMPLES, model_warnings, registry_entries

BLOCKING_CANDIDATE_WARNINGS = {
    "status_not_trained",
    "low_val_examples",
    "empty_train_dataset",
    "no_real_training_data",
    "low_real_training_pct",
    "unknown_source_mix",
}


def _entry_for_model(entries: list[dict[str, Any]], model_name: str) -> dict[str, Any] | None:
    wanted = Path(model_name).name
    for entry in entries:
        if str(entry.get("model_name")) == wanted or Path(str(entry.get("model"))).name == wanted:
            return entry
    return None


def _pct_improvement(baseline: float, candidate: float) -> float | None:
    if math.isinf(baseline) or math.isinf(candidate) or baseline <= 0:
        return None
    return ((baseline - candidate) / baseline) * 100.0


def compare_model_entries(
    active: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    *,
    min_score_improvement_pct: float = 1.0,
    min_val_examples: int = DEFAULT_MIN_VAL_EXAMPLES,
    min_real_pct: float = DEFAULT_MIN_REAL_PCT,
) -> dict[str, Any]:
    if candidate is None:
        return {
            "verdict": "no_candidate",
            "passed": False,
            "reason": "No manifest-backed candidate model was found.",
            "active": active,
            "candidate": None,
        }
    candidate_warnings = model_warnings(candidate, min_val_examples=min_val_examples, min_real_pct=min_real_pct)
    if "low_val_examples" in candidate_warnings:
        return {
            "verdict": "candidate_low_validation_evidence",
            "passed": False,
            "reason": (
                f"Candidate has {int(candidate.get('val_examples') or 0)} validation examples; "
                f"requires at least {int(min_val_examples)}."
            ),
            "min_val_examples": int(min_val_examples),
            "min_real_pct": float(min_real_pct),
            "active": active,
            "candidate": {**candidate, "warnings": candidate_warnings},
        }
    blocking_warnings = sorted(set(candidate_warnings).intersection(BLOCKING_CANDIDATE_WARNINGS))
    if blocking_warnings:
        return {
            "verdict": "candidate_training_data_quality_risk",
            "passed": False,
            "reason": f"Candidate has blocking training-data warnings: {','.join(blocking_warnings)}.",
            "min_val_examples": int(min_val_examples),
            "min_real_pct": float(min_real_pct),
            "active": active,
            "candidate": {**candidate, "warnings": candidate_warnings},
        }
    if active is None:
        return {
            "verdict": "candidate_scored_no_active_manifest",
            "passed": not math.isinf(float(candidate.get("score", math.inf))),
            "reason": "Candidate has a scored manifest, but the active model has no comparable manifest.",
            "min_val_examples": int(min_val_examples),
            "min_real_pct": float(min_real_pct),
            "active": None,
            "candidate": {**candidate, "warnings": candidate_warnings},
        }

    active_score = float(active.get("score", math.inf))
    candidate_score = float(candidate.get("score", math.inf))
    active_warnings = model_warnings(active, min_val_examples=min_val_examples, min_real_pct=min_real_pct)
    if math.isinf(active_score) and not math.isinf(candidate_score):
        return {
            "verdict": "candidate_scored_no_active_manifest",
            "passed": True,
            "reason": "Candidate has a scored manifest, but the active model has no comparable manifest.",
            "min_val_examples": int(min_val_examples),
            "min_real_pct": float(min_real_pct),
            "active": {**active, "warnings": active_warnings},
            "candidate": {**candidate, "warnings": candidate_warnings},
        }
    score_improvement_pct = _pct_improvement(active_score, candidate_score)
    active_mae = active.get("avg_mae")
    candidate_mae = candidate.get("avg_mae")
    mae_improvement_pct = (
        _pct_improvement(float(active_mae), float(candidate_mae))
        if active_mae is not None and candidate_mae is not None
        else None
    )
    passed = score_improvement_pct is not None and score_improvement_pct >= float(min_score_improvement_pct)
    return {
        "verdict": "candidate_better" if passed else "candidate_not_better",
        "passed": bool(passed),
        "reason": (
            f"Candidate score improved by {score_improvement_pct:.2f}%."
            if score_improvement_pct is not None
            else "Candidate and active scores are not comparable."
        ),
        "min_score_improvement_pct": float(min_score_improvement_pct),
        "min_val_examples": int(min_val_examples),
        "min_real_pct": float(min_real_pct),
        "active": {**active, "warnings": active_warnings},
        "candidate": {**candidate, "warnings": candidate_warnings},
        "score_delta": None if math.isinf(active_score) or math.isinf(candidate_score) else candidate_score - active_score,
        "score_improvement_pct": score_improvement_pct,
        "avg_mae_delta": None
        if active_mae is None or candidate_mae is None
        else float(candidate_mae) - float(active_mae),
        "avg_mae_improvement_pct": mae_improvement_pct,
    }


def compare_models(
    models_dir: Path,
    *,
    active_name: str = "boxbox_latest.pt",
    candidate_name: str | None = None,
    min_score_improvement_pct: float = 1.0,
    min_val_examples: int = DEFAULT_MIN_VAL_EXAMPLES,
    min_real_pct: float = DEFAULT_MIN_REAL_PCT,
) -> dict[str, Any]:
    entries = registry_entries(models_dir, min_val_examples=min_val_examples, min_real_pct=min_real_pct)
    active = _entry_for_model(entries, active_name)
    if candidate_name:
        candidate = _entry_for_model(entries, candidate_name)
    else:
        candidate = next((entry for entry in entries if str(entry.get("model_name")) != Path(active_name).name), None)
    return compare_model_entries(
        active,
        candidate,
        min_score_improvement_pct=min_score_improvement_pct,
        min_val_examples=min_val_examples,
        min_real_pct=min_real_pct,
    )


def print_comparison(result: dict[str, Any]) -> None:
    print(f"verdict={result.get('verdict')}")
    print(f"passed={1 if result.get('passed') else 0}")
    print(f"reason={result.get('reason')}")
    active = result.get("active") or {}
    candidate = result.get("candidate") or {}
    if active:
        print(f"active_model={active.get('model_name')}")
        print(f"active_score={active.get('score')}")
        print(f"active_avg_mae={active.get('avg_mae')}")
        print(f"active_warnings={','.join(active.get('warnings') or []) or 'none'}")
    if candidate:
        print(f"candidate_model={candidate.get('model_name')}")
        print(f"candidate_score={candidate.get('score')}")
        print(f"candidate_avg_mae={candidate.get('avg_mae')}")
        print(f"candidate_warnings={','.join(candidate.get('warnings') or []) or 'none'}")
    if "score_improvement_pct" in result:
        print(f"score_improvement_pct={result.get('score_improvement_pct')}")
    if "avg_mae_improvement_pct" in result:
        print(f"avg_mae_improvement_pct={result.get('avg_mae_improvement_pct')}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--active-name", type=str, default="boxbox_latest.pt")
    parser.add_argument("--candidate-name", type=str, default="")
    parser.add_argument("--min-score-improvement-pct", type=float, default=1.0)
    parser.add_argument("--min-val-examples", type=int, default=DEFAULT_MIN_VAL_EXAMPLES)
    parser.add_argument("--min-real-pct", type=float, default=DEFAULT_MIN_REAL_PCT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = compare_models(
        args.models_dir,
        active_name=args.active_name,
        candidate_name=args.candidate_name or None,
        min_score_improvement_pct=args.min_score_improvement_pct,
        min_val_examples=args.min_val_examples,
        min_real_pct=args.min_real_pct,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print_comparison(result)


if __name__ == "__main__":
    main()
