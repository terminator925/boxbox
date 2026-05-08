from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from backend.ml.import_legacy_audio import _audio_files, _legacy_source_key, _select_clip_windows


def test_select_clip_windows_prefers_dense_regions_without_large_overlap():
    onset_times = np.array([1.0, 2.0, 3.0, 14.0, 15.0, 16.0, 30.0, 31.0, 32.0, 33.0], dtype=np.float32)
    windows = _select_clip_windows(onset_times, duration_sec=40.0, clip_duration=10.0, clips_per_file=2)

    assert len(windows) == 2
    starts = [window[0] for window in windows]
    assert any(start >= 24.0 for start in starts)
    assert max(starts) - min(starts) >= 3.5


def test_legacy_source_key_changes_with_clip_bounds(tmp_path: Path):
    audio_path = tmp_path / 'track.wav'
    sf.write(str(audio_path), np.zeros((22050, 1), dtype=np.float32), 22050)

    key_a = _legacy_source_key(audio_path, Path('folder/track.wav'), 0.0, 20.0)
    key_b = _legacy_source_key(audio_path, Path('folder/track.wav'), 10.0, 20.0)

    assert key_a != key_b
    assert key_a.startswith('legacy_audio::folder/track.wav::0.000')


def test_audio_files_filters_supported_extensions(tmp_path: Path):
    valid = tmp_path / 'a.wav'
    invalid = tmp_path / 'b.txt'
    nested = tmp_path / 'nested'
    nested.mkdir()
    valid.write_bytes(b'RIFF')
    invalid.write_text('x', encoding='utf-8')
    (nested / 'c.flac').write_bytes(b'fLaC')

    files = _audio_files(tmp_path)

    assert valid in files
    assert (nested / 'c.flac') in files
    assert invalid not in files



def test_import_legacy_audio_cleans_temp_dir_when_unused(tmp_path: Path):
    source_dir = tmp_path / 'src'
    output_dir = tmp_path / 'out'
    source_dir.mkdir()
    output_dir.mkdir()
    sf.write(str(source_dir / 'short.wav'), np.zeros((22050, 1), dtype=np.float32), 22050)

    from backend.ml.import_legacy_audio import import_legacy_audio

    created = import_legacy_audio(
        source_dir=source_dir,
        output_dir=output_dir,
        max_files=1,
        clips_per_file=1,
        min_duration=12.0,
        keep_temp=False,
        use_hybrid=False,
    )

    assert created == 0
    assert not (output_dir / '_legacy_import_tmp').exists()
