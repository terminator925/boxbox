from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from backend.app import _blend_ml_with_baseline, _optimize_groove_target, _rank_hybrid_candidates
from backend.audio.dtw_targets import onset_quantize_curve
from backend.audio.features import extract_features
from backend.audio.io_utils import convert_to_wav, load_audio, mixdown_mono
from backend.audio.onsets import detect_onsets
from backend.audio.tempo import estimate_tempo
from backend.audio.warp import apply_warp
from backend.ml.import_public_midi import _write_example
from backend.ml.infer import infer_curve_candidates, model_exists

AUDIO_EXTENSIONS = {'.wav', '.flac', '.aiff', '.aif', '.mp3', '.m4a', '.mp4', '.aac', '.ogg', '.opus', '.wma'}


def _audio_files(source_dir: Path) -> list[Path]:
    files = [path for path in source_dir.rglob('*') if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
    files.sort()
    return files


def _existing_legacy_keys(output_dir: Path, prefix: str) -> set[str]:
    keys: set[str] = set()
    for sub in output_dir.glob(f'{prefix}_*'):
        if not sub.is_dir():
            continue
        meta_path = sub / 'meta.json'
        if not meta_path.exists():
            continue
        try:
            payload = json.loads(meta_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        key = payload.get('source_member')
        if isinstance(key, str) and key:
            keys.add(key)
    return keys


def _next_index(output_dir: Path, prefix: str) -> int:
    max_idx = -1
    for sub in output_dir.glob(f'{prefix}_*'):
        if not sub.is_dir():
            continue
        suffix = sub.name.split('_')[-1]
        if suffix.isdigit():
            max_idx = max(max_idx, int(suffix))
    return max_idx + 1


def _legacy_source_key(audio_path: Path, relative_path: Path, start_sec: float, duration_sec: float) -> str:
    digest = hashlib.sha1(f'{audio_path.resolve()}|{audio_path.stat().st_mtime_ns}|{start_sec:.3f}|{duration_sec:.3f}'.encode('utf-8')).hexdigest()[:16]
    return f'legacy_audio::{relative_path.as_posix()}::{start_sec:.3f}::{duration_sec:.3f}::{digest}'


def _select_clip_windows(
    onset_times: np.ndarray,
    duration_sec: float,
    clip_duration: float,
    clips_per_file: int,
) -> list[tuple[float, float]]:
    duration_sec = float(max(duration_sec, 0.0))
    clip_duration = float(max(min(clip_duration, duration_sec), 4.0 if duration_sec >= 4.0 else duration_sec))
    if duration_sec <= clip_duration + 1e-6:
        return [(0.0, duration_sec)] if duration_sec > 0 else []

    step = max(clip_duration * 0.5, 3.0)
    starts = np.arange(0.0, max(duration_sec - clip_duration, 0.0) + 1e-6, step, dtype=np.float32)
    if float(starts[-1]) < duration_sec - clip_duration - 1e-6:
        starts = np.append(starts, np.float32(duration_sec - clip_duration))

    scored: list[tuple[float, float, float]] = []
    for start in starts:
        end = float(min(duration_sec, float(start) + clip_duration))
        count = int(np.count_nonzero((onset_times >= float(start)) & (onset_times < end)))
        density = count / max(end - float(start), 1e-6)
        scored.append((density, float(start), end))
    scored.sort(key=lambda item: (-item[0], item[1]))

    selected: list[tuple[float, float]] = []
    min_gap = clip_duration * 0.35
    for _, start, end in scored:
        if any(abs(start - existing_start) < min_gap for existing_start, _ in selected):
            continue
        selected.append((start, end))
        if len(selected) >= max(1, clips_per_file):
            break
    selected.sort(key=lambda item: item[0])
    return selected


def _clip_audio(audio: np.ndarray, sr: int, start_sec: float, end_sec: float) -> np.ndarray:
    start_sample = max(0, int(round(start_sec * sr)))
    end_sample = min(len(audio), int(round(end_sec * sr)))
    return audio[start_sample:end_sample].astype(np.float32, copy=False)


def _build_pseudo_target(
    mono: np.ndarray,
    sr: int,
    bpm: float,
    resolution: int,
    device: str,
    use_hybrid: bool,
) -> tuple[np.ndarray, np.ndarray, dict]:
    duration_sec = float(len(mono) / sr)
    base_curve = onset_quantize_curve(mono, sr, bpm, resolution)
    source_times = base_curve['source_times']
    grid = base_curve['grid_times']
    onsets_before = detect_onsets(mono, sr, units='time')
    baseline_target, baseline_groove_preserve, baseline_metrics = _optimize_groove_target(
        source_times,
        base_curve['target_times'],
        0,
        grid,
        onsets_before,
    )
    selection = {
        'target_times': baseline_target,
        'mode': 'baseline',
        'bpm': float(bpm),
        'resolution': int(resolution),
        'metrics': baseline_metrics,
        'groove_preserve': int(baseline_groove_preserve),
    }

    if not use_hybrid or not model_exists(Path('models')):
        return source_times, baseline_target, selection

    feat = extract_features(mono, sr)
    ml_curves = infer_curve_candidates(
        feat['mel'],
        feat['onset'],
        duration_sec,
        Path('models'),
        device=device,
        prefer_openvino=False,
        y_mono=mono,
        sr=sr,
        bpm=bpm,
        resolution=resolution,
    )
    if not ml_curves:
        return source_times, baseline_target, selection

    ml_curve = next((curve for curve in ml_curves if curve.get('candidate_name') == 'routed'), None)
    if ml_curve is None:
        ml_curve = max(ml_curves, key=lambda curve: float(curve.get('confidence', 0.0)))
    _, alpha, diagnostics = _blend_ml_with_baseline(
        source_times,
        base_curve['target_times'],
        ml_curve,
        grid,
    )
    ranked = _rank_hybrid_candidates(
        source_times,
        baseline_target,
        ml_curves,
        grid,
        onsets_before,
        alpha,
        top_k=3,
    )
    best_target = baseline_target
    best_metrics = baseline_metrics
    best_info: dict[str, object] = {'candidate_name': 'baseline', 'model_file': ''}
    best_alpha = 0.0
    best_preserve = baseline_groove_preserve
    for candidate_target, _, candidate_alpha, candidate_info in ranked:
        optimized_target, candidate_preserve, candidate_metrics = _optimize_groove_target(
            source_times,
            candidate_target,
            0,
            grid,
            onsets_before,
        )
        if float(candidate_metrics['avg_abs_error_after_sec']) + 1e-6 < float(best_metrics['avg_abs_error_after_sec']):
            best_target = optimized_target
            best_metrics = candidate_metrics
            best_info = candidate_info
            best_alpha = float(candidate_alpha)
            best_preserve = int(candidate_preserve)
    selection = {
        'target_times': best_target,
        'mode': 'hybrid' if best_info.get('candidate_name') != 'baseline' else 'baseline',
        'bpm': float(bpm),
        'resolution': int(resolution),
        'metrics': best_metrics,
        'groove_preserve': int(best_preserve),
        'hybrid_alpha': float(best_alpha),
        'candidate_name': str(best_info.get('candidate_name', 'baseline')),
        'model_file': str(best_info.get('model_file', '')),
        'accelerator': str(best_info.get('accelerator', 'torch')),
        'diagnostics': diagnostics,
    }
    return source_times, best_target, selection


def import_legacy_audio(
    source_dir: Path,
    output_dir: Path,
    prefix: str = 'legacy_audio',
    max_files: int = 0,
    clips_per_file: int = 2,
    clip_duration: float = 30.0,
    min_duration: float = 12.0,
    min_onsets: int = 24,
    resolution: int = 8,
    device: str = 'cuda',
    use_hybrid: bool = True,
    target_sr: int = 22050,
    keep_temp: bool = False,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = output_dir / '_legacy_import_tmp'
    temp_dir.mkdir(parents=True, exist_ok=True)

    files = _audio_files(source_dir)
    if max_files > 0:
        files = files[:max_files]

    created = 0
    next_idx = _next_index(output_dir, prefix)
    existing_keys = _existing_legacy_keys(output_dir, prefix)

    for audio_path in files:
        relative_path = audio_path.relative_to(source_dir)
        temp_wav = temp_dir / f'{audio_path.stem}_{abs(hash(str(relative_path))) & 0xFFFFFFFF:x}.wav'
        try:
            convert_to_wav(audio_path, temp_wav, target_sr=target_sr)
            audio, sr = load_audio(temp_wav)
        except Exception:
            if temp_wav.exists() and not keep_temp:
                temp_wav.unlink(missing_ok=True)
            continue
        duration_sec = float(len(audio) / sr)
        if duration_sec < float(min_duration):
            if temp_wav.exists() and not keep_temp:
                temp_wav.unlink(missing_ok=True)
            continue
        mono = mixdown_mono(audio)
        onset_times = detect_onsets(mono, sr, units='time')
        if len(onset_times) < int(min_onsets):
            if temp_wav.exists() and not keep_temp:
                temp_wav.unlink(missing_ok=True)
            continue

        for start_sec, end_sec in _select_clip_windows(onset_times, duration_sec, clip_duration, clips_per_file):
            clip_len = end_sec - start_sec
            source_key = _legacy_source_key(audio_path, relative_path, start_sec, clip_len)
            if source_key in existing_keys:
                continue
            clip_audio = _clip_audio(audio, sr, start_sec, end_sec)
            if len(clip_audio) < int(round(min_duration * sr)):
                continue
            clip_mono = mixdown_mono(clip_audio)
            clip_onsets = detect_onsets(clip_mono, sr, units='time')
            if len(clip_onsets) < int(min_onsets):
                continue
            bpm = float(np.clip(estimate_tempo(clip_mono, sr), 50.0, 220.0))
            try:
                source_times, target_times, selection = _build_pseudo_target(
                    clip_mono,
                    sr,
                    bpm,
                    resolution,
                    device=device,
                    use_hybrid=use_hybrid,
                )
                warped_audio, warp_method = apply_warp(clip_audio, sr, source_times, target_times)
            except Exception:
                continue

            example_dir = output_dir / f'{prefix}_{next_idx:04d}'
            meta = {
                'dataset': prefix,
                'source_member': source_key,
                'source_path': relative_path.as_posix(),
                'clip_start_sec': float(start_sec),
                'clip_duration_sec': float(len(clip_audio) / sr),
                'bpm': float(selection['bpm']),
                'resolution': int(selection['resolution']),
                'pseudo_mode': str(selection['mode']),
                'event_count': int(len(clip_onsets)),
                'warp_method': warp_method,
                'effective_groove_preserve': int(selection.get('groove_preserve', 0)),
                'hybrid_alpha': float(selection.get('hybrid_alpha', 0.0)),
                'candidate_name': str(selection.get('candidate_name', 'baseline')),
                'model_file': str(selection.get('model_file', '')),
                'accelerator': str(selection.get('accelerator', 'torch')),
                'timing_metrics': selection.get('metrics', {}),
                'diagnostics': selection.get('diagnostics', {}),
            }
            _write_example(example_dir, clip_audio, warped_audio, sr, meta)
            existing_keys.add(source_key)
            next_idx += 1
            created += 1
        if temp_wav.exists() and not keep_temp:
            temp_wav.unlink(missing_ok=True)
    if not keep_temp:
        try:
            temp_dir.rmdir()
        except OSError:
            pass
    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/examples'))
    parser.add_argument('--prefix', type=str, default='legacy_audio')
    parser.add_argument('--max-files', type=int, default=0)
    parser.add_argument('--clips-per-file', type=int, default=2)
    parser.add_argument('--clip-duration', type=float, default=30.0)
    parser.add_argument('--min-duration', type=float, default=12.0)
    parser.add_argument('--min-onsets', type=int, default=24)
    parser.add_argument('--resolution', type=int, default=8)
    parser.add_argument('--device', type=str, default='cuda')
    parser.add_argument('--target-sr', type=int, default=22050)
    parser.add_argument('--baseline-only', action='store_true')
    parser.add_argument('--keep-temp', action='store_true')
    args = parser.parse_args()

    created = import_legacy_audio(
        source_dir=args.source,
        output_dir=args.output,
        prefix=args.prefix,
        max_files=args.max_files,
        clips_per_file=args.clips_per_file,
        clip_duration=args.clip_duration,
        min_duration=args.min_duration,
        min_onsets=args.min_onsets,
        resolution=args.resolution,
        device=args.device,
        use_hybrid=not args.baseline_only,
        target_sr=args.target_sr,
        keep_temp=args.keep_temp,
    )
    print(f'created_examples={created}')


if __name__ == '__main__':
    main()





