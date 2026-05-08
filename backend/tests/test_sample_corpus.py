from __future__ import annotations

import json
from pathlib import Path

from backend.ml.sample_corpus import build_corpus_manifest, duration_bucket, select_tracks


def _track(name: str, duration: float, ext: str = ".wav", bpm: float | None = None) -> dict:
    return {
        "name": name,
        "relative_path": name,
        "path": name,
        "duration_sec": duration,
        "extension": ext,
        "filename_bpm": bpm,
        "has_filename_bpm": bpm is not None,
    }


def test_duration_bucket_groups_tracks():
    assert duration_bucket(_track("a", 90)) == "short"
    assert duration_bucket(_track("b", 180)) == "medium"
    assert duration_bucket(_track("c", 300)) == "long"
    assert duration_bucket(_track("d", 500)) == "very_long"


def test_select_tracks_prefers_filename_bpm_and_is_deterministic():
    tracks = [
        _track("a.wav", 90),
        _track("b.wav", 180, bpm=120.0),
        _track("c.mp3", 300),
        _track("d.flac", 500),
    ]

    first = select_tracks(tracks, count=3, seed=3)
    second = select_tracks(tracks, count=3, seed=3)

    assert [track["name"] for track in first] == [track["name"] for track in second]
    assert first[0]["name"] == "b.wav"
    assert len(first) == 3


def test_build_corpus_manifest_summarizes_selection(tmp_path: Path):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps({"tracks": [_track("a.wav", 90), _track("b.mp3", 180, bpm=120.0), _track("c.mp3", 300)]}),
        encoding="utf-8",
    )

    manifest = build_corpus_manifest(source, count=2, seed=5, name="test")

    assert manifest["name"] == "test"
    assert manifest["selected_count"] == 2
    assert manifest["available_count"] == 3
    assert manifest["filename_bpm_count"] == 1
