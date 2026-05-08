from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

from backend.ml.import_public_midi import _maestro_rows


def test_maestro_rows_reads_embedded_metadata_csv(tmp_path: Path):
    zip_path = tmp_path / "maestro.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "canonical_composer",
                "canonical_title",
                "split",
                "year",
                "midi_filename",
                "audio_filename",
                "duration",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "canonical_composer": "Bach",
                "canonical_title": "Prelude",
                "split": "train",
                "year": "2018",
                "midi_filename": "2018/example.midi",
                "audio_filename": "2018/example.wav",
                "duration": "12.34",
            }
        )
        archive.writestr("maestro-v3.0.0/maestro-v3.0.0.csv", buffer.getvalue())

    with zipfile.ZipFile(zip_path) as archive:
        rows = _maestro_rows(archive)

    assert len(rows) == 1
    assert rows[0]["canonical_composer"] == "Bach"
    assert rows[0]["audio_filename"] == "2018/example.wav"
