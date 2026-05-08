from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.ml.dataset import (
    _meta_quality_ok,
    _read_example_meta,
    dataset_family_from_dir,
    normalize_dataset_filters,
    normalize_examples_roots,
)

AUDIO_EXTENSIONS = {".wav", ".wave", ".flac", ".mp3", ".ogg", ".aiff", ".aif", ".m4a"}
MIDI_EXTENSIONS = {".mid", ".midi"}
REAL_AUDIO_FAMILIES = {"legacy_audio", "musicnet_audio", "maestro_audio"}


def classify_source_kind(example_dir: Path, meta: dict[str, Any]) -> str:
    family = dataset_family_from_dir(example_dir).lower()
    dataset = str(meta.get("dataset") or family).lower()
    source_member = str(meta.get("source_member") or "").lower()
    member_suffix = Path(source_member).suffix.lower() if source_member else ""

    if "synthetic" in {family, dataset} or family.startswith("synthetic") or dataset.startswith("synthetic"):
        return "synthetic"
    if family in REAL_AUDIO_FAMILIES or dataset in REAL_AUDIO_FAMILIES or member_suffix in AUDIO_EXTENSIONS:
        return "real_audio"
    if member_suffix in MIDI_EXTENSIONS or meta.get("performance_member") or meta.get("score_member"):
        return "midi_import"
    if family.endswith("_audio") or dataset.endswith("_audio"):
        return "real_audio"
    return "unknown"


def source_group(source_kind: str) -> str:
    if source_kind == "real_audio":
        return "real"
    if source_kind in {"midi_import", "synthetic"}:
        return "generated"
    return "unknown"


def _pct(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((float(count) / float(total)) * 100.0, 2)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    by_family = Counter(str(record["family"]) for record in records)
    by_kind = Counter(str(record["source_kind"]) for record in records)
    by_group = Counter(str(record["source_group"]) for record in records)

    return {
        "examples": total,
        "by_family": dict(sorted(by_family.items())),
        "by_source_kind": dict(sorted(by_kind.items())),
        "by_source_group": dict(sorted(by_group.items())),
        "generated_examples": int(by_group.get("generated", 0)),
        "real_examples": int(by_group.get("real", 0)),
        "unknown_examples": int(by_group.get("unknown", 0)),
        "generated_pct": _pct(int(by_group.get("generated", 0)), total),
        "real_pct": _pct(int(by_group.get("real", 0)), total),
        "unknown_pct": _pct(int(by_group.get("unknown", 0)), total),
    }


def inventory_dataset_items(items: list[tuple[Path, Path]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for original, _warped in items:
        example_dir = original.parent
        meta = _read_example_meta(example_dir)
        family = dataset_family_from_dir(example_dir)
        kind = classify_source_kind(example_dir, meta)
        records.append(
            {
                "name": example_dir.name,
                "family": family,
                "dataset": str(meta.get("dataset") or family),
                "source_kind": kind,
                "source_group": source_group(kind),
                "has_meta": bool(meta),
            }
        )
    summary = summarize_records(records)
    summary["records"] = records
    return summary


def limit_records_by_family(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(records) <= limit:
        return records
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["family"]), []).append(record)

    selected: list[dict[str, Any]] = []
    family_names = sorted(grouped)
    cursor = 0
    while len(selected) < limit and family_names:
        family = family_names[cursor % len(family_names)]
        bucket = grouped[family]
        if bucket:
            selected.append(bucket.pop(0))
        if not bucket:
            family_names.remove(family)
            if family_names:
                cursor %= len(family_names)
            continue
        cursor += 1
    return selected


def inventory_examples(
    examples_root: Path | str | list[Path] | tuple[Path, ...],
    *,
    dataset_filters: str | list[str] | tuple[str, ...] | None = None,
    min_improvement_pct: float | None = None,
    max_after_sec: float | None = None,
    min_event_count: int | None = None,
    max_examples: int | None = None,
) -> dict[str, Any]:
    roots = normalize_examples_roots(examples_root)
    filters = normalize_dataset_filters(dataset_filters)
    records: list[dict[str, Any]] = []
    skipped_missing_pair = 0
    skipped_quality = 0

    for root in roots:
        if not root.exists():
            continue
        for sub in sorted(root.glob("*")):
            if not sub.is_dir():
                continue
            if filters and not any(sub.name.startswith(prefix) for prefix in filters):
                continue
            original = sub / "original.wav"
            warped = sub / "warped.wav"
            if not (original.exists() and warped.exists()):
                skipped_missing_pair += 1
                continue

            meta = _read_example_meta(sub)
            if not _meta_quality_ok(
                meta,
                min_improvement_pct=min_improvement_pct,
                max_after_sec=max_after_sec,
                min_event_count=min_event_count,
            ):
                skipped_quality += 1
                continue

            family = dataset_family_from_dir(sub)
            kind = classify_source_kind(sub, meta)
            records.append(
                {
                    "name": sub.name,
                    "root": str(root),
                    "family": family,
                    "dataset": str(meta.get("dataset") or family),
                    "source_kind": kind,
                    "source_group": source_group(kind),
                    "has_meta": bool(meta),
                    "event_count": meta.get("event_count"),
                    "improvement_pct": (meta.get("timing_metrics") or {}).get("improvement_pct"),
                    "avg_abs_error_after_sec": (meta.get("timing_metrics") or {}).get("avg_abs_error_after_sec"),
                }
            )

    if max_examples is not None and int(max_examples) > 0:
        records = limit_records_by_family(records, int(max_examples))

    summary = summarize_records(records)
    summary.update(
        {
        "roots": [str(root) for root in roots],
        "filters": filters,
        "skipped_missing_pair": skipped_missing_pair,
        "skipped_quality": skipped_quality,
        "records": records,
        }
    )
    return summary


def print_inventory(summary: dict[str, Any], *, include_records: bool = False) -> None:
    print(f"examples={summary['examples']}")
    print(f"generated_pct={summary['generated_pct']}")
    print(f"real_pct={summary['real_pct']}")
    print(f"unknown_pct={summary['unknown_pct']}")
    print(f"by_source_group={json.dumps(summary['by_source_group'], sort_keys=True)}")
    print(f"by_source_kind={json.dumps(summary['by_source_kind'], sort_keys=True)}")
    print(f"by_family={json.dumps(summary['by_family'], sort_keys=True)}")
    print(f"skipped_missing_pair={summary['skipped_missing_pair']}")
    print(f"skipped_quality={summary['skipped_quality']}")
    if include_records:
        print(f"records={json.dumps(summary['records'], sort_keys=True)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=str, default="data/examples")
    parser.add_argument("--datasets", type=str, default="")
    parser.add_argument("--min-improvement-pct", type=float, default=None)
    parser.add_argument("--max-after-sec", type=float, default=None)
    parser.add_argument("--min-event-count", type=int, default=None)
    parser.add_argument("--max-examples", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-records", action="store_true")
    args = parser.parse_args()

    summary = inventory_examples(
        args.examples,
        dataset_filters=args.datasets or None,
        min_improvement_pct=args.min_improvement_pct,
        max_after_sec=args.max_after_sec,
        min_event_count=args.min_event_count,
        max_examples=args.max_examples,
    )
    if args.json:
        payload = dict(summary)
        if not args.include_records:
            payload.pop("records", None)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print_inventory(summary, include_records=args.include_records)


if __name__ == "__main__":
    main()
