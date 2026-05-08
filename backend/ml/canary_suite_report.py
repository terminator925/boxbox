from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _suite_payload(report: dict[str, Any]) -> dict[str, Any]:
    suite = report.get("metronome_canary_suite")
    if isinstance(suite, dict) and "files" in suite:
        return suite
    if "files" in report and "summary" in report:
        return report
    raise RuntimeError("Report does not contain a metronome_canary_suite payload")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _row(item: dict[str, Any]) -> dict[str, Any]:
    gate = dict(item.get("metronome_canary_gate") or {})
    observed = dict(gate.get("observed") or {})
    manifest = dict(item.get("manifest_track") or {})
    failed_checks = [str(value) for value in gate.get("failed_checks") or []]
    return {
        "audio_path": str(item.get("audio_path") or ""),
        "relative_path": str(manifest.get("relative_path") or Path(str(item.get("audio_path") or "")).name),
        "target_bpm": _safe_float(item.get("target_bpm", observed.get("target_bpm"))),
        "target_bpm_source": str(manifest.get("target_bpm_source") or ""),
        "passed": bool(gate.get("passed", False)),
        "failed_checks": failed_checks,
        "failure_summary": str(gate.get("failure_summary", "")),
        "verdict": str(observed.get("verdict", "")),
        "segment_locked_ratio": _safe_float(observed.get("segment_locked_ratio")),
        "fixed_locked_ratio": _safe_float(observed.get("fixed_locked_ratio")),
        "raw_fixed_locked_ratio": _safe_float(observed.get("raw_fixed_locked_ratio")),
        "avg_error_after_sec": _safe_float(observed.get("avg_error_after_sec"), 999.0),
        "elapsed_sec": _safe_float(observed.get("elapsed_sec")),
        "max_window_offset_jump_sec": _safe_float(observed.get("max_window_offset_jump_sec")),
        "diagnostic_summary": str(observed.get("daw_lock_diagnostic_summary", "")),
        "report_path": str(item.get("report_path") or ""),
    }


def summarize_canary_suite_report(report_path: Path, *, top_n: int = 10) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    suite = _suite_payload(report)
    rows = [_row(dict(item)) for item in suite.get("files") or []]
    failures = [row for row in rows if not row["passed"]]
    hardest_failures = sorted(
        failures,
        key=lambda row: (
            float(row["fixed_locked_ratio"]),
            float(row["segment_locked_ratio"]),
            -float(row["avg_error_after_sec"]),
            row["relative_path"],
        ),
    )[: int(top_n)]
    slowest = sorted(
        [row for row in rows if float(row["elapsed_sec"]) > 0],
        key=lambda row: (-float(row["elapsed_sec"]), row["relative_path"]),
    )[: int(top_n)]
    best_lock = sorted(
        rows,
        key=lambda row: (
            -float(row["fixed_locked_ratio"]),
            -float(row["segment_locked_ratio"]),
            float(row["avg_error_after_sec"]),
            float(row["elapsed_sec"]),
            row["relative_path"],
        ),
    )[: int(top_n)]
    failed_check_counts: dict[str, int] = {}
    for row in failures:
        for check in row["failed_checks"]:
            failed_check_counts[check] = failed_check_counts.get(check, 0) + 1
    return {
        "report_path": str(report_path),
        "summary": suite.get("summary") or {},
        "file_count": len(rows),
        "pass_count": len([row for row in rows if row["passed"]]),
        "fail_count": len(failures),
        "failed_files": suite.get("failed_files") or [],
        "skipped_files": suite.get("skipped_files") or [],
        "failed_check_counts": failed_check_counts,
        "hardest_failures": hardest_failures,
        "slowest_tracks": slowest,
        "best_lock": best_lock,
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"report_path={summary['report_path']}")
    print(f"file_count={summary['file_count']}")
    print(f"pass_count={summary['pass_count']}")
    print(f"fail_count={summary['fail_count']}")
    print(f"failed_check_counts={json.dumps(summary['failed_check_counts'], sort_keys=True)}")
    for idx, row in enumerate(summary["hardest_failures"], start=1):
        print(
            "failure_{idx}=fixed:{fixed:.3f} segment:{segment:.3f} avg_error:{avg:.6f}s checks:{checks} {path}".format(
                idx=idx,
                fixed=float(row["fixed_locked_ratio"]),
                segment=float(row["segment_locked_ratio"]),
                avg=float(row["avg_error_after_sec"]),
                checks=",".join(row["failed_checks"]) or "unknown",
                path=row["relative_path"],
            )
        )
    for idx, row in enumerate(summary["slowest_tracks"], start=1):
        print(
            "slowest_{idx}=elapsed:{elapsed:.3f}s fixed:{fixed:.3f} avg_error:{avg:.6f}s {path}".format(
                idx=idx,
                elapsed=float(row["elapsed_sec"]),
                fixed=float(row["fixed_locked_ratio"]),
                avg=float(row["avg_error_after_sec"]),
                path=row["relative_path"],
            )
        )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = summarize_canary_suite_report(args.report, top_n=args.top_n)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Saved metronome canary suite summary: {args.output}")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print_summary(summary)


if __name__ == "__main__":
    main()
