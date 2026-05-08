from __future__ import annotations

from pathlib import Path

import numpy as np
import torch

from backend.ml import infer
from backend.ml.infer import (
    _adaptive_core4_model_paths,
    _adaptive_core4_plus_model_paths,
    _adaptive_core4_plus_qf_model_paths,
    _curve_confidence,
    _filter_model_paths_for_strategy,
    _model_role,
    _routing_profile,
    _routing_weight,
    infer_curve_candidates,
    preferred_inference_candidate_strategy,
)


def test_model_role_marks_groove_checkpoint_as_percussive():
    assert _model_role(Path("models/boxbox_before_groove_860.pt")) == "percussive"
    assert _model_role(Path("models/boxbox_legacy_specialist_pilot.pt")) == "legacy"
    assert _model_role(Path("models/boxbox_mixed_legacy_candidate_r1884.pt")) == "mixed_legacy"
    assert _model_role(Path("models/boxbox_latest.pt")) == "mixed"


def test_routing_weight_prefers_matching_domain():
    mixed_curve = {"confidence": 0.6, "model_role": "mixed"}
    mixed_legacy_curve = {"confidence": 0.6, "model_role": "mixed_legacy"}
    percussive_curve = {"confidence": 0.6, "model_role": "percussive"}
    legacy_curve = {"confidence": 0.6, "model_role": "legacy"}

    percussive_profile = {"percussive": 0.8, "legacy": 0.1, "mixed": 0.25}
    mixed_profile = {"percussive": 0.2, "legacy": 0.1, "mixed": 0.8}
    legacy_profile = {"percussive": 0.2, "legacy": 0.9, "mixed": 0.35}

    assert _routing_weight(percussive_curve, percussive_profile) > _routing_weight(mixed_curve, percussive_profile)
    assert _routing_weight(mixed_curve, mixed_profile) > _routing_weight(percussive_curve, mixed_profile)
    assert _routing_weight(legacy_curve, legacy_profile) > _routing_weight(mixed_curve, legacy_profile)
    assert _routing_weight(legacy_curve, legacy_profile) > _routing_weight(mixed_legacy_curve, legacy_profile)


def test_mixed_legacy_routing_is_suppressed_when_legacy_dominates():
    mixed_curve = {"confidence": 0.6, "model_role": "mixed"}
    mixed_legacy_curve = {"confidence": 0.6, "model_role": "mixed_legacy"}

    strongly_legacy_profile = {"percussive": 0.15, "legacy": 0.9, "mixed": 0.6}
    genuinely_mixed_profile = {"percussive": 0.35, "legacy": 0.45, "mixed": 0.78}
    low_legacy_profile = {"percussive": 0.45, "legacy": 0.1, "mixed": 0.82}
    transition_profile = {"percussive": 0.28, "legacy": 0.44, "mixed": 0.62}

    strong_legacy_weight = _routing_weight(mixed_legacy_curve, strongly_legacy_profile)
    genuinely_mixed_weight = _routing_weight(mixed_legacy_curve, genuinely_mixed_profile)
    low_legacy_weight = _routing_weight(mixed_legacy_curve, low_legacy_profile)
    transition_weight = _routing_weight(mixed_legacy_curve, transition_profile)

    assert strong_legacy_weight < _routing_weight(mixed_curve, strongly_legacy_profile)
    assert low_legacy_weight < _routing_weight(mixed_curve, low_legacy_profile)
    assert genuinely_mixed_weight > strong_legacy_weight * 5.0
    assert genuinely_mixed_weight > low_legacy_weight * 5.0
    assert transition_weight < genuinely_mixed_weight


def test_routing_profile_returns_expected_keys():
    y = np.zeros(4096, dtype=np.float32)
    profile = _routing_profile(y, 16000)

    assert set(profile) == {"percussive", "legacy", "mixed"}
    assert all(0.0 <= float(value) <= 1.0 for value in profile.values())


def test_curve_confidence_rewards_baseline_agreement():
    baseline = np.linspace(0.0, 1.0, 8, dtype=np.float32)
    close_pred = baseline.copy()
    far_pred = np.array([0.0, 0.0, 0.05, 0.07, 0.6, 0.65, 0.8, 1.0], dtype=np.float32)

    assert _curve_confidence(close_pred, baseline, 4.0) > _curve_confidence(far_pred, baseline, 4.0)


def test_infer_curve_candidates_include_specialists_and_routed(monkeypatch, tmp_path: Path):
    models_dir = tmp_path
    groove = models_dir / "boxbox_before_groove_860.pt"
    latest = models_dir / "boxbox_latest.pt"
    mixed_legacy = models_dir / "boxbox_mixed_legacy_candidate_r1884.pt"
    legacy = models_dir / "boxbox_legacy_specialist_r1730.pt"
    legacy_qm = models_dir / "boxbox_legacy_specialist_r1884_qm.pt"
    legacy_qf = models_dir / "boxbox_legacy_specialist_r1884_qf.pt"
    groove.write_bytes(b"x")
    latest.write_bytes(b"y")
    mixed_legacy.write_bytes(b"w")
    legacy.write_bytes(b"z")
    legacy_qm.write_bytes(b"m")
    legacy_qf.write_bytes(b"q")

    def fake_infer(model_file: Path, *args, **kwargs):
        return {
            "source_times": np.array([0.0, 1.0], dtype=np.float32),
            "target_times": np.array([0.0, 1.0], dtype=np.float32),
            "confidence": 0.8 if "groove" in model_file.name else (0.76 if "r1884_qf" in model_file.name else (0.755 if "r1884_qm" in model_file.name else (0.735 if "mixed_legacy" in model_file.name else (0.75 if "legacy" in model_file.name else 0.7)))),
            "model_file": model_file.name,
            "model_role": "percussive" if "groove" in model_file.name else ("legacy" if "legacy" in model_file.name else "mixed"),
        }

    monkeypatch.setattr(infer, "_infer_curve_from_model_file", fake_infer)
    monkeypatch.setattr(infer, "_routing_profile", lambda y, sr: {"percussive": 0.2, "legacy": 0.85, "mixed": 0.3})

    curves = infer_curve_candidates(
        np.zeros((4, 2), dtype=np.float32),
        np.zeros(2, dtype=np.float32),
        1.0,
        models_dir,
        y_mono=np.zeros(16, dtype=np.float32),
        sr=16000,
        bpm=120.0,
        resolution=8,
    )

    names = {curve["candidate_name"] for curve in curves}
    assert "boxbox_before_groove_860" in names
    assert "boxbox_latest" in names
    assert "boxbox_mixed_legacy_candidate_r1884" in names
    assert "boxbox_legacy_specialist_r1730" in names
    assert "boxbox_legacy_specialist_r1884_qm" in names
    assert "boxbox_legacy_specialist_r1884_qf" in names
    assert "routed" in names


def test_infer_curve_candidates_can_limit_model_count(monkeypatch, tmp_path: Path):
    models_dir = tmp_path
    for name in ("boxbox_latest.pt", "boxbox_mixed_legacy_candidate_r1884.pt", "boxbox_before_groove_860.pt"):
        (models_dir / name).write_bytes(b"x")
    calls = []

    def fake_infer(model_file: Path, *args, **kwargs):
        calls.append(model_file.name)
        return {
            "source_times": np.array([0.0, 1.0], dtype=np.float32),
            "target_times": np.array([0.0, 1.0], dtype=np.float32),
            "confidence": 0.8,
            "model_file": model_file.name,
            "model_role": "mixed",
        }

    monkeypatch.setattr(infer, "_infer_curve_from_model_file", fake_infer)

    curves = infer_curve_candidates(
        np.zeros((4, 2), dtype=np.float32),
        np.zeros(2, dtype=np.float32),
        1.0,
        models_dir,
        max_models=1,
    )

    assert calls == ["boxbox_latest.pt"]
    assert len(curves) == 1
    assert curves[0]["candidate_name"] == "boxbox_latest"


def test_infer_curve_candidates_reuse_prepared_features_for_same_feature_dim(monkeypatch, tmp_path: Path):
    for name in ("boxbox_latest.pt", "boxbox_mixed_legacy_candidate_r1884.pt", "boxbox_before_groove_860.pt"):
        (tmp_path / name).write_bytes(b"x")

    prepare_calls: list[int] = []

    class _FakeModel:
        def __call__(self, inp):
            return torch.linspace(0.0, 1.0, inp.shape[1], dtype=torch.float32).unsqueeze(0)

    def fake_load_model_bundle(model_file_str: str, device: str):
        return {}, 82, _FakeModel()

    def fake_prepare_input_features(*args, **kwargs):
        prepare_calls.append(int(kwargs.get("feature_dim") or args[3]))
        frame_count = int(args[0].shape[1])
        return np.zeros((frame_count, 82), dtype=np.float32)

    monkeypatch.setattr(infer, "_load_model_bundle", fake_load_model_bundle)
    monkeypatch.setattr(infer, "_prepare_input_features", fake_prepare_input_features)
    monkeypatch.setattr(infer, "infer_with_openvino", lambda *args, **kwargs: None)

    curves = infer_curve_candidates(
        np.zeros((80, 6), dtype=np.float32),
        np.zeros(6, dtype=np.float32),
        1.0,
        tmp_path,
        device="cpu",
        prefer_openvino=False,
        y_mono=np.zeros(16, dtype=np.float32),
        sr=16000,
        bpm=120.0,
        resolution=8,
    )

    assert len(curves) == 4
    assert prepare_calls == [82]


def test_filter_model_paths_for_core_strategies(tmp_path: Path):
    model_files = [
        tmp_path / "boxbox_latest.pt",
        tmp_path / "boxbox_mixed_legacy_candidate_r1884.pt",
        tmp_path / "boxbox_before_groove_860.pt",
        tmp_path / "boxbox_legacy_specialist_r1730.pt",
        tmp_path / "boxbox_legacy_specialist_r1884_qf.pt",
    ]

    core4 = [path.name for path in _filter_model_paths_for_strategy(model_files, "core4")]
    core5 = [path.name for path in _filter_model_paths_for_strategy(model_files, "core5")]

    assert core4 == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    ]
    assert core5 == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
        "boxbox_legacy_specialist_r1730.pt",
    ]


def test_preferred_inference_candidate_strategy_defaults_to_core4_adaptive_plus(monkeypatch):
    monkeypatch.delenv("BOXBOX_INFER_CANDIDATE_STRATEGY", raising=False)
    assert preferred_inference_candidate_strategy() == "core4_adaptive_plus"


def test_adaptive_core4_model_paths_adds_legacy_specialist_for_legacyish_profiles(tmp_path: Path):
    model_files = [
        tmp_path / "boxbox_latest.pt",
        tmp_path / "boxbox_mixed_legacy_candidate_r1884.pt",
        tmp_path / "boxbox_before_groove_860.pt",
        tmp_path / "boxbox_legacy_specialist_r1730.pt",
    ]

    plain = [
        path.name
        for path in _adaptive_core4_model_paths(
            model_files,
            {"percussive": 0.55, "legacy": 0.1, "mixed": 0.8},
        )
    ]
    legacyish = [
        path.name
        for path in _adaptive_core4_model_paths(
            model_files,
            {"percussive": 0.18, "legacy": 0.78, "mixed": 0.62},
        )
    ]
    mixed_legacyish = [
        path.name
        for path in _adaptive_core4_model_paths(
            model_files,
            {"percussive": 0.45, "legacy": 0.25, "mixed": 0.99},
        )
    ]

    assert plain == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    ]
    assert legacyish == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
        "boxbox_legacy_specialist_r1730.pt",
    ]
    assert mixed_legacyish == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
        "boxbox_legacy_specialist_r1730.pt",
    ]


def test_adaptive_core4_plus_model_paths_adds_full_legacy_trio_when_needed(tmp_path: Path):
    model_files = [
        tmp_path / "boxbox_latest.pt",
        tmp_path / "boxbox_mixed_legacy_candidate_r1884.pt",
        tmp_path / "boxbox_before_groove_860.pt",
        tmp_path / "boxbox_legacy_specialist_r1730.pt",
        tmp_path / "boxbox_legacy_specialist_r1884_qm.pt",
        tmp_path / "boxbox_legacy_specialist_r1884_qf.pt",
    ]

    base = [
        path.name
        for path in _adaptive_core4_plus_model_paths(
            model_files,
            {"percussive": 0.55, "legacy": 0.1, "mixed": 0.8},
        )
    ]
    legacyish = [
        path.name
        for path in _adaptive_core4_plus_model_paths(
            model_files,
            {"percussive": 0.18, "legacy": 0.78, "mixed": 0.62},
        )
    ]
    mixed_legacyish = [
        path.name
        for path in _adaptive_core4_plus_model_paths(
            model_files,
            {"percussive": 0.45, "legacy": 0.25, "mixed": 0.99},
        )
    ]
    assert base == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    ]
    assert legacyish == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
        "boxbox_legacy_specialist_r1730.pt",
        "boxbox_legacy_specialist_r1884_qm.pt",
        "boxbox_legacy_specialist_r1884_qf.pt",
    ]
    assert mixed_legacyish == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
        "boxbox_legacy_specialist_r1730.pt",
        "boxbox_legacy_specialist_r1884_qm.pt",
        "boxbox_legacy_specialist_r1884_qf.pt",
    ]


def test_adaptive_core4_plus_qf_model_paths_adds_legacy_pair_when_needed(tmp_path: Path):
    model_files = [
        tmp_path / "boxbox_latest.pt",
        tmp_path / "boxbox_mixed_legacy_candidate_r1884.pt",
        tmp_path / "boxbox_before_groove_860.pt",
        tmp_path / "boxbox_legacy_specialist_r1730.pt",
        tmp_path / "boxbox_legacy_specialist_r1884_qm.pt",
        tmp_path / "boxbox_legacy_specialist_r1884_qf.pt",
    ]

    base = [
        path.name
        for path in _adaptive_core4_plus_qf_model_paths(
            model_files,
            {"percussive": 0.55, "legacy": 0.1, "mixed": 0.8},
        )
    ]
    legacyish = [
        path.name
        for path in _adaptive_core4_plus_qf_model_paths(
            model_files,
            {"percussive": 0.18, "legacy": 0.78, "mixed": 0.62},
        )
    ]

    assert base == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
    ]
    assert legacyish == [
        "boxbox_latest.pt",
        "boxbox_mixed_legacy_candidate_r1884.pt",
        "boxbox_before_groove_860.pt",
        "boxbox_legacy_specialist_r1730.pt",
        "boxbox_legacy_specialist_r1884_qf.pt",
    ]


def test_infer_curve_from_model_file_prefers_openvino_when_available(monkeypatch, tmp_path: Path):
    model_path = tmp_path / "boxbox_latest.pt"
    model_path.write_bytes(b"x")
    monkeypatch.setenv("BOXBOX_INFER_ACCELERATOR", "auto")

    monkeypatch.setattr(
        infer,
        "_load_model_bundle",
        lambda model_file_str, device: ({}, 4, None),
    )
    monkeypatch.setattr(
        infer,
        "infer_with_openvino",
        lambda model_file, features, mask, ov_device=None: (np.linspace(0.0, 1.0, features.shape[1], dtype=np.float32)[None, :], "NPU"),
    )

    curve = infer._infer_curve_from_model_file(
        model_path,
        np.zeros((2, 8), dtype=np.float32),
        np.zeros(8, dtype=np.float32),
        1.0,
    )

    assert curve is not None
    assert curve["accelerator"] == "NPU"


def test_infer_curve_from_model_file_skips_openvino_for_interactive_auto(monkeypatch, tmp_path: Path):
    model_path = tmp_path / "boxbox_latest.pt"
    model_path.write_bytes(b"x")

    class DummyModel:
        def __call__(self, inp):
            seq_len = int(inp.shape[1])
            return torch.tensor(np.linspace(0.0, 1.0, seq_len, dtype=np.float32)[None, :])

    monkeypatch.setattr(
        infer,
        "_load_model_bundle",
        lambda model_file_str, device: ({}, 4, DummyModel()),
    )
    monkeypatch.setattr(infer, "resolve_device", lambda device: "cpu")
    monkeypatch.setattr(infer, "autocast_context", lambda device: __import__("contextlib").nullcontext())

    def fail_openvino(*args, **kwargs):
        raise AssertionError("OpenVINO should not run on interactive auto inference")

    monkeypatch.setattr(infer, "infer_with_openvino", fail_openvino)

    curve = infer._infer_curve_from_model_file(
        model_path,
        np.zeros((2, 8), dtype=np.float32),
        np.zeros(8, dtype=np.float32),
        1.0,
        prefer_openvino=False,
    )

    assert curve is not None
    assert curve["accelerator"] == "torch"
