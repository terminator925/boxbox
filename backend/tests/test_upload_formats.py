from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from backend.app import _metadata_bpm_from_source_meta, app


client = TestClient(app)


def test_upload_flac_is_accepted_and_reports_source_metadata(tmp_path: Path):
    sr = 48000
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    mono = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)

    source = tmp_path / "source.flac"
    sf.write(str(source), mono, sr, format="FLAC")

    with source.open("rb") as handle:
        resp = client.post("/api/upload", files={"file": ("source.flac", handle, "audio/flac")})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["source_extension"] == ".flac"
    assert payload["source_sample_rate"] == sr
    assert payload["is_lossless_source"] is True
    assert abs(payload["duration_sec"] - 2.0) < 0.05


def test_metadata_bpm_from_source_meta_reads_credible_tbpm():
    assert _metadata_bpm_from_source_meta({"metadata_tags": {"tbpm": "163.01"}}) == 163.01
    assert _metadata_bpm_from_source_meta({"metadata_tags": {"tbpm": "not-a-tempo"}}) is None
    assert _metadata_bpm_from_source_meta({"metadata_tags": {"tbpm": "999"}}) is None


def test_metadata_bpm_from_source_meta_reads_serato_autogain():
    autgain = "YXBwbGljYXRpb24vb2N0ZXQtc3RyZWFtAABTZXJhdG8gQXV0b3RhZ3MAAQExMjguMDAALTIuNTE2ADAuMDAA"

    assert _metadata_bpm_from_source_meta({"metadata_tags": {"autgain": autgain}}) == 128.0
