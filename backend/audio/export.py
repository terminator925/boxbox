from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.audio.io_utils import export_quantized_audio, write_audio


def export_quantized_wav(path: Path, audio: np.ndarray, sr: int, source_meta: dict | None = None) -> dict[str, str]:
    return export_quantized_audio(path.parent, audio, sr, source_meta=source_meta)


def export_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)


def _click_track(duration_samples: int, sr: int, bpm: float, channels: int, beats_per_bar: int = 4) -> np.ndarray:
    beat_step = 60.0 / max(float(bpm), 1e-6)
    click_len = max(16, int(round(0.022 * sr)))
    tail = np.linspace(0.0, 1.0, click_len, endpoint=False, dtype=np.float32)
    env = np.exp(-70.0 * tail).astype(np.float32)
    click = np.sin(2 * np.pi * 1800.0 * tail).astype(np.float32) * env
    accent = np.sin(2 * np.pi * 2750.0 * tail).astype(np.float32) * env
    track = np.zeros((duration_samples, channels), dtype=np.float32)
    beat_count = int(np.ceil((duration_samples / sr) / beat_step)) + 1
    for beat in range(beat_count):
        start = int(round(beat * beat_step * sr))
        if start >= duration_samples:
            break
        end = min(duration_samples, start + click_len)
        pulse = accent if beat % max(1, beats_per_bar) == 0 else click
        amp = 0.42 if beat % max(1, beats_per_bar) == 0 else 0.3
        track[start:end, :] += (amp * pulse[: end - start])[:, None]
    return np.clip(track, -1.0, 1.0)


def export_metronome_check_wav(path: Path, audio: np.ndarray, sr: int, bpm: float, beats_per_bar: int = 4) -> dict[str, object]:
    """Export a quick DAW-lock listening check: quantized audio plus a fixed-grid click."""
    if audio.ndim == 1:
        audio = audio[:, None]
    channels = int(audio.shape[1])
    duration_sec = float(audio.shape[0] / sr) if sr > 0 else 0.0
    click = _click_track(int(audio.shape[0]), sr, bpm, channels, beats_per_bar=beats_per_bar)
    bed = np.asarray(audio, dtype=np.float32)
    peak = float(np.max(np.abs(bed))) if bed.size else 0.0
    if peak > 0.92:
        bed = bed * (0.92 / peak)
    mix = np.clip((bed * 0.82) + click, -1.0, 1.0)
    write_audio(path, mix, sr, subtype="PCM_16")
    return {
        "filename": path.name,
        "generated": True,
        "target_bpm": float(bpm),
        "beats_per_bar": int(beats_per_bar),
        "sample_rate": int(sr),
        "channels": channels,
        "duration_sec": duration_sec,
        "audio_gain": 0.82,
        "click": "fixed_grid_accented_downbeat",
        "purpose": "Listen against the exported fixed-tempo grid without setting up a DAW metronome.",
    }


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def _meta_event(delta: int, meta_type: int, payload: bytes) -> bytes:
    return _vlq(delta) + bytes([0xFF, meta_type]) + _vlq(len(payload)) + payload


def _mtrk(chunk: bytes) -> bytes:
    return b"MTrk" + len(chunk).to_bytes(4, "big") + chunk


def export_tempo_midi(path: Path, bpm: float, duration_sec: float) -> None:
    ticks_per_beat = 480
    header = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + (2).to_bytes(2, "big") + ticks_per_beat.to_bytes(2, "big")

    tempo_us_per_beat = int(round(60_000_000 / max(bpm, 1e-6)))
    tempo_bytes = tempo_us_per_beat.to_bytes(3, "big", signed=False)
    tempo_track = _meta_event(0, 0x51, tempo_bytes) + _meta_event(0, 0x2F, b"")

    beats = int(np.ceil(duration_sec * bpm / 60.0))
    marker_track = bytearray()
    marker_track += _meta_event(0, 0x06, b"beat_0")
    for beat in range(1, beats + 1):
        marker_track += _meta_event(ticks_per_beat, 0x06, f"beat_{beat}".encode("ascii"))
    marker_track += _meta_event(0, 0x2F, b"")

    midi = header + _mtrk(tempo_track) + _mtrk(bytes(marker_track))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(midi)
