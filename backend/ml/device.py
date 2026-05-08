from __future__ import annotations

from contextlib import nullcontext

import torch


def resolve_device(device: str = "auto") -> str:
    requested = (device or "auto").strip().lower()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        return "cpu"
    return requested


def uses_cuda(device: str) -> bool:
    return resolve_device(device).startswith("cuda")


def autocast_context(device: str):
    resolved = resolve_device(device)
    if resolved.startswith("cuda"):
        return torch.autocast(device_type="cuda", dtype=torch.float16)
    return nullcontext()
