from __future__ import annotations

import json
from pathlib import Path

import soundfile as sf

from backend.ml.synthetic_data import generate_dataset


def test_generate_synthetic_dataset_creates_training_pairs(tmp_path: Path):
    output_dir = tmp_path / "examples"
    generated = generate_dataset(output_dir, count=3, sr=16000, seed=11, clean=True)

    assert len(generated) == 3
    for example_dir in generated:
        original = example_dir / "original.wav"
        warped = example_dir / "warped.wav"
        meta = example_dir / "meta.json"

        assert original.exists()
        assert warped.exists()
        assert meta.exists()

        original_audio, original_sr = sf.read(str(original), always_2d=True)
        warped_audio, warped_sr = sf.read(str(warped), always_2d=True)
        payload = json.loads(meta.read_text(encoding="utf-8"))

        assert original_sr == 16000
        assert warped_sr == 16000
        assert original_audio.shape[1] == 2
        assert warped_audio.shape[1] == 2
        assert abs(len(original_audio) - len(warped_audio)) <= 2
        assert payload["bars"] >= 4
