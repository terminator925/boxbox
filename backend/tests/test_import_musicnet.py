from __future__ import annotations

import csv
import io
import tarfile
from pathlib import Path

from backend.ml.import_public_midi import _musicnet_entries, _musicnet_note_events


def test_musicnet_entries_match_wav_and_csv_members(tmp_path: Path):
    tar_path = tmp_path / "musicnet.tar.gz"
    with tarfile.open(tar_path, "w:gz") as archive:
        audio_bytes = b"RIFFxxxxWAVE"
        csv_bytes = b"start_time,end_time,note\n0,4410,60\n"

        wav_info = tarfile.TarInfo("musicnet/train_data/1234.wav")
        wav_info.size = len(audio_bytes)
        archive.addfile(wav_info, io.BytesIO(audio_bytes))

        csv_info = tarfile.TarInfo("musicnet/train_labels/1234.csv")
        csv_info.size = len(csv_bytes)
        archive.addfile(csv_info, io.BytesIO(csv_bytes))

        extra_info = tarfile.TarInfo("musicnet/train_labels/9999.csv")
        extra_info.size = len(csv_bytes)
        archive.addfile(extra_info, io.BytesIO(csv_bytes))

    with tarfile.open(tar_path, "r:gz") as archive:
        entries = _musicnet_entries(archive)

    assert entries == [("musicnet/train_data/1234.wav", "musicnet/train_labels/1234.csv")]


def test_musicnet_note_events_parse_sample_index_labels():
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["start_time", "end_time", "instrument", "note"])
    writer.writeheader()
    writer.writerow({"start_time": "0", "end_time": "4410", "instrument": "1", "note": "60"})
    writer.writerow({"start_time": "8820", "end_time": "13230", "instrument": "1", "note": "64"})
    writer.writerow({"start_time": "17640", "end_time": "22050", "instrument": "1", "note": "67"})
    writer.writerow({"start_time": "26460", "end_time": "30870", "instrument": "1", "note": "72"})

    events, bpm = _musicnet_note_events(buffer.getvalue(), sr=44100)

    assert len(events) == 4
    assert events[0].onset == 0.0
    assert round(events[1].onset, 3) == 0.2
    assert round(events[0].duration, 3) == 0.1
    assert events[0].midi == 60
    assert 40.0 <= bpm <= 220.0


def test_musicnet_priority_prefers_ensemble_over_solo_piano():
    from backend.ml.import_public_midi import _musicnet_priority

    solo = {"ensemble": "Solo Piano", "composer": "Mozart", "source": "Museopen"}
    quintet = {"ensemble": "String Quintet", "composer": "Mozart", "source": "European Archive"}

    assert _musicnet_priority(quintet) < _musicnet_priority(solo)
