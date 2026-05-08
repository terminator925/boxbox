from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from backend.ml.sample_corpus import load_tracks, write_corpus_manifest

YEAR_PATTERN = re.compile(r"(?<!\d)(19[0-9]{2}|20[0-2][0-9])(?!\d)")
DECADE_PATTERN = re.compile(r"(?<!\d)([1-2]?[0-9])0'?s(?![a-z])", re.IGNORECASE)

OLDER_STYLE_MARKERS = {
    "60s",
    "70s",
    "80s",
    "90s",
    "oldies",
    "old school",
    "soul",
    "funk",
    "disco",
    "jazz",
    "swing",
    "motown",
    "vinyl",
    "classic",
    "sample",
}
MODERN_GRID_MARKERS = {
    "bpm",
    "loop",
    "type beat",
    "prod.",
    "prod ",
    "producer",
    "drill",
    "trap",
    "pluggnb",
    "rnb",
    "edm",
    "house",
    "remix",
    "instrumental",
}


def extract_years(text: str) -> list[int]:
    years = [int(match.group(1)) for match in YEAR_PATTERN.finditer(text)]
    for match in DECADE_PATTERN.finditer(text):
        decade = int(match.group(1))
        if decade < 100:
            years.append(1900 + decade * 10)
    return sorted(set(years))


def ffprobe_tags(path: Path) -> dict[str, str]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        payload = json.loads(result.stdout or "{}")
    except Exception:
        return {}
    tags = (payload.get("format") or {}).get("tags") or {}
    return {str(key).lower(): str(value) for key, value in tags.items()}


def _contains_any(text: str, markers: set[str]) -> list[str]:
    lowered = text.lower()
    return sorted(marker for marker in markers if marker in lowered)


def classify_track(track: dict[str, Any], *, probe_tags: bool = False) -> dict[str, Any]:
    path = Path(str(track.get("path") or ""))
    text = " ".join(
        str(value)
        for value in (
            track.get("name"),
            track.get("relative_path"),
            track.get("path"),
        )
        if value
    )
    tags = ffprobe_tags(path) if probe_tags and path.exists() else {}
    tag_text = " ".join(tags.values())
    years = extract_years(f"{text} {tag_text}")
    earliest_year = min(years) if years else None
    older_markers = _contains_any(text, OLDER_STYLE_MARKERS)
    modern_markers = _contains_any(text, MODERN_GRID_MARKERS)

    older_score = 0
    modern_score = 0
    reasons: list[str] = []

    if earliest_year is not None:
        if earliest_year <= 1985:
            older_score += 5
            reasons.append(f"year<={earliest_year}")
        elif earliest_year <= 1999:
            older_score += 4
            reasons.append(f"year={earliest_year}")
        elif earliest_year <= 2009:
            older_score += 1
            modern_score += 1
            reasons.append(f"transitional_year={earliest_year}")
        else:
            modern_score += 3
            reasons.append(f"modern_year={earliest_year}")

    if older_markers:
        older_score += min(4, len(older_markers) * 2)
        reasons.append("older_markers=" + ",".join(older_markers[:4]))
    if modern_markers:
        modern_score += min(5, len(modern_markers) * 2)
        reasons.append("modern_markers=" + ",".join(modern_markers[:4]))
    if bool(track.get("has_filename_bpm")):
        modern_score += 3
        reasons.append("explicit_filename_bpm")
    if bool(track.get("likely_loop_sample")):
        modern_score += 4
        reasons.append("likely_loop_sample")

    score = older_score - modern_score
    if score >= 3:
        classification = "likely_older_unquantized"
    elif score <= -3:
        classification = "likely_modern_grid"
    else:
        classification = "uncertain"

    enriched = dict(track)
    enriched.update(
        {
            "classification": classification,
            "older_unquantized_score": int(score),
            "older_score": int(older_score),
            "modern_grid_score": int(modern_score),
            "years": years,
            "earliest_year": earliest_year,
            "older_markers": older_markers,
            "modern_markers": modern_markers,
            "classification_reasons": reasons,
            "metadata_tags": tags if probe_tags else {},
        }
    )
    return enriched


def classify_manifest(
    manifest_path: Path,
    *,
    probe_tags: bool = False,
    older_count: int = 250,
    uncertain_count: int = 100,
) -> dict[str, Any]:
    tracks = [classify_track(track, probe_tags=probe_tags) for track in load_tracks(manifest_path)]
    counts: dict[str, int] = {}
    for track in tracks:
        key = str(track["classification"])
        counts[key] = counts.get(key, 0) + 1

    older_ranked = sorted(
        tracks,
        key=lambda item: (
            -int(item["older_unquantized_score"]),
            str(item.get("relative_path") or item.get("name")),
        ),
    )
    uncertain_ranked = [track for track in older_ranked if track["classification"] == "uncertain"]
    return {
        "source_manifest": str(manifest_path),
        "probe_tags": bool(probe_tags),
        "classification_counts": dict(sorted(counts.items())),
        "tracks": tracks,
        "likely_older_unquantized": [track for track in older_ranked if track["classification"] == "likely_older_unquantized"][
            : int(older_count)
        ],
        "likely_modern_grid": [track for track in older_ranked if track["classification"] == "likely_modern_grid"],
        "uncertain": uncertain_ranked[: int(uncertain_count)],
    }


def _corpus_from_tracks(name: str, source_manifest: Path, tracks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "source_manifest": str(source_manifest),
        "selected_count": len(tracks),
        "available_count": len(tracks),
        "classification_source": "sample_era_classifier",
        "tracks": tracks,
    }


def write_classified_outputs(report: dict[str, Any], output: Path, older_output: Path | None, uncertain_output: Path | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Saved era classification report: {output}")
    source = Path(str(report["source_manifest"]))
    if older_output is not None:
        write_corpus_manifest(
            _corpus_from_tracks("stash_likely_older_unquantized", source, list(report["likely_older_unquantized"])),
            older_output,
        )
    if uncertain_output is not None:
        write_corpus_manifest(_corpus_from_tracks("stash_uncertain_era", source, list(report["uncertain"])), uncertain_output)


def print_report(report: dict[str, Any]) -> None:
    print(f"classification_counts={json.dumps(report['classification_counts'], sort_keys=True)}")
    print(f"likely_older_unquantized_count={len(report['likely_older_unquantized'])}")
    print(f"likely_modern_grid_count={len(report['likely_modern_grid'])}")
    print(f"uncertain_count={len(report['uncertain'])}")
    for idx, track in enumerate(report["likely_older_unquantized"][:10], start=1):
        print(
            "older_{idx}=score:{score} year:{year} {path}".format(
                idx=idx,
                score=track["older_unquantized_score"],
                year=track.get("earliest_year") or "",
                path=track.get("relative_path") or track.get("name"),
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("data/sample_stash_full_track_candidates.json"))
    parser.add_argument("--output", type=Path, default=Path("data/sample_stash_era_classification.json"))
    parser.add_argument("--older-output", type=Path, default=Path("data/benchmark_corpus_stash_likely_older.json"))
    parser.add_argument("--uncertain-output", type=Path, default=Path("data/benchmark_corpus_stash_uncertain_era.json"))
    parser.add_argument("--older-count", type=int, default=250)
    parser.add_argument("--uncertain-count", type=int, default=100)
    parser.add_argument("--probe-tags", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = classify_manifest(
        args.manifest,
        probe_tags=args.probe_tags,
        older_count=args.older_count,
        uncertain_count=args.uncertain_count,
    )
    write_classified_outputs(report, args.output, args.older_output, args.uncertain_output)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print_report(report)


if __name__ == "__main__":
    main()
