from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _gate_payload(report: dict[str, Any]) -> dict[str, Any]:
    gate = report.get("metronome_canary_gate")
    if isinstance(gate, dict):
        return gate
    if "passed" in report and "observed" in report:
        return report
    raise RuntimeError("Report does not contain a metronome_canary_gate payload")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _row(path: Path) -> dict[str, Any] | None:
    report = json.loads(path.read_text(encoding="utf-8"))
    try:
        gate = _gate_payload(report)
    except RuntimeError:
        return None
    observed = dict(gate.get("observed") or {})
    runtime = dict(observed.get("runtime_config") or {})
    stat = path.stat()
    failed_checks = [str(item) for item in gate.get("failed_checks") or []]
    return {
        "path": str(path),
        "name": path.name,
        "modified_time": stat.st_mtime,
        "passed": bool(gate.get("passed", False)),
        "failed_checks": failed_checks,
        "failure_summary": str(gate.get("failure_summary", "")),
        "target_bpm": _safe_float(observed.get("target_bpm")),
        "verdict": str(observed.get("verdict", "")),
        "segment_locked_ratio": _safe_float(observed.get("segment_locked_ratio")),
        "fixed_locked_ratio": _safe_float(observed.get("fixed_locked_ratio")),
        "raw_fixed_locked_ratio": _safe_float(observed.get("raw_fixed_locked_ratio")),
        "avg_error_after_sec": _safe_float(observed.get("avg_error_after_sec"), 999.0),
        "elapsed_sec": _safe_float(observed.get("elapsed_sec")),
        "max_window_offset_jump_sec": _safe_float(observed.get("max_window_offset_jump_sec")),
        "unstable_segments": _safe_int(observed.get("unstable_segments")),
        "meltdown_segments": _safe_int(observed.get("meltdown_segments")),
        "unstable_windows": _safe_int(observed.get("unstable_windows")),
        "meltdown_windows": _safe_int(observed.get("meltdown_windows")),
        "runtime_config": runtime,
    }


def summarize_canary_history(paths: list[Path], *, top_n: int = 10) -> dict[str, Any]:
    parsed_rows = [_row(path) for path in paths]
    rows = sorted((row for row in parsed_rows if row is not None), key=lambda row: (-float(row["modified_time"]), row["name"]))
    skipped_count = sum(1 for row in parsed_rows if row is None)
    latest = rows[0] if rows else None
    previous = rows[1] if len(rows) > 1 else None
    passing = [row for row in rows if row["passed"]]
    failing = [row for row in rows if not row["passed"]]
    best_lock = sorted(
        rows,
        key=lambda row: (
            -float(row["fixed_locked_ratio"]),
            -float(row["segment_locked_ratio"]),
            float(row["avg_error_after_sec"]),
            float(row["elapsed_sec"]),
            row["name"],
        ),
    )[: int(top_n)]
    fastest = sorted([row for row in rows if float(row["elapsed_sec"]) > 0], key=lambda row: (float(row["elapsed_sec"]), row["name"]))[
        : int(top_n)
    ]
    trend = {}
    if latest and previous:
        trend = {
            "avg_error_delta_latest_minus_previous_sec": float(latest["avg_error_after_sec"]) - float(previous["avg_error_after_sec"]),
            "elapsed_delta_latest_minus_previous_sec": float(latest["elapsed_sec"]) - float(previous["elapsed_sec"]),
            "fixed_lock_delta_latest_minus_previous": float(latest["fixed_locked_ratio"]) - float(previous["fixed_locked_ratio"]),
            "segment_lock_delta_latest_minus_previous": float(latest["segment_locked_ratio"])
            - float(previous["segment_locked_ratio"]),
        }
    return {
        "file_count": len(rows),
        "input_file_count": len(paths),
        "skipped_non_gate_count": skipped_count,
        "pass_count": len(passing),
        "fail_count": len(failing),
        "latest": latest,
        "previous": previous,
        "trend": trend,
        "best_lock": best_lock,
        "fastest": fastest,
        "recent": rows[: int(top_n)],
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"file_count={summary['file_count']}")
    print(f"input_file_count={summary['input_file_count']}")
    print(f"skipped_non_gate_count={summary['skipped_non_gate_count']}")
    print(f"pass_count={summary['pass_count']}")
    print(f"fail_count={summary['fail_count']}")
    latest = summary.get("latest") or {}
    if latest:
        runtime = dict(latest.get("runtime_config") or {})
        failed_checks = ",".join(latest.get("failed_checks") or []) or "none"
        print(
            "latest={status} fixed:{fixed:.3f} segment:{segment:.3f} avg_error:{avg:.6f}s elapsed:{elapsed:.3f}s {name}".format(
                status="pass" if latest.get("passed") else "fail",
                fixed=float(latest.get("fixed_locked_ratio", 0.0)),
                segment=float(latest.get("segment_locked_ratio", 0.0)),
                avg=float(latest.get("avg_error_after_sec", 999.0)),
                elapsed=float(latest.get("elapsed_sec", 0.0)),
                name=latest.get("name", ""),
            )
        )
        if runtime:
            print(
                "latest_runtime={accelerator}+{infer}+{hybrid} verify_top_k={topk} failed_checks={failed}".format(
                    accelerator=runtime.get("inference_accelerator", ""),
                    infer=runtime.get("inference_candidate_strategy", ""),
                    hybrid=runtime.get("hybrid_search_strategy", ""),
                    topk=runtime.get("hybrid_verify_top_k", ""),
                    failed=failed_checks,
                )
            )
    trend = summary.get("trend") or {}
    if trend:
        print(
            "trend=avg_error_delta:{avg:+.6f}s elapsed_delta:{elapsed:+.3f}s fixed_lock_delta:{fixed:+.3f}".format(
                avg=float(trend.get("avg_error_delta_latest_minus_previous_sec", 0.0)),
                elapsed=float(trend.get("elapsed_delta_latest_minus_previous_sec", 0.0)),
                fixed=float(trend.get("fixed_lock_delta_latest_minus_previous", 0.0)),
            )
        )
    for idx, row in enumerate(summary.get("best_lock") or [], start=1):
        print(
            "best_lock_{idx}=fixed:{fixed:.3f} segment:{segment:.3f} avg_error:{avg:.6f}s elapsed:{elapsed:.3f}s {name}".format(
                idx=idx,
                fixed=float(row.get("fixed_locked_ratio", 0.0)),
                segment=float(row.get("segment_locked_ratio", 0.0)),
                avg=float(row.get("avg_error_after_sec", 999.0)),
                elapsed=float(row.get("elapsed_sec", 0.0)),
                name=row.get("name", ""),
            )
        )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", type=str, default="outputs/*canary*.json")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = sorted(Path().glob(args.glob))
    summary = summarize_canary_history(paths, top_n=args.top_n)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Saved canary history summary: {args.output}")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print_summary(summary)


if __name__ == "__main__":
    main()
