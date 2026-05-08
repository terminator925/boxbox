from __future__ import annotations

import argparse
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass
class NoteEvent:
    onset: float
    duration: float
    midi: int
    velocity: float


def midi_to_hz(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _adsr(length: int, attack: float, decay: float, sustain: float, release: float) -> np.ndarray:
    length = max(1, length)
    attack_n = min(length, max(1, int(length * attack)))
    decay_n = min(length - attack_n, max(1, int(length * decay)))
    release_n = min(length - attack_n - decay_n, max(1, int(length * release)))
    sustain_n = max(0, length - attack_n - decay_n - release_n)

    env = np.empty(length, dtype=np.float32)
    pos = 0
    env[pos : pos + attack_n] = np.linspace(0.0, 1.0, attack_n, endpoint=False, dtype=np.float32)
    pos += attack_n
    env[pos : pos + decay_n] = np.linspace(1.0, sustain, decay_n, endpoint=False, dtype=np.float32)
    pos += decay_n
    if sustain_n:
        env[pos : pos + sustain_n] = sustain
        pos += sustain_n
    env[pos : pos + release_n] = np.linspace(sustain, 0.0, release_n, endpoint=False, dtype=np.float32)
    pos += release_n
    if pos < length:
        env[pos:] = 0.0
    return env


def _pan_stereo(signal: np.ndarray, pan: float) -> np.ndarray:
    angle = (pan + 1.0) * (np.pi / 4.0)
    left = np.cos(angle) * signal
    right = np.sin(angle) * signal
    return np.stack([left, right], axis=1)


def _colored_noise(rng: np.random.Generator, length: int, tilt: float) -> np.ndarray:
    white = rng.standard_normal(length).astype(np.float32)
    spec = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(length, d=1.0)
    shaping = 1.0 / np.maximum(freqs, 1.0 / max(length, 1)) ** tilt
    shaping[0] = 0.0
    shaped = np.fft.irfft(spec * shaping, n=length)
    peak = np.max(np.abs(shaped)) + 1e-8
    return (shaped / peak).astype(np.float32)


def synth_tone(kind: str, freq: float, duration: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    n = max(1, int(round(duration * sr)))
    t = np.arange(n, dtype=np.float32) / sr

    if kind == "bass":
        signal = (
            0.72 * np.sin(2 * np.pi * freq * t)
            + 0.18 * np.sin(2 * np.pi * freq * 2.0 * t)
            + 0.10 * np.sin(2 * np.pi * freq * 0.5 * t)
        )
        env = _adsr(n, 0.02, 0.15, 0.70, 0.20)
    elif kind == "pad":
        signal = (
            0.45 * np.sin(2 * np.pi * freq * t)
            + 0.25 * np.sin(2 * np.pi * freq * 2.0 * t + 0.2)
            + 0.18 * np.sin(2 * np.pi * freq * 3.0 * t + 0.5)
            + 0.12 * np.sin(2 * np.pi * freq * 4.0 * t + 1.2)
        )
        signal += 0.04 * _colored_noise(rng, n, 0.8)
        env = _adsr(n, 0.10, 0.18, 0.82, 0.20)
    elif kind == "pluck":
        signal = (
            0.60 * np.sin(2 * np.pi * freq * t)
            + 0.25 * np.sin(2 * np.pi * freq * 2.0 * t + 0.1)
            + 0.14 * np.sin(2 * np.pi * freq * 3.0 * t + 0.4)
        )
        env = _adsr(n, 0.005, 0.10, 0.35, 0.25)
    elif kind == "piano":
        signal = (
            0.58 * np.sin(2 * np.pi * freq * t)
            + 0.22 * np.sin(2 * np.pi * freq * 2.0 * t + 0.05)
            + 0.11 * np.sin(2 * np.pi * freq * 3.0 * t + 0.14)
            + 0.06 * np.sin(2 * np.pi * freq * 4.0 * t + 0.32)
        )
        hammer = 0.025 * _colored_noise(rng, n, 0.0) * np.exp(-120.0 * t)
        signal = signal + hammer
        env = _adsr(n, 0.003, 0.16, 0.42, 0.32)
    elif kind == "bell":
        signal = (
            0.65 * np.sin(2 * np.pi * freq * t)
            + 0.28 * np.sin(2 * np.pi * freq * 2.71 * t)
            + 0.16 * np.sin(2 * np.pi * freq * 4.07 * t)
        )
        env = _adsr(n, 0.004, 0.22, 0.18, 0.40)
    else:
        signal = (
            0.60 * np.sin(2 * np.pi * freq * t)
            + 0.20 * np.sin(2 * np.pi * freq * 2.0 * t + 0.3)
            + 0.10 * np.sin(2 * np.pi * freq * 3.0 * t + 0.7)
        )
        env = _adsr(n, 0.01, 0.12, 0.50, 0.22)

    lfo = 1.0 + 0.005 * np.sin(2 * np.pi * (4.0 + rng.uniform(-1.0, 1.0)) * t)
    return (signal * env * lfo).astype(np.float32)


def synth_drum(kind: str, duration: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    n = max(1, int(round(duration * sr)))
    t = np.arange(n, dtype=np.float32) / sr

    if kind == "kick":
        base = np.sin(2 * np.pi * (90.0 * np.exp(-7.0 * t) + 32.0) * t)
        env = np.exp(-10.0 * t)
        click = 0.12 * _colored_noise(rng, n, 0.0) * np.exp(-80.0 * t)
        out = 0.9 * base * env + click
    elif kind == "snare":
        noise = _colored_noise(rng, n, 0.25)
        tone = np.sin(2 * np.pi * 185.0 * t)
        env = np.exp(-12.0 * t)
        out = 0.72 * noise * env + 0.18 * tone * np.exp(-20.0 * t)
    else:
        noise = _colored_noise(rng, n, -0.1)
        env = np.exp(-24.0 * t)
        out = noise * env
    return out.astype(np.float32)


def add_clip(mix: np.ndarray, clip: np.ndarray, onset: float, pan: float, gain: float, sr: int) -> None:
    start = int(round(onset * sr))
    if start >= len(mix):
        return
    stereo = _pan_stereo(clip * gain, pan)
    end = min(len(mix), start + len(stereo))
    if end <= start:
        return
    mix[start:end] += stereo[: end - start]


def chord_pool(root: int) -> list[list[int]]:
    return [
        [root, root + 4, root + 7],
        [root, root + 3, root + 7],
        [root, root + 5, root + 9],
        [root, root + 7, root + 11],
    ]


def build_harmony_track(
    bars: int,
    bpm: float,
    step: float,
    rng: np.random.Generator,
) -> list[NoteEvent]:
    roots = rng.choice([48, 50, 52, 53, 55, 57, 59], size=bars, replace=True)
    chords: list[NoteEvent] = []
    for bar_idx in range(bars):
        onset = bar_idx * 4 * step
        dur = 4 * step * rng.choice([0.9, 1.0, 1.2])
        for midi in chord_pool(int(roots[bar_idx]))[bar_idx % 4]:
            chords.append(NoteEvent(onset=onset, duration=dur, midi=midi, velocity=float(rng.uniform(0.45, 0.8))))
    return chords


def build_lead_track(
    bars: int,
    step: float,
    rng: np.random.Generator,
) -> list[NoteEvent]:
    scale = np.array([60, 62, 64, 65, 67, 69, 71, 72])
    events: list[NoteEvent] = []
    for bar_idx in range(bars):
        for slot in range(8):
            if rng.random() < 0.22:
                continue
            onset = (bar_idx * 8 + slot) * (step / 2.0)
            midi = int(rng.choice(scale) + rng.choice([0, 0, 12]))
            dur = step * rng.choice([0.45, 0.65, 0.9, 1.2])
            vel = float(rng.uniform(0.30, 0.75))
            events.append(NoteEvent(onset=onset, duration=dur, midi=midi, velocity=vel))
    return events


def build_bass_track(
    bars: int,
    step: float,
    rng: np.random.Generator,
) -> list[NoteEvent]:
    roots = [36, 38, 41, 43, 45, 48]
    events: list[NoteEvent] = []
    for bar_idx in range(bars):
        root = int(rng.choice(roots))
        for beat in range(4):
            onset = (bar_idx * 4 + beat) * step
            midi = root + (12 if rng.random() < 0.12 else 0)
            dur = step * rng.choice([0.7, 0.9, 1.0, 1.3])
            vel = float(rng.uniform(0.45, 0.85))
            events.append(NoteEvent(onset=onset, duration=dur, midi=midi, velocity=vel))
            if rng.random() < 0.35:
                ghost_onset = onset + step * 0.5
                events.append(NoteEvent(onset=ghost_onset, duration=step * 0.45, midi=midi + 5, velocity=vel * 0.6))
    return events


def build_drum_track(
    bars: int,
    step: float,
    rng: np.random.Generator,
) -> dict[str, list[NoteEvent]]:
    kick: list[NoteEvent] = []
    snare: list[NoteEvent] = []
    hat: list[NoteEvent] = []
    for bar_idx in range(bars):
        for beat in range(4):
            beat_time = (bar_idx * 4 + beat) * step
            if beat in {0, 2} or rng.random() < 0.3:
                kick.append(NoteEvent(beat_time, step * 0.5, 36, float(rng.uniform(0.7, 1.0))))
            if beat in {1, 3}:
                snare.append(NoteEvent(beat_time, step * 0.35, 38, float(rng.uniform(0.55, 0.9))))
        for slot in range(8):
            onset = bar_idx * 4 * step + slot * (step / 2.0)
            if rng.random() < 0.1:
                continue
            hat.append(NoteEvent(onset, step * 0.2, 42, float(rng.uniform(0.18, 0.45))))
    return {"kick": kick, "snare": snare, "hat": hat}


def humanize_events(
    events: list[NoteEvent],
    step: float,
    rng: np.random.Generator,
    amount: float,
    duration_sec: float,
) -> list[NoteEvent]:
    drift_depth = amount * step * 0.35
    jitter_depth = amount * step * 0.22
    drift_rate = rng.uniform(0.03, 0.10)
    out: list[NoteEvent] = []
    for event in events:
        drift = drift_depth * math.sin(2 * math.pi * drift_rate * event.onset + rng.uniform(0.0, np.pi))
        jitter = rng.normal(0.0, jitter_depth)
        onset = float(np.clip(event.onset + drift + jitter, 0.0, max(0.0, duration_sec - 0.02)))
        duration = float(np.clip(event.duration * rng.uniform(0.82, 1.18), 0.05, duration_sec - onset))
        velocity = float(np.clip(event.velocity * rng.uniform(0.72, 1.22), 0.05, 1.0))
        out.append(NoteEvent(onset=onset, duration=duration, midi=event.midi, velocity=velocity))
    out.sort(key=lambda item: item.onset)
    return out


def render_events(
    duration_sec: float,
    sr: int,
    rng: np.random.Generator,
    instrument_kind: str,
    events: list[NoteEvent],
    pan: float,
    base_gain: float,
) -> np.ndarray:
    mix = np.zeros((int(round(duration_sec * sr)), 2), dtype=np.float32)
    for event in events:
        clip = synth_tone(instrument_kind, midi_to_hz(event.midi), event.duration, sr, rng)
        add_clip(mix, clip, event.onset, pan, base_gain * event.velocity, sr)
    return mix


def render_drums(
    duration_sec: float,
    sr: int,
    rng: np.random.Generator,
    drums: dict[str, list[NoteEvent]],
) -> np.ndarray:
    mix = np.zeros((int(round(duration_sec * sr)), 2), dtype=np.float32)
    configs = {
        "kick": (-0.05, 0.95),
        "snare": (0.0, 0.68),
        "hat": (0.45, 0.32),
    }
    for kind, events in drums.items():
        pan, gain = configs[kind]
        for event in events:
            clip = synth_drum(kind, event.duration, sr, rng)
            add_clip(mix, clip, event.onset, pan, gain * event.velocity, sr)
    return mix


def finalize_mix(mix: np.ndarray) -> np.ndarray:
    peak = float(np.max(np.abs(mix)) + 1e-8)
    if peak > 0.95:
        mix = mix * (0.95 / peak)
    return mix.astype(np.float32)


def example_blueprint(index: int, seed: int, sr: int) -> tuple[np.ndarray, np.ndarray, dict]:
    rng = np.random.default_rng(seed + index)
    bpm = float(rng.uniform(82.0, 138.0))
    resolution = int(rng.choice([4, 8, 8, 16]))
    step = (60.0 / bpm) * (4.0 / resolution)
    bars = int(rng.integers(4, 9))
    duration_sec = bars * 4 * (60.0 / bpm) + 0.8

    harmony_quant = build_harmony_track(bars, bpm, 60.0 / bpm, rng)
    lead_quant = build_lead_track(bars, 60.0 / bpm, rng)
    bass_quant = build_bass_track(bars, 60.0 / bpm, rng)
    drums_quant = build_drum_track(bars, 60.0 / bpm, rng)

    human_amount = float(rng.uniform(0.45, 1.0))
    harmony_unquant = humanize_events(harmony_quant, step, rng, human_amount * 0.85, duration_sec)
    lead_unquant = humanize_events(lead_quant, step, rng, human_amount, duration_sec)
    bass_unquant = humanize_events(bass_quant, step, rng, human_amount * 0.75, duration_sec)
    drums_unquant = {name: humanize_events(items, step, rng, human_amount * 1.15, duration_sec) for name, items in drums_quant.items()}

    harmony_kind = str(rng.choice(["pad", "pluck"]))
    lead_kind = str(rng.choice(["lead", "bell", "pluck"]))
    bass_kind = "bass"

    quant_mix = np.zeros((int(round(duration_sec * sr)), 2), dtype=np.float32)
    quant_mix += render_events(duration_sec, sr, rng, harmony_kind, harmony_quant, pan=-0.3, base_gain=0.28)
    quant_mix += render_events(duration_sec, sr, rng, lead_kind, lead_quant, pan=0.18, base_gain=0.22)
    quant_mix += render_events(duration_sec, sr, rng, bass_kind, bass_quant, pan=-0.08, base_gain=0.34)
    quant_mix += render_drums(duration_sec, sr, rng, drums_quant)

    unquant_mix = np.zeros_like(quant_mix)
    unquant_mix += render_events(duration_sec, sr, rng, harmony_kind, harmony_unquant, pan=-0.3, base_gain=0.28)
    unquant_mix += render_events(duration_sec, sr, rng, lead_kind, lead_unquant, pan=0.18, base_gain=0.22)
    unquant_mix += render_events(duration_sec, sr, rng, bass_kind, bass_unquant, pan=-0.08, base_gain=0.34)
    unquant_mix += render_drums(duration_sec, sr, rng, drums_unquant)

    metadata = {
        "seed": int(seed + index),
        "bpm": bpm,
        "resolution": resolution,
        "bars": bars,
        "duration_sec": duration_sec,
        "harmony_kind": harmony_kind,
        "lead_kind": lead_kind,
        "humanize_amount": human_amount,
    }
    return finalize_mix(unquant_mix), finalize_mix(quant_mix), metadata


def generate_dataset(output_dir: Path, count: int, sr: int = 22050, seed: int = 7, clean: bool = False) -> list[Path]:
    if clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    start_idx = 0
    for sub in output_dir.glob("synthetic_*"):
        if not sub.is_dir():
            continue
        suffix = sub.name.split("_")[-1]
        if suffix.isdigit():
            start_idx = max(start_idx, int(suffix) + 1)

    generated: list[Path] = []
    for idx in range(count):
        example_dir = output_dir / f"synthetic_{start_idx + idx:04d}"
        example_dir.mkdir(parents=True, exist_ok=True)
        original, warped, metadata = example_blueprint(idx, seed, sr)
        sf.write(str(example_dir / "original.wav"), original, sr, subtype="PCM_24")
        sf.write(str(example_dir / "warped.wav"), warped, sr, subtype="PCM_24")
        (example_dir / "meta.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        generated.append(example_dir)
    return generated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("data/examples"))
    parser.add_argument("--count", type=int, default=64)
    parser.add_argument("--sr", type=int, default=22050)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    generated = generate_dataset(args.output, args.count, args.sr, args.seed, args.clean)
    print(f"generated_examples={len(generated)}")
    print(f"output_dir={args.output}")


if __name__ == "__main__":
    main()
