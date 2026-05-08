from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from backend.app import app
from backend.services.storage import read_json


client = TestClient(app)


def test_runtime_config_endpoint_reports_promoted_defaults():
    resp = client.get("/api/runtime-config")
    assert resp.status_code == 200

    payload = resp.json()
    assert payload["runtime_config"]["inference_accelerator"] == "torch"
    assert payload["runtime_config"]["inference_candidate_strategy"] == "core4_adaptive_plus"
    assert payload["runtime_config"]["hybrid_search_strategy"] == "core4"
    assert payload["runtime_config"]["hybrid_verify_top_k"] == 3
    assert payload["defaults"]["mode"] == "hybrid"
    assert payload["defaults"]["target_bpm"] == 100
    assert payload["defaults"]["groove_preserve"] == 50
    assert isinstance(payload["model_available"], bool)


def test_smoke_pipeline(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 4.0, int(sr * 4.0), endpoint=False)
    left = 0.2 * np.sin(2 * np.pi * 440 * t)
    right = 0.2 * np.sin(2 * np.pi * 442 * t)
    stereo = np.stack([left, right], axis=1).astype(np.float32)

    source = tmp_path / "test.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as f:
        resp = client.post("/api/upload", files={"file": ("test.wav", f, "audio/wav")})
    assert resp.status_code == 200
    payload = resp.json()
    job_id = payload["job_id"]
    assert payload["is_stereo"] is True
    assert abs(payload["duration_sec"] - 4.0) < 0.05
    assert payload["source_extension"] == ".wav"

    q = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 50, "mode": "hybrid"},
    )
    assert q.status_code == 200
    result = q.json()

    out = Path("outputs") / job_id / result["output_files"]["browser_audio"]
    assert out.exists()
    data, _ = sf.read(str(out), always_2d=True)
    assert data.shape[1] == 2
    metronome_check = Path("outputs") / job_id / result["output_files"]["metronome_check_audio"]
    assert metronome_check.exists()
    check_data, _ = sf.read(str(metronome_check), always_2d=True)
    assert check_data.shape[1] == 2
    assert check_data.shape[0] == data.shape[0]
    assert (Path("outputs") / job_id / result["output_files"]["master_audio"]).exists()
    report = read_json(Path("outputs") / job_id / result["output_files"]["report_json"])
    assert report["output_files"] == result["output_files"]
    assert report["output_files"]["metronome_check_audio"] == "metronome_check.wav"
    assert report["metronome_check"]["filename"] == "metronome_check.wav"
    assert report["metronome_check"]["target_bpm"] == 100.0
    assert report["metronome_check"]["beats_per_bar"] == 4
    assert report["metronome_check"]["channels"] == 2
    assert abs(report["metronome_check"]["duration_sec"] - report["duration_output_sec"]) < 0.001
    assert "style_guided_groove_preserve" in report
    assert "style_adaptation" in report
    assert "guided_groove_preserve" in report["style_adaptation"]
    assert report["runtime_config"]["inference_candidate_strategy"] == "core4_adaptive_plus"
    assert report["runtime_config"]["hybrid_search_strategy"] == "core4"
    assert report["runtime_config"]["hybrid_verify_top_k"] == 3


def test_quantize_rounds_target_bpm_to_whole_number(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    stereo = np.stack([0.2 * np.sin(2 * np.pi * 440 * t), 0.2 * np.sin(2 * np.pi * 442 * t)], axis=1).astype(np.float32)

    source = tmp_path / "round.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as f:
        resp = client.post("/api/upload", files={"file": ("round.wav", f, "audio/wav")})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    q = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 104.2, "resolution": 8, "groove_preserve": 50, "mode": "hybrid"},
    )
    assert q.status_code == 200
    result = q.json()

    report = read_json(Path("outputs") / job_id / result["output_files"]["report_json"])
    assert report["target_bpm"] == 104.0
