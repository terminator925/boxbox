from __future__ import annotations

import json
from pathlib import Path

from backend.ml.sample_era_classifier import classify_manifest, classify_track, extract_years


def _track(name: str, **kwargs) -> dict:
    return {
        "name": name,
        "relative_path": name,
        "path": name,
        "has_filename_bpm": False,
        "likely_loop_sample": False,
        **kwargs,
    }


def test_extract_years_reads_years_and_decades():
    assert extract_years("Edith Piaf 1960 and 90's remix") == [1960, 1990]


def test_classify_track_marks_old_year_as_likely_unquantized():
    result = classify_track(_track("08EdithPiaf-NonJeNeRegretteRien-1960.mp3"))

    assert result["classification"] == "likely_older_unquantized"
    assert result["earliest_year"] == 1960


def test_classify_track_marks_bpm_type_beat_as_modern_grid():
    result = classify_track(_track("FREE Trap Type Beat 140 BPM.mp3", has_filename_bpm=True))

    assert result["classification"] == "likely_modern_grid"
    assert "explicit_filename_bpm" in result["classification_reasons"]


def test_classify_manifest_outputs_ranked_groups(tmp_path: Path):
    manifest = tmp_path / "tracks.json"
    manifest.write_text(
        json.dumps(
            {
                "tracks": [
                    _track("old soul 1972.wav"),
                    _track("modern type beat 150 BPM.mp3", has_filename_bpm=True),
                    _track("unknown song.wav"),
                ]
            }
        ),
        encoding="utf-8",
    )

    report = classify_manifest(manifest, older_count=10, uncertain_count=10)

    assert report["classification_counts"]["likely_older_unquantized"] == 1
    assert report["classification_counts"]["likely_modern_grid"] == 1
    assert report["classification_counts"]["uncertain"] == 1
    assert report["likely_older_unquantized"][0]["name"] == "old soul 1972.wav"
