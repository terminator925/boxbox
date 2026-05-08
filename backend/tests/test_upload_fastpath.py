from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import app


client = TestClient(app)


def test_upload_uses_metadata_fastpath_without_audio_decode(monkeypatch):
    monkeypatch.setattr(
        "backend.app.convert_to_wav",
        lambda src_path, dst_path: {
            "duration_sec": 9.5,
            "sample_rate": 48000,
            "channels": 2,
            "original_filename": "clip.mp3",
            "container_extension": ".mp3",
            "codec_name": "mp3",
            "format_name": "mp3",
            "bit_rate": 320000,
            "bits_per_sample": 0,
            "is_lossless_source": False,
            "metadata_tags": {},
        },
    )
    monkeypatch.setattr("backend.app.load_audio", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upload should not decode audio")))
    monkeypatch.setattr("backend.app.estimate_tempo", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("upload should not estimate tempo from audio")))

    response = client.post("/api/upload", files={"file": ("clip.mp3", b"fake-audio", "audio/mpeg")})

    assert response.status_code == 200
    payload = response.json()
    assert payload["duration_sec"] == 9.5
    assert payload["sr"] == 48000
    assert payload["channels"] == 2
    assert "estimated_bpm" not in payload
