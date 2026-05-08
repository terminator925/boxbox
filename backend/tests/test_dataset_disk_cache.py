from __future__ import annotations

import numpy as np
from pathlib import Path

from backend.ml import dataset as ds
from backend.ml import dataset_inventory as inv
from backend.ml import train as train_mod


def test_build_warp_target_cached_to_disk_reuses_saved_payload(tmp_path: Path, monkeypatch):
    original = tmp_path / "original.wav"
    warped = tmp_path / "warped.wav"
    original.write_bytes(b"orig")
    warped.write_bytes(b"warp")
    cache_dir = tmp_path / "cache"

    calls = {"count": 0}

    def fake_build_warp_target(original_path: Path, warped_path: Path, max_frames: int = 2000):
        calls["count"] += 1
        return np.ones((4, 3), dtype=np.float32), np.zeros(4, dtype=np.float32)

    monkeypatch.setattr(ds, "build_warp_target", fake_build_warp_target)

    feat1, target1 = ds.build_warp_target_cached_to_disk(original, warped, cache_dir=cache_dir)
    feat2, target2 = ds.build_warp_target_cached_to_disk(original, warped, cache_dir=cache_dir)

    assert calls["count"] == 1
    assert np.array_equal(feat1, feat2)
    assert np.array_equal(target1, target2)
    assert len(list(cache_dir.glob("*.npz"))) == 1


def test_warp_dataset_accepts_multiple_roots(tmp_path: Path):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    ex_a = root_a / "groove_audio_0000"
    ex_b = root_b / "musicnet_audio_0000"
    ex_a.mkdir(parents=True)
    ex_b.mkdir(parents=True)
    (ex_a / "original.wav").write_bytes(b"a")
    (ex_a / "warped.wav").write_bytes(b"a")
    (ex_b / "original.wav").write_bytes(b"b")
    (ex_b / "warped.wav").write_bytes(b"b")

    dataset = ds.WarpDataset([root_a, root_b], split="all")

    assert len(dataset.items) == 2
    names = {item[0].parent.name for item in dataset.items}
    assert names == {"groove_audio_0000", "musicnet_audio_0000"}


def test_dataset_family_from_dir_strips_numeric_suffix():
    assert ds.dataset_family_from_dir(Path("asap_0000")) == "asap"
    assert ds.dataset_family_from_dir(Path("synthetic_0001")) == "synthetic"
    assert ds.dataset_family_from_dir(Path("musicnet_audio_0002")) == "musicnet_audio"


def test_warp_dataset_filters_by_dataset_prefix(tmp_path: Path):
    root = tmp_path / "examples"
    keep = root / "groove_audio_0000"
    skip = root / "synthetic_0000"
    keep.mkdir(parents=True)
    skip.mkdir(parents=True)
    for path in (keep, skip):
        (path / "original.wav").write_bytes(b"x")
        (path / "warped.wav").write_bytes(b"y")

    dataset = ds.WarpDataset(root, split="all", dataset_filters="groove_audio,musicnet_audio")

    assert len(dataset.items) == 1
    assert dataset.items[0][0].parent.name == "groove_audio_0000"


def test_dataset_family_weights_favor_underrepresented_prefix(tmp_path: Path):
    root = tmp_path / "examples"
    for name in ("groove_audio_0000", "groove_audio_0001", "musicnet_audio_0000"):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")

    dataset = ds.WarpDataset(root, split="all")
    weights = ds.dataset_sample_weights(dataset, mode="inverse")

    family_to_weight = {family: weight for family, weight in zip(dataset.dataset_families, weights)}
    assert family_to_weight["musicnet_audio"] > family_to_weight["groove_audio"]


def test_dataset_family_weight_overrides_apply_multiplier(tmp_path: Path):
    root = tmp_path / "examples"
    for name in ("groove_audio_0000", "musicnet_audio_0000"):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")

    dataset = ds.WarpDataset(root, split="all")
    weights = ds.dataset_sample_weights(
        dataset,
        mode="uniform",
        weight_overrides="legacy_audio=3,musicnet_audio=2,groove_audio=0.5",
    )

    family_to_weight = {family: weight for family, weight in zip(dataset.dataset_families, weights)}
    assert family_to_weight["musicnet_audio"] == 2.0
    assert family_to_weight["groove_audio"] == 0.5


def test_warp_dataset_can_filter_by_meta_quality(tmp_path: Path):
    root = tmp_path / "examples"
    keep = root / "legacy_audio_0000"
    skip = root / "legacy_audio_0001"
    keep.mkdir(parents=True)
    skip.mkdir(parents=True)
    for path in (keep, skip):
        (path / "original.wav").write_bytes(b"x")
        (path / "warped.wav").write_bytes(b"y")
    (keep / "meta.json").write_text(
        '{"event_count": 120, "timing_metrics": {"improvement_pct": 42.0, "avg_abs_error_after_sec": 0.03}}',
        encoding="utf-8",
    )
    (skip / "meta.json").write_text(
        '{"event_count": 40, "timing_metrics": {"improvement_pct": 12.0, "avg_abs_error_after_sec": 0.09}}',
        encoding="utf-8",
    )

    dataset = ds.WarpDataset(
        root,
        split="all",
        dataset_filters="legacy_audio",
        min_improvement_pct=20.0,
        max_after_sec=0.05,
        min_event_count=80,
    )

    assert len(dataset.items) == 1
    assert dataset.items[0][0].parent.name == "legacy_audio_0000"


def test_warp_dataset_can_limit_examples_deterministically(tmp_path: Path):
    root = tmp_path / "examples"
    for name in ("legacy_audio_0000", "legacy_audio_0001", "legacy_audio_0002"):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")

    dataset = ds.WarpDataset(root, split="all", dataset_filters="legacy_audio", max_examples=2)

    assert len(dataset.items) == 2
    assert [item[0].parent.name for item in dataset.items] == ["legacy_audio_0000", "legacy_audio_0001"]


def test_warp_dataset_limits_examples_across_families(tmp_path: Path):
    root = tmp_path / "examples"
    for name in (
        "asap_0000",
        "asap_0001",
        "asap_0002",
        "legacy_audio_0000",
        "legacy_audio_0001",
        "legacy_audio_0002",
        "musicnet_audio_0000",
        "musicnet_audio_0001",
    ):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")

    dataset = ds.WarpDataset(root, split="all", max_examples=5)

    assert len(dataset.items) == 5
    assert dataset.dataset_families == ["asap", "legacy_audio", "musicnet_audio", "asap", "legacy_audio"]


def test_dataset_summary_counts_families(tmp_path: Path):
    root = tmp_path / "examples"
    for name in ("legacy_audio_0000", "legacy_audio_0001", "groove_audio_0000"):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")

    dataset = ds.WarpDataset(root, split="all")

    summary = train_mod.dataset_summary(dataset)

    assert summary["examples"] == 3
    assert summary["families"] == {"groove_audio": 1, "legacy_audio": 2}
    assert summary["source_mix"]["by_source_group"] == {"real": 3}
    assert summary["source_mix"]["real_pct"] == 100.0


def test_train_dry_run_prints_dataset_summary_without_loading_audio(tmp_path: Path, capsys):
    root = tmp_path / "examples"
    for name in ("legacy_audio_0000", "legacy_audio_0001", "groove_audio_0000"):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"not a real wav")
        (ex / "warped.wav").write_bytes(b"not a real wav")

    train_mod.train(
        root,
        tmp_path / "model.pt",
        device="cpu",
        val_ratio=0.0,
        dataset_filters="legacy_audio",
        max_examples=1,
        dry_run=True,
    )

    output = capsys.readouterr().out
    assert "train_examples=1" in output
    assert 'train_families={"legacy_audio": 1}' in output
    assert 'train_source_group={"real": 1}' in output
    assert 'train_source_kind={"real_audio": 1}' in output
    assert "train_real_pct=100.0" in output
    assert "val_examples=0" in output
    assert "val_generated_pct=0.0" in output
    assert "dataset_filters=legacy_audio" in output
    assert "max_examples=1" in output
    assert "dry_run=1" in output


def test_train_dry_run_can_write_manifest_without_loading_audio(tmp_path: Path):
    root = tmp_path / "examples"
    ex = root / "legacy_audio_0000"
    ex.mkdir(parents=True)
    (ex / "original.wav").write_bytes(b"not a real wav")
    (ex / "warped.wav").write_bytes(b"not a real wav")
    manifest_path = tmp_path / "dry_run_manifest.json"

    train_mod.train(
        root,
        tmp_path / "model.pt",
        device="cpu",
        val_ratio=0.0,
        dry_run=True,
        manifest_output=manifest_path,
    )

    payload = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["status"] == "dry_run"
    assert payload["train_dataset"]["examples"] == 1
    assert payload["train_dataset"]["source_mix"]["real_pct"] == 100.0
    assert payload["val_dataset"]["examples"] == 0
    assert payload["epoch_losses"] == []
    assert payload["val_metrics"] == {}


def test_build_training_manifest_captures_config_and_metrics():
    manifest = train_mod.build_training_manifest(
        examples_dir="data/examples",
        output_model=Path("models/boxbox_latest.pt"),
        train_summary={"examples": 2, "families": {"asap": 2}},
        val_summary={"examples": 1, "families": {"asap": 1}},
        config={"epochs": 3, "dataset_balance": "inverse"},
        epoch_losses=[0.3, 0.2],
        val_metrics={"avg_mae": 0.1},
        status="trained",
        created_at="2026-04-21T00:00:00+00:00",
    )

    assert manifest["created_at"] == "2026-04-21T00:00:00+00:00"
    assert manifest["status"] == "trained"
    assert manifest["config"]["dataset_balance"] == "inverse"
    assert manifest["epoch_losses"] == [0.3, 0.2]
    assert manifest["val_metrics"] == {"avg_mae": 0.1}


def test_dataset_inventory_reports_generated_vs_real_mix(tmp_path: Path):
    root = tmp_path / "examples"
    synthetic = root / "synthetic_0000"
    musicnet = root / "musicnet_audio_0000"
    asap = root / "asap_0000"
    for ex in (synthetic, musicnet, asap):
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")
    (synthetic / "meta.json").write_text('{"dataset": "synthetic"}', encoding="utf-8")
    (musicnet / "meta.json").write_text(
        '{"dataset": "musicnet_audio", "source_member": "musicnet/train_data/1234.wav"}',
        encoding="utf-8",
    )
    (asap / "meta.json").write_text(
        '{"dataset": "asap", "performance_member": "asap/foo.mid", "score_member": "asap/score.mid"}',
        encoding="utf-8",
    )

    summary = inv.inventory_examples(root)

    assert summary["examples"] == 3
    assert summary["generated_examples"] == 2
    assert summary["real_examples"] == 1
    assert summary["generated_pct"] == 66.67
    assert summary["real_pct"] == 33.33
    assert summary["by_source_kind"] == {"midi_import": 1, "real_audio": 1, "synthetic": 1}


def test_dataset_inventory_applies_quality_filters(tmp_path: Path):
    root = tmp_path / "examples"
    keep = root / "legacy_audio_0000"
    skip = root / "legacy_audio_0001"
    for ex in (keep, skip):
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")
    (keep / "meta.json").write_text(
        '{"dataset": "legacy_audio", "event_count": 100, "timing_metrics": {"improvement_pct": 40.0, "avg_abs_error_after_sec": 0.02}}',
        encoding="utf-8",
    )
    (skip / "meta.json").write_text(
        '{"dataset": "legacy_audio", "event_count": 10, "timing_metrics": {"improvement_pct": 2.0, "avg_abs_error_after_sec": 0.2}}',
        encoding="utf-8",
    )

    summary = inv.inventory_examples(root, min_improvement_pct=10.0, max_after_sec=0.05, min_event_count=50)

    assert summary["examples"] == 1
    assert summary["skipped_quality"] == 1
    assert summary["by_family"] == {"legacy_audio": 1}


def test_dataset_inventory_max_examples_is_family_balanced(tmp_path: Path):
    root = tmp_path / "examples"
    for name in (
        "asap_0000",
        "asap_0001",
        "asap_0002",
        "musicnet_audio_0000",
        "musicnet_audio_0001",
        "musicnet_audio_0002",
    ):
        ex = root / name
        ex.mkdir(parents=True)
        (ex / "original.wav").write_bytes(b"x")
        (ex / "warped.wav").write_bytes(b"y")
    for ex in root.glob("musicnet_audio_*"):
        (ex / "meta.json").write_text(
            '{"dataset": "musicnet_audio", "source_member": "musicnet/train_data/1234.wav"}',
            encoding="utf-8",
        )
    for ex in root.glob("asap_*"):
        (ex / "meta.json").write_text('{"dataset": "asap", "performance_member": "foo.mid"}', encoding="utf-8")

    summary = inv.inventory_examples(root, max_examples=4)

    assert summary["examples"] == 4
    assert summary["by_family"] == {"asap": 2, "musicnet_audio": 2}
    assert summary["generated_pct"] == 50.0
    assert summary["real_pct"] == 50.0
