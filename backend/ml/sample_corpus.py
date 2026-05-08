from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any


def load_tracks(manifest_path: Path) -> list[dict[str, Any]]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    tracks = payload.get("tracks")
    if not isinstance(tracks, list):
        raise RuntimeError(f"Manifest has no tracks list: {manifest_path}")
    return [dict(track) for track in tracks]


def duration_bucket(track: dict[str, Any]) -> str:
    duration = float(track.get("duration_sec") or 0.0)
    if duration < 120:
        return "short"
    if duration < 240:
        return "medium"
    if duration < 420:
        return "long"
    return "very_long"


def _bucket_key(track: dict[str, Any]) -> tuple[str, str]:
    return (duration_bucket(track), str(track.get("extension") or ""))


def select_tracks(
    tracks: list[dict[str, Any]],
    *,
    count: int,
    seed: int = 7,
    prefer_filename_bpm: bool = True,
) -> list[dict[str, Any]]:
    if count <= 0 or count >= len(tracks):
        return list(tracks)

    rng = random.Random(int(seed))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(track: dict[str, Any]) -> None:
        path = str(track.get("path") or track.get("relative_path") or track.get("name"))
        if path not in seen and len(selected) < int(count):
            selected.append(track)
            seen.add(path)

    if prefer_filename_bpm:
        bpm_tracks = [track for track in tracks if bool(track.get("has_filename_bpm"))]
        rng.shuffle(bpm_tracks)
        for track in bpm_tracks:
            add(track)

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for track in tracks:
        buckets.setdefault(_bucket_key(track), []).append(track)
    for bucket_tracks in buckets.values():
        rng.shuffle(bucket_tracks)

    keys = sorted(buckets)
    cursor = 0
    while len(selected) < int(count) and keys:
        key = keys[cursor % len(keys)]
        bucket = buckets[key]
        if bucket:
            add(bucket.pop(0))
        if not bucket:
            keys.remove(key)
            if keys:
                cursor %= len(keys)
            continue
        cursor += 1

    return selected


def build_corpus_manifest(
    source_manifest: Path,
    *,
    count: int,
    seed: int = 7,
    name: str = "sample_stash_corpus",
) -> dict[str, Any]:
    tracks = load_tracks(source_manifest)
    selected = select_tracks(tracks, count=count, seed=seed)
    by_duration_bucket: dict[str, int] = {}
    by_extension: dict[str, int] = {}
    for track in selected:
        by_duration_bucket[duration_bucket(track)] = by_duration_bucket.get(duration_bucket(track), 0) + 1
        ext = str(track.get("extension") or "")
        by_extension[ext] = by_extension.get(ext, 0) + 1
    return {
        "name": name,
        "source_manifest": str(source_manifest),
        "selected_count": len(selected),
        "available_count": len(tracks),
        "seed": int(seed),
        "by_duration_bucket": dict(sorted(by_duration_bucket.items())),
        "by_extension": dict(sorted(by_extension.items())),
        "filename_bpm_count": sum(1 for track in selected if bool(track.get("has_filename_bpm"))),
        "tracks": selected,
    }


def write_corpus_manifest(manifest: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved sample corpus manifest: {output}")


def print_corpus_manifest(manifest: dict[str, Any]) -> None:
    print(f"name={manifest['name']}")
    print(f"selected_count={manifest['selected_count']}")
    print(f"available_count={manifest['available_count']}")
    print(f"filename_bpm_count={manifest['filename_bpm_count']}")
    print(f"by_duration_bucket={json.dumps(manifest['by_duration_bucket'], sort_keys=True)}")
    print(f"by_extension={json.dumps(manifest['by_extension'], sort_keys=True)}")
    for idx, track in enumerate(manifest["tracks"][:10], start=1):
        bpm = track.get("filename_bpm")
        bpm_text = "" if bpm is None else f" bpm={bpm}"
        print(f"track_{idx}={track.get('relative_path')}{bpm_text}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("data/sample_stash_full_track_candidates.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--name", type=str, default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    manifest = build_corpus_manifest(
        args.manifest,
        count=args.count,
        seed=args.seed,
        name=args.name or args.output.stem,
    )
    write_corpus_manifest(manifest, args.output)
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return
    print_corpus_manifest(manifest)


if __name__ == "__main__":
    main()
