from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SAFE_NAME_RE = re.compile(r'[^A-Za-z0-9._-]+')
ALLOWED_SCHEMES = {'http', 'https'}


def _safe_name(value: str, fallback: str) -> str:
    cleaned = SAFE_NAME_RE.sub('_', value.strip()).strip('._')
    return cleaned or fallback


def _detect_extension(url: str, content_type: str | None = None) -> str:
    path = urllib.parse.urlparse(url).path
    suffix = Path(path).suffix.lower()
    if suffix:
        return suffix
    mapping = {
        'audio/mpeg': '.mp3',
        'audio/mp4': '.m4a',
        'audio/x-m4a': '.m4a',
        'audio/wav': '.wav',
        'audio/x-wav': '.wav',
        'audio/flac': '.flac',
        'audio/ogg': '.ogg',
        'audio/opus': '.opus',
    }
    if content_type:
        return mapping.get(content_type.split(';', 1)[0].strip().lower(), '.bin')
    return '.bin'


def _manifest_rows(manifest_path: Path) -> list[dict[str, str]]:
    if manifest_path.suffix.lower() == '.jsonl':
        rows = []
        for line in manifest_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows
    with manifest_path.open('r', encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def _download(url: str, output_path: Path, timeout: int = 60) -> dict[str, object]:
    req = urllib.request.Request(url, headers={'User-Agent': 'BoxBox/0.1'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        content_type = resp.headers.get('Content-Type', '')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    sha1 = hashlib.sha1(data).hexdigest()
    return {
        'bytes': len(data),
        'sha1': sha1,
        'content_type': content_type,
    }


def download_manifest(manifest_path: Path, output_dir: Path, metadata_path: Path | None = None, limit: int = 0) -> dict[str, int]:
    rows = _manifest_rows(manifest_path)
    if limit > 0:
        rows = rows[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    if metadata_path is None:
        metadata_path = output_dir / 'manifest_downloads.jsonl'

    existing_sha1: set[str] = set()
    if metadata_path.exists():
        for line in metadata_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            sha1 = payload.get('sha1')
            if isinstance(sha1, str) and sha1:
                existing_sha1.add(sha1)

    downloaded = 0
    skipped = 0
    failed = 0
    metadata_lines: list[str] = []

    for idx, row in enumerate(rows, start=1):
        url = str(row.get('url') or '').strip()
        if not url:
            skipped += 1
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            skipped += 1
            continue
        title = str(row.get('title') or row.get('name') or f'legacy_{idx:04d}')
        source = str(row.get('source') or parsed.netloc or 'unknown')
        ext = _detect_extension(url, str(row.get('content_type') or ''))
        filename = f"{_safe_name(source, 'src')}_{_safe_name(title, f'legacy_{idx:04d}')}{ext}"
        output_path = output_dir / filename

        try:
            result = _download(url, output_path)
        except (urllib.error.URLError, TimeoutError, ValueError):
            failed += 1
            continue

        sha1 = str(result['sha1'])
        if sha1 in existing_sha1:
            output_path.unlink(missing_ok=True)
            skipped += 1
            continue
        existing_sha1.add(sha1)
        downloaded += 1
        record = {
            'url': url,
            'source': source,
            'title': title,
            'output_file': output_path.name,
            'bytes': int(result['bytes']),
            'sha1': sha1,
            'content_type': str(result.get('content_type') or ''),
            'tags': row.get('tags', ''),
            'notes': row.get('notes', ''),
            'public_domain': str(row.get('public_domain', '')),
        }
        metadata_lines.append(json.dumps(record, ensure_ascii=True))

    if metadata_lines:
        with metadata_path.open('a', encoding='utf-8') as handle:
            for line in metadata_lines:
                handle.write(line + '\n')

    return {
        'downloaded': downloaded,
        'skipped': skipped,
        'failed': failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/legacy_sources'))
    parser.add_argument('--metadata', type=Path, default=None)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()

    result = download_manifest(args.manifest, args.output, args.metadata, args.limit)
    for key, value in result.items():
        print(f'{key}={value}')


if __name__ == '__main__':
    main()
