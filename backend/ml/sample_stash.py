from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {".wav", ".wave", ".flac", ".mp3", ".ogg", ".opus", ".aiff", ".aif", ".m4a", ".aac", ".wma"}
DEFAULT_STEM_MARKERS = ("(drums)", "(bass)", "(vocals)", "(other)")
DEFAULT_LOOP_MARKERS = ("loop", "one shot", "oneshot")
BPM_PATTERN = re.compile(r"(?<!\d)([4-9]\d|1\d{2}|2[0-4]\d)\s*bpm(?![a-z])", re.IGNORECASE)


def is_split_stem(path: Path, *, stem_markers: tuple[str, ...] = DEFAULT_STEM_MARKERS) -> bool:
    name = path.name.lower()
    return any(marker.lower() in name for marker in stem_markers)


def is_likely_loop_sample(path: Path, *, loop_markers: tuple[str, ...] = DEFAULT_LOOP_MARKERS) -> bool:
    name = path.stem.lower()
    return any(marker.lower() in name for marker in loop_markers)


def extract_filename_bpm(path: Path) -> float | None:
    match = BPM_PATTERN.search(path.stem)
    if not match:
        return None
    return float(match.group(1))


def probe_audio_duration_sec(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        payload = json.loads(result.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration"))
    except Exception:
        return None
    return duration if duration > 0 else None


def _audio_record(path: Path, root: Path, *, probe_duration: bool = False) -> dict[str, Any]:
    stat = path.stat()
    filename_bpm = extract_filename_bpm(path)
    duration_sec = probe_audio_duration_sec(path) if probe_duration else None
    return {
        "path": str(path),
        "relative_path": str(path.relative_to(root)),
        "name": path.name,
        "extension": path.suffix.lower(),
        "bytes": int(stat.st_size),
        "likely_split_stem": is_split_stem(path),
        "likely_loop_sample": is_likely_loop_sample(path),
        "filename_bpm": filename_bpm,
        "has_filename_bpm": filename_bpm is not None,
        "duration_sec": duration_sec,
        "duration_probe_failed": bool(probe_duration and duration_sec is None),
    }


def inventory_sample_stash(
    root: Path,
    *,
    include_stems: bool = False,
    exclude_likely_loops: bool = False,
    min_bytes: int = 0,
    probe_duration: bool = False,
    min_duration_sec: float = 0.0,
    limit: int | None = None,
) -> dict[str, Any]:
    if not root.exists():
        raise FileNotFoundError(f"Sample stash folder does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Sample stash path is not a folder: {root}")

    should_probe_duration = bool(probe_duration or float(min_duration_sec) > 0.0)
    all_audio = [
        _audio_record(path, root, probe_duration=should_probe_duration)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    stems = [record for record in all_audio if bool(record["likely_split_stem"])]
    eligible = all_audio if include_stems else [record for record in all_audio if not bool(record["likely_split_stem"])]
    loops = [record for record in eligible if bool(record["likely_loop_sample"])]
    if exclude_likely_loops:
        eligible = [record for record in eligible if not bool(record["likely_loop_sample"])]
    if int(min_bytes) > 0:
        eligible = [record for record in eligible if int(record["bytes"]) >= int(min_bytes)]
    if float(min_duration_sec) > 0.0:
        eligible = [
            record
            for record in eligible
            if record["duration_sec"] is not None and float(record["duration_sec"]) >= float(min_duration_sec)
        ]
    if limit is not None and int(limit) > 0:
        eligible = eligible[: int(limit)]
    eligible_with_bpm = [record for record in eligible if bool(record["has_filename_bpm"])]
    duration_failures = [record for record in all_audio if bool(record["duration_probe_failed"])]

    return {
        "root": str(root),
        "audio_count": len(all_audio),
        "eligible_count": len(eligible),
        "eligible_with_filename_bpm_count": len(eligible_with_bpm),
        "excluded_stem_count": len(stems),
        "excluded_likely_loop_count": len(loops) if exclude_likely_loops else 0,
        "include_stems": bool(include_stems),
        "exclude_likely_loops": bool(exclude_likely_loops),
        "min_bytes": int(min_bytes),
        "probe_duration": bool(should_probe_duration),
        "min_duration_sec": float(min_duration_sec),
        "duration_probe_failed_count": len(duration_failures),
        "stem_markers": list(DEFAULT_STEM_MARKERS),
        "loop_markers": list(DEFAULT_LOOP_MARKERS),
        "extensions": sorted(AUDIO_EXTENSIONS),
        "tracks": eligible,
    }


def write_manifest(summary: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved sample stash manifest: {output}")


def print_inventory(summary: dict[str, Any]) -> None:
    print(f"root={summary['root']}")
    print(f"audio_count={summary['audio_count']}")
    print(f"eligible_count={summary['eligible_count']}")
    print(f"eligible_with_filename_bpm_count={summary['eligible_with_filename_bpm_count']}")
    print(f"excluded_stem_count={summary['excluded_stem_count']}")
    print(f"excluded_likely_loop_count={summary['excluded_likely_loop_count']}")
    print(f"include_stems={1 if summary['include_stems'] else 0}")
    print(f"exclude_likely_loops={1 if summary['exclude_likely_loops'] else 0}")
    print(f"min_bytes={summary['min_bytes']}")
    print(f"probe_duration={1 if summary['probe_duration'] else 0}")
    print(f"min_duration_sec={summary['min_duration_sec']}")
    print(f"duration_probe_failed_count={summary['duration_probe_failed_count']}")
    for idx, record in enumerate(summary["tracks"][:10], start=1):
        print(f"track_{idx}={record['relative_path']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--include-stems", action="store_true")
    parser.add_argument("--exclude-likely-loops", action="store_true")
    parser.add_argument("--min-bytes", type=int, default=0)
    parser.add_argument("--probe-duration", action="store_true")
    parser.add_argument("--min-duration-sec", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = inventory_sample_stash(
        args.root,
        include_stems=args.include_stems,
        exclude_likely_loops=args.exclude_likely_loops,
        min_bytes=args.min_bytes,
        probe_duration=args.probe_duration,
        min_duration_sec=args.min_duration_sec,
        limit=args.limit if args.limit > 0 else None,
    )
    if args.output is not None:
        write_manifest(summary, args.output)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return
    print_inventory(summary)


if __name__ == "__main__":
    main()
