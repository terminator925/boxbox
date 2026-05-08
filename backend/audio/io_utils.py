from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


LOSSLESS_EXTENSIONS = {".wav", ".flac", ".aiff", ".aif"}
SOURCE_EXPORT_CODECS = {
    ".wav": ["-c:a", "pcm_s24le"],
    ".flac": ["-c:a", "flac", "-compression_level", os.getenv("BOXBOX_FLAC_COMPRESSION", "5")],
    ".aiff": ["-c:a", "pcm_s24be"],
    ".aif": ["-c:a", "pcm_s24be"],
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "0"],
    ".m4a": ["-c:a", "aac", "-b:a", "320k"],
    ".mp4": ["-c:a", "aac", "-b:a", "320k"],
    ".aac": ["-c:a", "aac", "-b:a", "320k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "10"],
    ".opus": ["-c:a", "libopus", "-b:a", "510k", "-vbr", "on"],
    ".wma": ["-c:a", "wmav2", "-b:a", "320k"],
}


def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required tool not found on PATH: {cmd[0]}") from exc


def probe_audio(path: Path) -> dict:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = _run_subprocess(cmd)
    if result.returncode != 0:
        err = (result.stderr or "").strip() or "unknown ffprobe error"
        raise RuntimeError(f"Audio probe failed: {err}")

    payload = json.loads(result.stdout or "{}")
    audio_stream = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio_stream:
        raise RuntimeError("No audio stream found in uploaded file")

    fmt = payload.get("format", {})
    metadata_tags = {}
    for tag_source in (fmt.get("tags"), audio_stream.get("tags")):
        if isinstance(tag_source, dict):
            for key, value in tag_source.items():
                metadata_tags[str(key).lower()] = str(value)
    sample_rate = int(float(audio_stream.get("sample_rate") or 0) or 0)
    channels = int(audio_stream.get("channels") or 0)
    duration = float(fmt.get("duration") or audio_stream.get("duration") or 0.0)
    bit_rate_raw = audio_stream.get("bit_rate") or fmt.get("bit_rate") or 0
    bits_per_sample = int(audio_stream.get("bits_per_raw_sample") or audio_stream.get("bits_per_sample") or 0)
    extension = path.suffix.lower()
    codec = str(audio_stream.get("codec_name") or "").lower()

    return {
        "original_filename": path.name,
        "container_extension": extension,
        "format_name": str(fmt.get("format_name") or ""),
        "codec_name": codec,
        "duration_sec": duration,
        "sample_rate": sample_rate,
        "channels": channels,
        "bit_rate": int(float(bit_rate_raw) or 0),
        "bits_per_sample": bits_per_sample,
        "is_lossless_source": extension in LOSSLESS_EXTENSIONS or codec in {"pcm_s16le", "pcm_s24le", "pcm_f32le", "flac", "alac"},
        "metadata_tags": metadata_tags,
    }


def _resample_if_needed(audio: np.ndarray, src_sr: int, target_sr: int) -> np.ndarray:
    if src_sr == target_sr:
        return audio
    return resample_poly(audio, target_sr, src_sr, axis=0)


def convert_to_wav(src_path: Path, dst_path: Path, target_sr: int | None = None) -> dict:
    if not src_path.exists():
        raise FileNotFoundError(f"Input audio not found: {src_path}")

    metadata = probe_audio(src_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    target_sr = int(target_sr or metadata["sample_rate"] or 44100)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src_path),
        "-vn",
        "-map",
        "a:0",
        "-c:a",
        "pcm_f32le",
        "-ar",
        str(target_sr),
        str(dst_path),
    ]
    result = _run_subprocess(cmd)
    if result.returncode == 0 and dst_path.exists() and dst_path.stat().st_size > 0:
        metadata["processing_sample_rate"] = target_sr
        return metadata

    try:
        audio, src_sr = sf.read(str(src_path), always_2d=True)
        audio = _resample_if_needed(audio, int(src_sr), target_sr)
        sf.write(str(dst_path), audio, target_sr, subtype="FLOAT")
        metadata["processing_sample_rate"] = target_sr
        return metadata
    except Exception:
        err = (result.stderr or "").strip() or "unknown ffmpeg error"
        raise RuntimeError(f"Audio conversion failed: {err}")


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    try:
        audio, sr = sf.read(str(path), always_2d=True)
        return audio.astype(np.float32), sr
    except Exception:
        metadata = probe_audio(path)
        sr = int(metadata.get("sample_rate") or 44100)
        channels = max(1, int(metadata.get("channels") or 1))
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",
            "-map",
            "a:0",
            "-f",
            "f32le",
            "pipe:1",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("Required tool not found on PATH: ffmpeg") from exc
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip() or "unknown ffmpeg error"
            raise RuntimeError(f"Audio load failed: {err}")
        flat = np.frombuffer(result.stdout, dtype=np.float32)
        if len(flat) == 0:
            raise RuntimeError("Audio load failed: empty ffmpeg output")
        usable = (len(flat) // channels) * channels
        audio = flat[:usable].reshape(-1, channels)
        return audio.astype(np.float32), sr


def write_audio(path: Path, audio: np.ndarray, sr: int, subtype: str = "PCM_24") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, sr, subtype=subtype)


def _ffmpeg_encode(src_wav: Path, dst_path: Path, codec_args: list[str]) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src_wav),
        *codec_args,
        str(dst_path),
    ]
    result = _run_subprocess(cmd)
    return result.returncode == 0 and dst_path.exists() and dst_path.stat().st_size > 0


def export_quantized_audio(output_dir: Path, audio: np.ndarray, sr: int, source_meta: dict | None = None) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)

    browser_wav = output_dir / "quantized.wav"
    write_audio(browser_wav, audio, sr, subtype="PCM_24")

    master_path = output_dir / "quantized_master.flac"
    _ffmpeg_encode(browser_wav, master_path, ["-c:a", "flac", "-compression_level", os.getenv("BOXBOX_FLAC_COMPRESSION", "5")])
    if not master_path.exists():
        master_path = browser_wav

    files = {
        "browser_audio": browser_wav.name,
        "master_audio": master_path.name,
        "primary_audio": master_path.name,
    }

    if source_meta:
        source_ext = str(source_meta.get("container_extension") or "").lower()
        codec_args = SOURCE_EXPORT_CODECS.get(source_ext)
        if codec_args:
            source_match_path = output_dir / f"quantized_source{source_ext}"
            if _ffmpeg_encode(browser_wav, source_match_path, codec_args):
                files["source_match_audio"] = source_match_path.name
                if source_meta.get("is_lossless_source"):
                    files["primary_audio"] = source_match_path.name

    return files


def audio_info(audio: np.ndarray, sr: int, source_meta: dict | None = None) -> dict:
    channels = int(audio.shape[1])
    duration = float(audio.shape[0] / sr)
    info = {
        "duration_sec": duration,
        "sr": int(sr),
        "channels": channels,
        "is_stereo": channels == 2,
    }
    if source_meta:
        info.update(
            {
                "original_filename": source_meta.get("original_filename", ""),
                "source_extension": source_meta.get("container_extension", ""),
                "source_codec": source_meta.get("codec_name", ""),
                "source_format_name": source_meta.get("format_name", ""),
                "source_sample_rate": int(source_meta.get("sample_rate") or sr),
                "source_bit_rate": int(source_meta.get("bit_rate") or 0),
                "source_bits_per_sample": int(source_meta.get("bits_per_sample") or 0),
                "is_lossless_source": bool(source_meta.get("is_lossless_source", False)),
            }
        )
    return info


def audio_info_from_meta(source_meta: dict | None = None) -> dict:
    meta = dict(source_meta or {})
    sr = int(meta.get("sample_rate") or meta.get("processing_sample_rate") or 0)
    channels = max(1, int(meta.get("channels") or 1))
    info = {
        "duration_sec": float(meta.get("duration_sec") or 0.0),
        "sr": sr,
        "channels": channels,
        "is_stereo": channels == 2,
    }
    if meta:
        info.update(
            {
                "original_filename": meta.get("original_filename", ""),
                "source_extension": meta.get("container_extension", ""),
                "source_codec": meta.get("codec_name", ""),
                "source_format_name": meta.get("format_name", ""),
                "source_sample_rate": int(meta.get("sample_rate") or sr),
                "source_bit_rate": int(meta.get("bit_rate") or 0),
                "source_bits_per_sample": int(meta.get("bits_per_sample") or 0),
                "is_lossless_source": bool(meta.get("is_lossless_source", False)),
            }
        )
    return info


def mixdown_mono(audio: np.ndarray) -> np.ndarray:
    if audio.shape[1] == 1:
        return audio[:, 0]
    return audio.mean(axis=1)
