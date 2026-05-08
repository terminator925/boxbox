from __future__ import annotations

import json
from pathlib import Path

from backend.ml.download_legacy_manifest import _detect_extension, _manifest_rows, _safe_name


def test_safe_name_normalizes_strings():
    assert _safe_name('  Duke Ellington / Take A Train  ', 'fallback') == 'Duke_Ellington_Take_A_Train'
    assert _safe_name('***', 'fallback') == 'fallback'


def test_detect_extension_prefers_url_suffix_then_content_type():
    assert _detect_extension('https://example.com/audio/file.mp3') == '.mp3'
    assert _detect_extension('https://example.com/audio/noext', 'audio/flac') == '.flac'
    assert _detect_extension('https://example.com/audio/noext', None) == '.bin'


def test_manifest_rows_supports_csv_and_jsonl(tmp_path: Path):
    csv_path = tmp_path / 'manifest.csv'
    csv_path.write_text('url,title\nhttps://example.com/a.mp3,Track A\n', encoding='utf-8')
    jsonl_path = tmp_path / 'manifest.jsonl'
    jsonl_path.write_text(json.dumps({'url': 'https://example.com/b.ogg', 'title': 'Track B'}) + '\n', encoding='utf-8')

    csv_rows = _manifest_rows(csv_path)
    jsonl_rows = _manifest_rows(jsonl_path)

    assert csv_rows[0]['title'] == 'Track A'
    assert jsonl_rows[0]['title'] == 'Track B'
