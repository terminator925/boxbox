from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path


def cleanup_duplicates(examples_dir: Path, dry_run: bool = False) -> dict[str, int]:
    by_key: dict[str, list[Path]] = defaultdict(list)
    for sub in sorted(examples_dir.iterdir()):
        if not sub.is_dir():
            continue
        meta_path = sub / "meta.json"
        if not meta_path.exists():
            continue
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = payload.get("source_member") or payload.get("performance_member")
        if not isinstance(key, str) or not key:
            continue
        by_key[key].append(sub)

    removed = 0
    duplicate_groups = 0
    for paths in by_key.values():
        if len(paths) < 2:
            continue
        duplicate_groups += 1
        for extra in paths[1:]:
            removed += 1
            if not dry_run:
                shutil.rmtree(extra)

    return {"duplicate_groups": duplicate_groups, "removed_examples": removed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=Path, default=Path("data/examples"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = cleanup_duplicates(args.examples, dry_run=args.dry_run)
    for key, value in stats.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
