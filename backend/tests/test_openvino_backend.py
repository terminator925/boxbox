from __future__ import annotations

import numpy as np

from backend.ml import openvino_backend


def test_openvino_backend_gracefully_handles_missing_dependency(monkeypatch):
    monkeypatch.setattr(openvino_backend, "ov", None)
    openvino_backend._core.cache_clear()
    openvino_backend._compiled_model.cache_clear()

    try:
        assert openvino_backend.available_devices() == []
        assert openvino_backend.preferred_device() is None
        assert (
            openvino_backend.infer_with_openvino(
                model_path=None,
                features=np.zeros((1, 4, 8), dtype=np.float32),
                mask=np.ones((1, 4), dtype=np.float32),
            )
            is None
        )
    finally:
        openvino_backend._core.cache_clear()
        openvino_backend._compiled_model.cache_clear()
