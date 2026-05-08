from __future__ import annotations

import numpy as np

from backend.ml.import_public_midi import NoteEvent, _alignment_curve_from_events


def test_alignment_curve_from_events_preserves_monotonic_anchor_mapping():
    source = [
        NoteEvent(onset=0.10, duration=0.2, midi=36, velocity=0.8),
        NoteEvent(onset=0.10, duration=0.2, midi=42, velocity=0.8),
        NoteEvent(onset=0.62, duration=0.2, midi=38, velocity=0.8),
        NoteEvent(onset=1.12, duration=0.2, midi=42, velocity=0.8),
    ]
    target = [
        NoteEvent(onset=0.00, duration=0.2, midi=36, velocity=0.8),
        NoteEvent(onset=0.00, duration=0.2, midi=42, velocity=0.8),
        NoteEvent(onset=0.50, duration=0.2, midi=38, velocity=0.8),
        NoteEvent(onset=1.00, duration=0.2, midi=42, velocity=0.8),
    ]

    src_times, tgt_times = _alignment_curve_from_events(source, target, duration_sec=1.6)

    assert np.all(np.diff(src_times) > 0.0)
    assert np.all(np.diff(tgt_times) > 0.0)
    assert src_times[0] == 0.0
    assert tgt_times[0] == 0.0
    assert src_times[-1] == np.float32(1.6)
    assert tgt_times[-1] == np.float32(1.6)
