from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _suite_payload(report: dict[str, Any]) -> dict[str, Any]:
    if "summary" in report and "files" in report:
        return report
    suite = report.get("real_audio_suite")
    if isinstance(suite, dict) and "summary" in suite and "files" in suite:
        return suite
    raise RuntimeError("Report does not contain a real_audio_suite payload")


def _metric(item: dict[str, Any], branch: str, key: str = "avg_abs_error_after_sec") -> float:
    payload = dict(item.get(branch) or {})
    return float(payload.get(key, 999.0))


def _processing_elapsed(item: dict[str, Any]) -> float:
    timing = dict(item.get("processing_timing") or {})
    return float(timing.get("elapsed_sec", 0.0) or 0.0)


def _slowest_stage(item: dict[str, Any]) -> dict[str, Any]:
    timing = dict(item.get("processing_timing") or {})
    stages = dict(timing.get("stages") or {})
    if not stages:
        return {"stage": "", "elapsed_sec": 0.0}
    stage, elapsed = max(stages.items(), key=lambda pair: float(pair[1]))
    return {"stage": str(stage), "elapsed_sec": float(elapsed)}


def _stage_timing_summary(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stage_values: dict[str, list[float]] = {}
    for item in files:
        timing = dict(item.get("processing_timing") or {})
        stages = dict(timing.get("stages") or {})
        for name, value in stages.items():
            stage_values.setdefault(str(name), []).append(float(value))
    rows = []
    for name, values in stage_values.items():
        if not values:
            continue
        rows.append(
            {
                "stage": name,
                "avg_sec": sum(values) / len(values),
                "max_sec": max(values),
                "count": len(values),
            }
        )
    return sorted(rows, key=lambda row: (-float(row["avg_sec"]), row["stage"]))


def summarize_benchmark_report(report_path: Path, *, top_n: int = 10) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    suite = _suite_payload(report)
    files = [dict(item) for item in suite.get("files") or []]
    rows: list[dict[str, Any]] = []
    for item in files:
        baseline = _metric(item, "baseline")
        ml = _metric(item, "ml")
        hybrid = _metric(item, "hybrid_effective")
        manifest_track = dict(item.get("manifest_track") or {})
        slowest_stage = _slowest_stage(item)
        rows.append(
            {
                "audio_path": str(item.get("audio_path") or ""),
                "relative_path": str(manifest_track.get("relative_path") or Path(str(item.get("audio_path") or "")).name),
                "target_bpm": item.get("target_bpm"),
                "target_bpm_source": manifest_track.get("target_bpm_source"),
                "baseline_error_after_sec": baseline,
                "ml_error_after_sec": ml,
                "hybrid_error_after_sec": hybrid,
                "hybrid_improvement_vs_baseline_sec": baseline - hybrid,
                "hybrid_regression_vs_baseline_sec": hybrid - baseline,
                "hybrid_selected": item.get("hybrid_selected") or (item.get("hybrid_candidate") or {}).get("candidate_name"),
                "clip_start_sec": item.get("clip_start_sec"),
                "clip_duration_sec": item.get("clip_duration_sec"),
                "processing_elapsed_sec": _processing_elapsed(item),
                "slowest_stage": slowest_stage["stage"],
                "slowest_stage_elapsed_sec": slowest_stage["elapsed_sec"],
            }
        )

    hardest = sorted(rows, key=lambda row: (-float(row["hybrid_error_after_sec"]), row["relative_path"]))[: int(top_n)]
    regressions = [
        row
        for row in sorted(rows, key=lambda row: (-float(row["hybrid_regression_vs_baseline_sec"]), row["relative_path"]))
        if float(row["hybrid_regression_vs_baseline_sec"]) > 0
    ][: int(top_n)]
    best_improvements = sorted(rows, key=lambda row: (-float(row["hybrid_improvement_vs_baseline_sec"]), row["relative_path"]))[
        : int(top_n)
    ]
    slowest_tracks = [
        row
        for row in sorted(rows, key=lambda row: (-float(row["processing_elapsed_sec"]), row["relative_path"]))
        if float(row["processing_elapsed_sec"]) > 0
    ][: int(top_n)]
    return {
        "report_path": str(report_path),
        "summary": suite.get("summary") or {},
        "file_count": len(rows),
        "top_n": int(top_n),
        "stage_timing_summary": _stage_timing_summary(files),
        "hardest_tracks": hardest,
        "hybrid_regressions": regressions,
        "best_hybrid_improvements": best_improvements,
        "slowest_tracks": slowest_tracks,
    }


def print_summary(summary: dict[str, Any]) -> None:
    print(f"report_path={summary['report_path']}")
    print(f"file_count={summary['file_count']}")
    print(f"suite_summary={json.dumps(summary['summary'], sort_keys=True)}")
    for row in summary["stage_timing_summary"][:5]:
        print(
            "stage_avg={stage}:{avg:.3f}s max:{max_sec:.3f}s count:{count}".format(
                stage=row["stage"],
                avg=float(row["avg_sec"]),
                max_sec=float(row["max_sec"]),
                count=int(row["count"]),
            )
        )
    for idx, row in enumerate(summary["hardest_tracks"], start=1):
        print(
            "hardest_{idx}=hybrid_error:{err:.6f} baseline:{base:.6f} improvement:{improve:.6f} {path}".format(
                idx=idx,
                err=float(row["hybrid_error_after_sec"]),
                base=float(row["baseline_error_after_sec"]),
                improve=float(row["hybrid_improvement_vs_baseline_sec"]),
                path=row["relative_path"],
            )
        )
    for idx, row in enumerate(summary["hybrid_regressions"], start=1):
        print(
            "regression_{idx}=regression:{regress:.6f} hybrid:{hybrid:.6f} baseline:{base:.6f} {path}".format(
                idx=idx,
                regress=float(row["hybrid_regression_vs_baseline_sec"]),
                hybrid=float(row["hybrid_error_after_sec"]),
                base=float(row["baseline_error_after_sec"]),
                path=row["relative_path"],
            )
        )
    for idx, row in enumerate(summary["slowest_tracks"], start=1):
        print(
            "slowest_{idx}=elapsed:{elapsed:.3f}s stage:{stage}:{stage_elapsed:.3f}s {path}".format(
                idx=idx,
                elapsed=float(row["processing_elapsed_sec"]),
                stage=row["slowest_stage"],
                stage_elapsed=float(row["slowest_stage_elapsed_sec"]),
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

    summary = summarize_benchmark_report(args.report, top_n=args.top_n)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(f"Saved benchmark summary: {args.output}")
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print_summary(summary)


if __name__ == "__main__":
    main()
