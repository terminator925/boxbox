from __future__ import annotations

import argparse
import csv
import io
import json
import random
import tarfile
import zipfile
from pathlib import Path

import librosa
import mido
import numpy as np
import soundfile as sf

from backend.audio.warp import apply_warp
from backend.ml.synthetic_data import NoteEvent, add_clip, finalize_mix, midi_to_hz, synth_drum, synth_tone


GM_DRUM_MAP = {
    35: "kick",
    36: "kick",
    37: "snare",
    38: "snare",
    39: "snare",
    40: "snare",
    41: "tom",
    42: "hat",
    43: "tom",
    44: "hat",
    45: "tom",
    46: "hat",
    47: "tom",
    48: "tom",
    49: "hat",
    50: "tom",
    51: "hat",
    52: "hat",
    53: "hat",
    54: "hat",
    55: "hat",
    57: "hat",
    59: "hat",
}


def _iter_note_events(midi_bytes: bytes) -> tuple[list[NoteEvent], float]:
    midi = mido.MidiFile(file=io.BytesIO(midi_bytes))
    tempo = 500000
    seconds = 0.0
    active: dict[tuple[int, int], tuple[float, int]] = {}
    events: list[NoteEvent] = []
    tempos: list[float] = []

    for msg in mido.merge_tracks(midi.tracks):
        seconds += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
        if msg.type == "set_tempo":
            tempo = msg.tempo
            tempos.append(float(mido.tempo2bpm(tempo)))
            continue
        if msg.type == "note_on" and msg.velocity > 0:
            active[(msg.channel, msg.note)] = (seconds, msg.velocity)
            continue
        if msg.type in {"note_off", "note_on"}:
            key = (msg.channel, msg.note)
            if key not in active:
                continue
            onset, velocity = active.pop(key)
            duration = max(0.04, seconds - onset)
            events.append(NoteEvent(onset=onset, duration=duration, midi=msg.note, velocity=float(velocity / 127.0)))

    bpm = float(np.median(tempos)) if tempos else 120.0
    events.sort(key=lambda item: item.onset)
    return events, bpm


def _crop_events(events: list[NoteEvent], max_duration: float, rng: random.Random) -> tuple[list[NoteEvent], float]:
    start, end, output_duration = _choose_crop_window(events, max_duration, rng)
    return _crop_events_to_window(events, start, end), output_duration


def _choose_crop_window(events: list[NoteEvent], max_duration: float, rng: random.Random) -> tuple[float, float, float]:
    if not events:
        return 0.0, max_duration, max_duration
    total = max(event.onset + event.duration for event in events)
    if total <= max_duration:
        return 0.0, total, total + 0.25

    note_onsets = [event.onset for event in events if event.onset < total - max_duration]
    start = rng.choice(note_onsets) if note_onsets else 0.0
    end = start + max_duration
    return float(start), float(end), max_duration + 0.25


def _crop_events_to_window(events: list[NoteEvent], start: float, end: float) -> list[NoteEvent]:
    cropped: list[NoteEvent] = []
    for event in events:
        if event.onset >= end or event.onset + event.duration <= start:
            continue
        onset = max(0.0, event.onset - start)
        duration = min(end, event.onset + event.duration) - max(start, event.onset)
        cropped.append(NoteEvent(onset=onset, duration=max(0.04, duration), midi=event.midi, velocity=event.velocity))
    return cropped


def _quantize_events(events: list[NoteEvent], bpm: float, subdivision: int) -> list[NoteEvent]:
    step = (60.0 / max(bpm, 1e-6)) * (4.0 / subdivision)
    quantized: list[NoteEvent] = []
    for event in events:
        onset = round(event.onset / step) * step
        end = round((event.onset + event.duration) / step) * step
        if end <= onset:
            end = onset + max(step * 0.5, 0.04)
        quantized.append(NoteEvent(onset=float(max(0.0, onset)), duration=float(end - onset), midi=event.midi, velocity=event.velocity))
    quantized.sort(key=lambda item: item.onset)
    return quantized


def _render_piano(events: list[NoteEvent], duration_sec: float, sr: int) -> np.ndarray:
    rng = np.random.default_rng(17)
    mix = np.zeros((int(round(duration_sec * sr)), 2), dtype=np.float32)
    for event in events:
        pan = float(np.clip((event.midi - 60) / 36.0, -0.6, 0.6))
        clip = synth_tone("piano", midi_to_hz(event.midi), event.duration, sr, rng)
        add_clip(mix, clip, event.onset, pan, 0.26 * event.velocity, sr)
    return finalize_mix(mix)


def _render_drums(events: list[NoteEvent], duration_sec: float, sr: int) -> np.ndarray:
    rng = np.random.default_rng(23)
    mix = np.zeros((int(round(duration_sec * sr)), 2), dtype=np.float32)
    pan_map = {"kick": -0.05, "snare": 0.0, "hat": 0.42, "tom": 0.18}
    gain_map = {"kick": 0.92, "snare": 0.70, "hat": 0.28, "tom": 0.48}
    drum_duration = {"kick": 0.35, "snare": 0.24, "hat": 0.10, "tom": 0.22}
    for event in events:
        kind = GM_DRUM_MAP.get(event.midi, "hat")
        synth_kind = kind if kind in {"kick", "snare", "hat"} else "snare"
        clip = synth_drum(synth_kind, drum_duration[kind], sr, rng)
        add_clip(mix, clip, event.onset, pan_map[kind], gain_map[kind] * event.velocity, sr)
    return finalize_mix(mix)


def _write_example(example_dir: Path, original: np.ndarray, warped: np.ndarray, sr: int, meta: dict) -> None:
    example_dir.mkdir(parents=True, exist_ok=True)
    sf.write(str(example_dir / "original.wav"), original, sr, subtype="PCM_24")
    sf.write(str(example_dir / "warped.wav"), warped, sr, subtype="PCM_24")
    (example_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _next_index(output_dir: Path, prefix: str) -> int:
    max_idx = -1
    for sub in output_dir.glob(f"{prefix}_*"):
        if not sub.is_dir():
            continue
        suffix = sub.name.split("_")[-1]
        if suffix.isdigit():
            max_idx = max(max_idx, int(suffix))
    return max_idx + 1


def _existing_keys(output_dir: Path) -> set[str]:
    keys: set[str] = set()
    for sub in output_dir.iterdir():
        if not sub.is_dir():
            continue
        meta_path = sub / "meta.json"
        if not meta_path.exists():
            continue
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = payload.get("source_member") or payload.get("performance_member")
        if isinstance(key, str) and key:
            keys.add(key)
    return keys


def _maestro_members(zip_file: zipfile.ZipFile) -> list[str]:
    return [name for name in zip_file.namelist() if name.endswith(".midi")]


def _groove_members(zip_file: zipfile.ZipFile) -> list[str]:
    return [name for name in zip_file.namelist() if name.endswith(".mid") or name.endswith(".midi")]


def _read_csv_rows(zip_file: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    text = zip_file.read(member).decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _maestro_rows(zip_file: zipfile.ZipFile) -> list[dict[str, str]]:
    for candidate in ("maestro-v3.0.0/maestro-v3.0.0.csv", "maestro-v3.0.0.csv"):
        if candidate in zip_file.namelist():
            return _read_csv_rows(zip_file, candidate)
    return []


def _read_zip_audio(audio_bytes: bytes, target_sr: int) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(io.BytesIO(audio_bytes), always_2d=True, dtype="float32")
    if sr != target_sr:
        resampled = []
        for ch in range(audio.shape[1]):
            resampled.append(librosa.resample(audio[:, ch], orig_sr=sr, target_sr=target_sr).astype(np.float32))
        max_len = max(len(ch) for ch in resampled)
        padded = [np.pad(ch, (0, max_len - len(ch))) for ch in resampled]
        audio = np.stack(padded, axis=1).astype(np.float32)
        sr = target_sr
    return audio.astype(np.float32), sr


def _read_tar_audio(audio_bytes: bytes, target_sr: int) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(io.BytesIO(audio_bytes), always_2d=True, dtype="float32")
    if sr != target_sr:
        resampled = []
        for ch in range(audio.shape[1]):
            resampled.append(librosa.resample(audio[:, ch], orig_sr=sr, target_sr=target_sr).astype(np.float32))
        max_len = max(len(ch) for ch in resampled)
        padded = [np.pad(ch, (0, max_len - len(ch))) for ch in resampled]
        audio = np.stack(padded, axis=1).astype(np.float32)
        sr = target_sr
    return audio.astype(np.float32), sr


def _musicnet_entries(archive: tarfile.TarFile) -> list[tuple[str, str]]:
    wav_members: dict[str, str] = {}
    csv_members: dict[str, str] = {}
    for member in archive.getmembers():
        if not member.isfile():
            continue
        path = member.name
        suffix = Path(path).suffix.lower()
        stem = Path(path).stem
        if suffix == ".wav":
            wav_members[stem] = path
        elif suffix == ".csv":
            csv_members[stem] = path
    return sorted((wav_members[key], csv_members[key]) for key in wav_members.keys() & csv_members.keys())


def _musicnet_metadata(downloads_dir: Path) -> dict[str, dict[str, str]]:
    metadata_path = downloads_dir / "musicnet_metadata.csv"
    if not metadata_path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    with metadata_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            item_id = str(row.get("id") or "").strip()
            if item_id:
                rows[item_id] = row
    return rows


def _musicnet_priority(row: dict[str, str] | None) -> tuple[int, int, int, str]:
    if not row:
        return (1, 1, 1, "")
    ensemble = str(row.get("ensemble") or "").strip().lower()
    composer = str(row.get("composer") or "").strip().lower()
    source = str(row.get("source") or "").strip().lower()
    solo_penalty = 1 if "solo piano" in ensemble else 0
    small_group_bonus = 0 if any(token in ensemble for token in ("trio", "quartet", "quintet", "sextet", "octet", "ensemble", "clarinet", "violin", "cello", "string")) else 1
    source_bonus = 0 if any(token in source for token in ("archive", "museopen", "albright")) else 1
    return (solo_penalty, small_group_bonus, source_bonus, f"{composer}|{ensemble}")


def _musicnet_note_events(label_csv: str, sr: int) -> tuple[list[NoteEvent], float]:
    reader = csv.DictReader(io.StringIO(label_csv))
    events: list[NoteEvent] = []
    onset_times: list[float] = []
    for row in reader:
        try:
            start_sample = float(row.get("start_time", ""))
            end_sample = float(row.get("end_time", ""))
            midi = int(float(row.get("note", "")))
        except Exception:
            continue
        onset = max(0.0, start_sample / max(sr, 1))
        duration = max(0.04, (end_sample - start_sample) / max(sr, 1))
        events.append(NoteEvent(onset=onset, duration=duration, midi=midi, velocity=0.8))
        onset_times.append(onset)
    events.sort(key=lambda item: item.onset)
    if len(onset_times) < 4:
        return events, 120.0
    intervals = np.diff(np.asarray(onset_times, dtype=np.float32))
    intervals = intervals[(intervals > 0.08) & (intervals < 1.5)]
    if len(intervals) == 0:
        return events, 120.0
    bpm = float(np.clip(60.0 / float(np.median(intervals)), 40.0, 220.0))
    return events, bpm


def _alignment_curve_from_events(
    source_events: list[NoteEvent],
    target_events: list[NoteEvent],
    duration_sec: float,
) -> tuple[np.ndarray, np.ndarray]:
    if not source_events or not target_events:
        anchors = np.array([0.0, duration_sec], dtype=np.float32)
        return anchors, anchors

    n = min(len(source_events), len(target_events))
    source_pairs = np.array([max(0.0, float(source_events[idx].onset)) for idx in range(n)], dtype=np.float32)
    target_pairs = np.array([max(0.0, float(target_events[idx].onset)) for idx in range(n)], dtype=np.float32)
    order = np.argsort(source_pairs, kind="stable")
    source_pairs = source_pairs[order]
    target_pairs = target_pairs[order]

    src = [0.0]
    tgt = [0.0]
    start = 0
    while start < n:
        stop = start + 1
        while stop < n and abs(float(source_pairs[stop]) - float(source_pairs[start])) < 1e-4:
            stop += 1
        src_val = float(np.mean(source_pairs[start:stop]))
        tgt_val = float(np.mean(target_pairs[start:stop]))
        if src_val > src[-1] and tgt_val > tgt[-1]:
            src.append(src_val)
            tgt.append(tgt_val)
        start = stop

    src.append(float(duration_sec))
    tgt.append(float(duration_sec))
    return np.asarray(src, dtype=np.float32), np.asarray(tgt, dtype=np.float32)


def _asap_pairs(zip_file: zipfile.ZipFile) -> list[tuple[str, str]]:
    groups: dict[str, dict[str, list[str] | str]] = {}
    for member in zip_file.namelist():
        if not member.lower().endswith(".mid"):
            continue
        folder = member.rsplit("/", 1)[0]
        bucket = groups.setdefault(folder, {"score": "", "perf": []})
        if member.endswith("midi_score.mid"):
            bucket["score"] = member
        else:
            bucket["perf"].append(member)

    pairs: list[tuple[str, str]] = []
    for folder, bucket in groups.items():
        score = str(bucket["score"])
        if not score:
            continue
        for perf in bucket["perf"]:
            pairs.append((perf, score))
    return pairs


def _pop909_members(zip_file: zipfile.ZipFile) -> list[str]:
    members = []
    for name in zip_file.namelist():
        if not name.endswith(".mid"):
            continue
        parts = name.split("/")
        if len(parts) == 4 and parts[-1][:3].isdigit():
            members.append(name)
    return members


def import_public_midi(
    downloads_dir: Path,
    output_dir: Path,
    maestro_count: int = 24,
    groove_count: int = 24,
    egmd_count: int = 24,
    asap_count: int = 24,
    pop909_count: int = 24,
    musicnet_count: int = 24,
    sr: int = 22050,
    seed: int = 7,
) -> int:
    rng = random.Random(seed)
    created = 0
    existing_keys = _existing_keys(output_dir)

    maestro_zip = downloads_dir / "maestro-v3.0.0-midi.zip"
    if maestro_zip.exists() and maestro_count > 0:
        start_idx = _next_index(output_dir, "maestro")
        with zipfile.ZipFile(maestro_zip) as archive:
            members = _maestro_members(archive)
            rng.shuffle(members)
            written = 0
            for member in members:
                if member in existing_keys:
                    continue
                events, bpm = _iter_note_events(archive.read(member))
                events, duration_sec = _crop_events(events, max_duration=24.0, rng=rng)
                if len(events) < 16:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=8)
                original = _render_piano(events, duration_sec, sr)
                warped = _render_piano(warped_events, duration_sec, sr)
                _write_example(
                    output_dir / f"maestro_{start_idx + written:04d}",
                    original,
                    warped,
                    sr,
                    {"dataset": "maestro", "source_member": member, "bpm": bpm, "event_count": len(events), "subdivision": 8},
                )
                existing_keys.add(member)
                created += 1
                written += 1
                if written >= maestro_count:
                    break

    maestro_audio_zip = downloads_dir / "maestro-v3.0.0.zip"
    if maestro_audio_zip.exists() and maestro_count > 0:
        start_idx = _next_index(output_dir, "maestro_audio")
        with zipfile.ZipFile(maestro_audio_zip) as archive:
            rows = _maestro_rows(archive)
            rng.shuffle(rows)
            written = 0
            for row in rows:
                midi_member = row.get("midi_filename", "")
                audio_member = row.get("audio_filename", "")
                if midi_member and not midi_member.startswith("maestro-v3.0.0/"):
                    midi_member = f"maestro-v3.0.0/{midi_member}"
                if audio_member and not audio_member.startswith("maestro-v3.0.0/"):
                    audio_member = f"maestro-v3.0.0/{audio_member}"
                if not midi_member or not audio_member or midi_member in existing_keys:
                    continue
                if midi_member not in archive.namelist() or audio_member not in archive.namelist():
                    continue
                events, bpm = _iter_note_events(archive.read(midi_member))
                if len(events) < 24:
                    continue
                crop_start, crop_end, duration_hint = _choose_crop_window(events, max_duration=28.0, rng=rng)
                events = _crop_events_to_window(events, crop_start, crop_end)
                if len(events) < 24:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=8)
                original_audio, audio_sr = _read_zip_audio(archive.read(audio_member), sr)
                start_sample = max(0, int(round(crop_start * audio_sr)))
                end_sample = min(len(original_audio), int(round(crop_end * audio_sr)))
                if end_sample - start_sample < int(4.0 * audio_sr):
                    continue
                original_audio = original_audio[start_sample:end_sample]
                duration_sec = max(float(len(original_audio) / audio_sr), duration_hint)
                src_times, tgt_times = _alignment_curve_from_events(events, warped_events, duration_sec)
                warped_audio, warp_method = apply_warp(original_audio, audio_sr, src_times, tgt_times)
                _write_example(
                    output_dir / f"maestro_audio_{start_idx + written:04d}",
                    finalize_mix(original_audio.astype(np.float32)),
                    finalize_mix(warped_audio.astype(np.float32)),
                    audio_sr,
                    {
                        "dataset": "maestro_audio",
                        "source_member": midi_member,
                        "audio_member": audio_member,
                        "composer": row.get("canonical_composer", ""),
                        "title": row.get("canonical_title", ""),
                        "split": row.get("split", ""),
                        "year": row.get("year", ""),
                        "bpm": bpm,
                        "event_count": len(events),
                        "subdivision": 8,
                        "warp_method": warp_method,
                    },
                )
                existing_keys.add(midi_member)
                created += 1
                written += 1
                if written >= maestro_count:
                    break

    groove_audio_zip = downloads_dir / "groove-v1.0.0.zip"
    if groove_audio_zip.exists() and groove_count > 0:
        start_idx = _next_index(output_dir, "groove_audio")
        with zipfile.ZipFile(groove_audio_zip) as archive:
            info_rows = []
            for candidate in ("groove/info.csv", "info.csv"):
                if candidate in archive.namelist():
                    text = archive.read(candidate).decode("utf-8")
                    header, *rows = text.strip().splitlines()
                    columns = header.split(",")
                    for row in rows:
                        values = row.split(",")
                        if len(values) != len(columns):
                            continue
                        info_rows.append(dict(zip(columns, values)))
                    break
            rng.shuffle(info_rows)
            written = 0
            for row in info_rows:
                member = row.get("midi_filename", "")
                audio_member = row.get("audio_filename", "")
                if member and not member.startswith("groove/"):
                    member = f"groove/{member}"
                if audio_member and not audio_member.startswith("groove/"):
                    audio_member = f"groove/{audio_member}"
                if not member or not audio_member or member in existing_keys:
                    continue
                if member not in archive.namelist() or audio_member not in archive.namelist():
                    continue
                events, bpm = _iter_note_events(archive.read(member))
                if len(events) < 8:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=16)
                original_audio, audio_sr = _read_zip_audio(archive.read(audio_member), sr)
                duration_sec = max(float(len(original_audio) / audio_sr), max((event.onset + event.duration for event in events), default=0.0) + 0.25)
                src_times, tgt_times = _alignment_curve_from_events(events, warped_events, duration_sec)
                warped_audio, warp_method = apply_warp(original_audio, audio_sr, src_times, tgt_times)
                _write_example(
                    output_dir / f"groove_audio_{start_idx + written:04d}",
                    finalize_mix(original_audio.astype(np.float32)),
                    finalize_mix(warped_audio.astype(np.float32)),
                    audio_sr,
                    {
                        "dataset": "groove_audio",
                        "source_member": member,
                        "audio_member": audio_member,
                        "bpm": bpm,
                        "event_count": len(events),
                        "subdivision": 16,
                        "warp_method": warp_method,
                    },
                )
                existing_keys.add(member)
                created += 1
                written += 1
                if written >= groove_count:
                    break

    groove_zip = downloads_dir / "groove-v1.0.0-midionly.zip"
    if groove_zip.exists() and groove_count > 0 and not groove_audio_zip.exists():
        start_idx = _next_index(output_dir, "groove")
        with zipfile.ZipFile(groove_zip) as archive:
            members = _groove_members(archive)
            rng.shuffle(members)
            written = 0
            for member in members:
                if member in existing_keys:
                    continue
                events, bpm = _iter_note_events(archive.read(member))
                duration_sec = max((event.onset + event.duration for event in events), default=0.0) + 0.25
                if len(events) < 8 or duration_sec > 32.0:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=16)
                original = _render_drums(events, duration_sec, sr)
                warped = _render_drums(warped_events, duration_sec, sr)
                _write_example(
                    output_dir / f"groove_{start_idx + written:04d}",
                    original,
                    warped,
                    sr,
                    {"dataset": "groove", "source_member": member, "bpm": bpm, "event_count": len(events), "subdivision": 16},
                )
                existing_keys.add(member)
                created += 1
                written += 1
                if written >= groove_count:
                    break

    egmd_zip = downloads_dir / "e-gmd-v1.0.0-midi.zip"
    if egmd_zip.exists() and egmd_count > 0:
        start_idx = _next_index(output_dir, "egmd")
        with zipfile.ZipFile(egmd_zip) as archive:
            members = _groove_members(archive)
            rng.shuffle(members)
            written = 0
            for member in members:
                if member in existing_keys:
                    continue
                events, bpm = _iter_note_events(archive.read(member))
                duration_sec = max((event.onset + event.duration for event in events), default=0.0) + 0.25
                if len(events) < 8 or duration_sec > 24.0:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=16)
                original = _render_drums(events, duration_sec, sr)
                warped = _render_drums(warped_events, duration_sec, sr)
                _write_example(
                    output_dir / f"egmd_{start_idx + written:04d}",
                    original,
                    warped,
                    sr,
                    {"dataset": "e-gmd", "source_member": member, "bpm": bpm, "event_count": len(events), "subdivision": 16},
                )
                existing_keys.add(member)
                created += 1
                written += 1
                if written >= egmd_count:
                    break

    asap_zip = downloads_dir / "asap-dataset-master.zip"
    if asap_zip.exists() and asap_count > 0:
        start_idx = _next_index(output_dir, "asap")
        with zipfile.ZipFile(asap_zip) as archive:
            pairs = _asap_pairs(archive)
            rng.shuffle(pairs)
            written = 0
            for perf_member, score_member in pairs:
                if perf_member in existing_keys:
                    continue
                perf_events, perf_bpm = _iter_note_events(archive.read(perf_member))
                score_events, _ = _iter_note_events(archive.read(score_member))
                perf_events, duration_sec = _crop_events(perf_events, max_duration=22.0, rng=rng)
                score_events, _ = _crop_events(score_events, max_duration=max(8.0, duration_sec - 0.25), rng=rng)
                if len(perf_events) < 16 or len(score_events) < 16:
                    continue
                duration_sec = max(
                    max((event.onset + event.duration for event in perf_events), default=0.0),
                    max((event.onset + event.duration for event in score_events), default=0.0),
                ) + 0.25
                original = _render_piano(perf_events, duration_sec, sr)
                warped = _render_piano(score_events, duration_sec, sr)
                _write_example(
                    output_dir / f"asap_{start_idx + written:04d}",
                    original,
                    warped,
                    sr,
                    {"dataset": "asap", "performance_member": perf_member, "score_member": score_member, "bpm": perf_bpm, "event_count": len(perf_events)},
                )
                existing_keys.add(perf_member)
                created += 1
                written += 1
                if written >= asap_count:
                    break

    pop909_zip = downloads_dir / "POP909-Dataset-master.zip"
    if pop909_zip.exists() and pop909_count > 0:
        start_idx = _next_index(output_dir, "pop909")
        with zipfile.ZipFile(pop909_zip) as archive:
            members = _pop909_members(archive)
            rng.shuffle(members)
            written = 0
            for member in members:
                if member in existing_keys:
                    continue
                events, bpm = _iter_note_events(archive.read(member))
                events, duration_sec = _crop_events(events, max_duration=24.0, rng=rng)
                if len(events) < 24:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=8)
                original = _render_piano(events, duration_sec, sr)
                warped = _render_piano(warped_events, duration_sec, sr)
                _write_example(
                    output_dir / f"pop909_{start_idx + written:04d}",
                    original,
                    warped,
                    sr,
                    {"dataset": "pop909", "source_member": member, "bpm": bpm, "event_count": len(events), "subdivision": 8},
                )
                existing_keys.add(member)
                created += 1
                written += 1
                if written >= pop909_count:
                    break

    musicnet_tar = downloads_dir / "musicnet.tar.gz"
    if musicnet_tar.exists() and musicnet_count > 0:
        start_idx = _next_index(output_dir, "musicnet_audio")
        metadata_rows = _musicnet_metadata(downloads_dir)
        with tarfile.open(musicnet_tar, "r:gz") as archive:
            entries = _musicnet_entries(archive)
            rng.shuffle(entries)
            entries.sort(key=lambda item: _musicnet_priority(metadata_rows.get(Path(item[0]).stem)))
            written = 0
            for audio_member, label_member in entries:
                if audio_member in existing_keys:
                    continue
                item_id = Path(audio_member).stem
                row = metadata_rows.get(item_id, {})
                audio_handle = archive.extractfile(audio_member)
                label_handle = archive.extractfile(label_member)
                if audio_handle is None or label_handle is None:
                    continue
                original_audio, audio_sr = _read_tar_audio(audio_handle.read(), sr)
                label_text = label_handle.read().decode("utf-8")
                events, bpm = _musicnet_note_events(label_text, audio_sr)
                if len(events) < 24:
                    continue
                crop_start, crop_end, duration_hint = _choose_crop_window(events, max_duration=28.0, rng=rng)
                events = _crop_events_to_window(events, crop_start, crop_end)
                if len(events) < 24:
                    continue
                warped_events = _quantize_events(events, bpm, subdivision=8)
                start_sample = max(0, int(round(crop_start * audio_sr)))
                end_sample = min(len(original_audio), int(round(crop_end * audio_sr)))
                if end_sample - start_sample < int(4.0 * audio_sr):
                    continue
                original_crop = original_audio[start_sample:end_sample]
                duration_sec = max(float(len(original_crop) / audio_sr), duration_hint)
                src_times, tgt_times = _alignment_curve_from_events(events, warped_events, duration_sec)
                warped_audio, warp_method = apply_warp(original_crop, audio_sr, src_times, tgt_times)
                _write_example(
                    output_dir / f"musicnet_audio_{start_idx + written:04d}",
                    finalize_mix(original_crop.astype(np.float32)),
                    finalize_mix(warped_audio.astype(np.float32)),
                    audio_sr,
                    {
                        "dataset": "musicnet_audio",
                        "source_member": audio_member,
                        "label_member": label_member,
                        "musicnet_id": item_id,
                        "composer": row.get("composer", ""),
                        "composition": row.get("composition", ""),
                        "movement": row.get("movement", ""),
                        "ensemble": row.get("ensemble", ""),
                        "source": row.get("source", ""),
                        "seconds": row.get("seconds", ""),
                        "bpm": bpm,
                        "event_count": len(events),
                        "subdivision": 8,
                        "warp_method": warp_method,
                    },
                )
                existing_keys.add(audio_member)
                created += 1
                written += 1
                if written >= musicnet_count:
                    break

    return created


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--downloads", type=Path, default=Path("data/downloads"))
    parser.add_argument("--output", type=Path, default=Path("data/examples"))
    parser.add_argument("--maestro-count", type=int, default=24)
    parser.add_argument("--groove-count", type=int, default=24)
    parser.add_argument("--egmd-count", type=int, default=24)
    parser.add_argument("--asap-count", type=int, default=24)
    parser.add_argument("--pop909-count", type=int, default=24)
    parser.add_argument("--musicnet-count", type=int, default=24)
    parser.add_argument("--sr", type=int, default=22050)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    created = import_public_midi(
        downloads_dir=args.downloads,
        output_dir=args.output,
        maestro_count=args.maestro_count,
        groove_count=args.groove_count,
        egmd_count=args.egmd_count,
        asap_count=args.asap_count,
        pop909_count=args.pop909_count,
        musicnet_count=args.musicnet_count,
        sr=args.sr,
        seed=args.seed,
    )
    print(f"created_examples={created}")


if __name__ == "__main__":
    main()


