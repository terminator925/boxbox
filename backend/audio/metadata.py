from __future__ import annotations

import base64
import re
from typing import Mapping

import numpy as np


_SERATO_AUTOTAGS_RE = re.compile(rb"Serato Autotags\x00+\x01\x01([0-9]+(?:\.[0-9]+)?)")


def _credible_bpm(value: object) -> float | None:
    try:
        bpm = float(str(value).strip().split()[0])
    except (TypeError, ValueError):
        return None
    if np.isfinite(bpm) and 40.0 <= bpm <= 240.0:
        return bpm
    return None


def _decode_metadata_blob(raw: object) -> bytes | None:
    compact = "".join(str(raw or "").split())
    if not compact:
        return None
    try:
        return base64.b64decode(compact + "=" * ((4 - len(compact) % 4) % 4), validate=False)
    except Exception:
        return None


def metadata_bpm_from_tags(tags: Mapping[str, object] | None) -> float | None:
    normalized = {str(key).lower(): value for key, value in dict(tags or {}).items()}
    for key in ("tbpm", "bpm", "tempo"):
        bpm = _credible_bpm(normalized.get(key))
        if bpm is not None:
            return bpm

    # Serato stores the analyzed BPM inside a base64 application/octet-stream tag.
    # The first value in Autotags is the tempo; later floats are gain/volume values.
    decoded_autogain = _decode_metadata_blob(normalized.get("autgain"))
    if decoded_autogain:
        match = _SERATO_AUTOTAGS_RE.search(decoded_autogain)
        if match:
            bpm = _credible_bpm(match.group(1).decode("ascii", errors="ignore"))
            if bpm is not None:
                return bpm

    return None
