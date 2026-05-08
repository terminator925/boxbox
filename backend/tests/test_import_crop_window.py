from __future__ import annotations

import random

from backend.ml.import_public_midi import NoteEvent, _choose_crop_window, _crop_events_to_window


def test_crop_window_and_events_keep_relative_offsets_consistent():
    events = [
        NoteEvent(onset=1.0, duration=0.3, midi=60, velocity=0.7),
        NoteEvent(onset=2.1, duration=0.2, midi=62, velocity=0.8),
        NoteEvent(onset=4.2, duration=0.4, midi=64, velocity=0.9),
    ]

    start, end, duration = _choose_crop_window(events, max_duration=2.5, rng=random.Random(7))
    cropped = _crop_events_to_window(events, start, end)

    assert end > start
    assert duration > 0.0
    assert cropped
    assert min(event.onset for event in cropped) >= 0.0
    assert max(event.onset + event.duration for event in cropped) <= duration
