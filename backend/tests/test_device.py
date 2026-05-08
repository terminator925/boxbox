from __future__ import annotations

from contextlib import nullcontext

import torch

from backend.ml import device as device_mod
from backend.ml import infer as infer_mod


def test_resolve_device_prefers_cuda_when_available(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert device_mod.resolve_device("auto") == "cuda"


def test_resolve_device_falls_back_to_cpu_when_cuda_unavailable(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    assert device_mod.resolve_device("auto") == "cpu"
    assert device_mod.resolve_device("cuda") == "cpu"


def test_autocast_context_is_noop_on_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    ctx = device_mod.autocast_context("auto")
    assert isinstance(ctx, type(nullcontext()))


def test_preferred_inference_accelerator_defaults_and_validates(monkeypatch):
    monkeypatch.delenv("BOXBOX_INFER_ACCELERATOR", raising=False)
    assert infer_mod.preferred_inference_accelerator() == "torch"
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "npu")
    assert infer_mod.preferred_inference_accelerator() == "npu"
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "garbage")
    assert infer_mod.preferred_inference_accelerator() == "torch"
