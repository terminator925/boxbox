from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from backend.audio import io_utils


def test_load_audio_falls_back_to_ffmpeg_when_soundfile_fails(tmp_path: Path, monkeypatch):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"not really mp3")
    samples = np.array([[0.1, -0.1], [0.2, -0.2]], dtype=np.float32)

    def fake_sf_read(*args, **kwargs):
        raise RuntimeError("unsupported")

    def fake_run(cmd, capture_output=False, check=False):
        assert cmd[0] == "ffmpeg"
        return subprocess.CompletedProcess(cmd, 0, stdout=samples.tobytes(), stderr=b"")

    monkeypatch.setattr(io_utils.sf, "read", fake_sf_read)
    monkeypatch.setattr(io_utils, "probe_audio", lambda path: {"sample_rate": 48000, "channels": 2})
    monkeypatch.setattr(io_utils.subprocess, "run", fake_run)

    audio, sr = io_utils.load_audio(source)

    assert sr == 48000
    assert audio.shape == (2, 2)
    np.testing.assert_allclose(audio, samples)


def test_audio_info_from_meta_builds_upload_response():
    info = io_utils.audio_info_from_meta(
        {
            "duration_sec": 12.5,
            "sample_rate": 44100,
            "channels": 2,
            "original_filename": "song.mp3",
            "container_extension": ".mp3",
            "codec_name": "mp3",
            "format_name": "mp3",
            "bit_rate": 320000,
            "bits_per_sample": 0,
            "is_lossless_source": False,
        }
    )

    assert info["duration_sec"] == 12.5
    assert info["sr"] == 44100
    assert info["channels"] == 2
    assert info["is_stereo"] is True
    assert info["source_extension"] == ".mp3"
