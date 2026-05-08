from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import openvino as ov
import torch

from backend.ml.model import BoxBoxWarpNet

OPENVINO_DEVICE_PRIORITY = ("NPU", "GPU", "CPU")


class _ExportWrapper(torch.nn.Module):
    def __init__(self, model: BoxBoxWarpNet):
        super().__init__()
        self.model = model

    def forward(self, features: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        return self.model.forward_export(features, mask=mask)


@lru_cache(maxsize=1)
def _core() -> ov.Core:
    return ov.Core()


def available_devices() -> list[str]:
    return list(_core().available_devices)


def preferred_device() -> str | None:
    devices = set(available_devices())
    for device in OPENVINO_DEVICE_PRIORITY:
        if device in devices:
            return device
    return None


def onnx_path_for_model(model_path: Path, seq_len: int) -> Path:
    return model_path.with_name(f"{model_path.stem}_t{int(seq_len)}.onnx")


def ensure_onnx_export(model_path: Path, seq_len: int, feature_dim: int) -> Path:
    onnx_path = onnx_path_for_model(model_path, seq_len)
    if onnx_path.exists() and onnx_path.stat().st_mtime_ns >= model_path.stat().st_mtime_ns:
        return onnx_path

    ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
    feature_dim = int(ckpt.get("feature_dim") or feature_dim or 82)
    model = BoxBoxWarpNet(feature_dim=feature_dim)
    model.load_state_dict(ckpt["model"])
    model.eval()
    wrapper = _ExportWrapper(model)

    dummy_x = torch.randn(1, seq_len, feature_dim, dtype=torch.float32)
    dummy_mask = torch.ones(1, seq_len, dtype=torch.float32)
    torch.onnx.export(
        wrapper,
        (dummy_x, dummy_mask),
        onnx_path,
        input_names=["features", "mask"],
        output_names=["pred"],
        opset_version=17,
        dynamo=False,
    )
    return onnx_path


@lru_cache(maxsize=16)
def _compiled_model(model_path_str: str, ov_device: str, seq_len: int, feature_dim: int):
    model_path = Path(model_path_str)
    onnx_path = ensure_onnx_export(model_path, seq_len=seq_len, feature_dim=feature_dim)
    compiled = _core().compile_model(str(onnx_path), ov_device)
    output = compiled.output(0)
    return compiled, output


def infer_with_openvino(model_path: Path, features: np.ndarray, mask: np.ndarray, ov_device: str | None = None) -> tuple[np.ndarray, str] | None:
    device = ov_device or preferred_device()
    if device is None:
        return None
    seq_len = int(features.shape[1])
    feature_dim = int(features.shape[2])
    compiled, output = _compiled_model(str(model_path.resolve()), device, seq_len, feature_dim)
    result = compiled([features.astype(np.float32), mask.astype(np.float32)])[output]
    pred = np.asarray(result, dtype=np.float32)
    return pred, device
