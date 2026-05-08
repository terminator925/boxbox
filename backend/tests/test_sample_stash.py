from __future__ import annotations

import json
from pathlib import Path

from backend.ml.sample_stash import (
    extract_filename_bpm,
    inventory_sample_stash,
    is_likely_loop_sample,
    is_split_stem,
    write_manifest,
)


def test_is_split_stem_matches_user_markers_case_insensitively():
    assert is_split_stem(Path("song_(Drums).wav")) is True
    assert is_split_stem(Path("song (bass).mp3")) is True
    assert is_split_stem(Path("song_(Vocals).wav")) is True
    assert is_split_stem(Path("song_(Other).flac")) is True
    assert is_split_stem(Path("full song.wav")) is False


def test_extract_filename_bpm_reads_explicit_bpm_marker():
    assert extract_filename_bpm(Path("GRADE - FINESHYT 155 BPM (LOOP).mp3")) == 155.0
    assert extract_filename_bpm(Path("song 104bpm.wav")) == 104.0
    assert extract_filename_bpm(Path("song 147.mp3")) is None


def test_is_likely_loop_sample_matches_loop_markers():
    assert is_likely_loop_sample(Path("Drum Loop 95 BPM 5.mp3")) is True
    assert is_likely_loop_sample(Path("one shot hit.wav")) is True
    assert is_likely_loop_sample(Path("full song.wav")) is False


def test_inventory_sample_stash_excludes_split_stems_by_default(tmp_path: Path):
    root = tmp_path / "stash"
    root.mkdir()
    (root / "full track.wav").write_bytes(b"x")
    (root / "club track 128 BPM.wav").write_bytes(b"x")
    (root / "full track_(Drums).wav").write_bytes(b"x")
    (root / "notes.txt").write_text("ignore", encoding="utf-8")

    summary = inventory_sample_stash(root)

    assert summary["audio_count"] == 3
    assert summary["eligible_count"] == 2
    assert summary["eligible_with_filename_bpm_count"] == 1
    assert summary["excluded_stem_count"] == 1
    assert [track["name"] for track in summary["tracks"]] == ["club track 128 BPM.wav", "full track.wav"]


def test_inventory_sample_stash_can_include_stems_and_write_manifest(tmp_path: Path):
    root = tmp_path / "stash"
    root.mkdir()
    (root / "full track.wav").write_bytes(b"x")
    (root / "full track_(Vocals).wav").write_bytes(b"x")
    output = tmp_path / "manifest.json"

    summary = inventory_sample_stash(root, include_stems=True)
    write_manifest(summary, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["eligible_count"] == 2
    assert {track["name"] for track in payload["tracks"]} == {"full track.wav", "full track_(Vocals).wav"}


def test_inventory_sample_stash_can_filter_likely_loops_and_small_files(tmp_path: Path):
    root = tmp_path / "stash"
    root.mkdir()
    (root / "full track.wav").write_bytes(b"x" * 10)
    (root / "tiny full track.wav").write_bytes(b"x")
    (root / "drum loop.wav").write_bytes(b"x" * 10)

    summary = inventory_sample_stash(root, exclude_likely_loops=True, min_bytes=5)

    assert summary["eligible_count"] == 1
    assert summary["excluded_likely_loop_count"] == 1
    assert summary["tracks"][0]["name"] == "full track.wav"


def test_inventory_sample_stash_duration_filter_requires_successful_probe(tmp_path: Path):
    root = tmp_path / "stash"
    root.mkdir()
    (root / "not really audio.wav").write_bytes(b"x" * 10)

    summary = inventory_sample_stash(root, min_duration_sec=60.0)

    assert summary["probe_duration"] is True
    assert summary["duration_probe_failed_count"] == 1
    assert summary["eligible_count"] == 0
