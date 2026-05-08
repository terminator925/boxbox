from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import app
from backend.audio.feedback import (
    build_best_next_pass,
    build_feedback_loop,
    build_segment_feedback,
    classify_segment_bucket,
    rank_next_pass_recommendations,
    summarize_recommendation_memory,
    summarize_position_feedback_memory,
    summarize_feedback_memory,
    summarize_segment_feedback,
    update_recommendation_outcome_memory,
    update_recommendation_memory,
    update_recommendation_rerun_memory,
)
from backend.config import OUTPUTS_DIR
from backend.services.storage import read_json


client = TestClient(app)


def test_feedback_memory_summarizes_style_bias_after_multiple_entries():
    memory = {
        "profiles": {
            "balanced": {
                "counts": {
                    "too_loose": 3,
                    "good": 1,
                }
            }
        }
    }

    learned = summarize_feedback_memory(memory, "balanced", 20)

    assert learned["evidence_count"] == 4
    assert learned["guided_groove_preserve"] < 20
    assert learned["preferred_mode"] == "hybrid"


def test_feedback_loop_emits_learned_default_after_enough_evidence():
    feedback_loop = build_feedback_loop(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        style_profile={"profile": "balanced", "confidence": 0.8},
        timing_metrics={"improvement_pct": 1.0},
        warp_selection="hybrid_candidate",
        ml_used=True,
        learned_behavior={
            "evidence_count": 3,
            "guided_groove_preserve": 14,
            "preferred_mode": "hybrid",
        },
    )

    assert feedback_loop["learned_default"] is not None
    assert feedback_loop["learned_default"]["suggested_controls"]["groove_preserve"] == 14
    assert feedback_loop["learned_default"]["suggested_controls"]["mode"] == "hybrid"
    assert feedback_loop["recommendation_rank"]["preferred_kind"] == "learned_default"


def test_feedback_loop_biases_learned_default_controls_when_trusted_history_is_strong():
    feedback_loop = build_feedback_loop(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        style_profile={"profile": "balanced", "confidence": 0.8},
        timing_metrics={"improvement_pct": 1.0},
        warp_selection="hybrid_candidate",
        ml_used=True,
        learned_behavior={
            "evidence_count": 4,
            "guided_groove_preserve": 14,
            "preferred_mode": "hybrid",
            "confidence": 0.58,
        },
        recommendation_behavior={
            "selection_biases": {"learned_default": 0.04, "best_next_pass": -0.04},
            "outcome_biases": {"learned_default": 0.08, "best_next_pass": -0.08},
            "rerun_biases": {"learned_default": 0.02, "best_next_pass": -0.02},
            "adherence_biases": {"learned_default": 0.03, "best_next_pass": -0.03},
            "score_biases": {"learned_default": 0.17, "best_next_pass": -0.17},
            "total_selections": 6,
            "total_outcomes": 4,
            "total_reruns": 4,
            "control_bias_effectiveness": {
                "learned_default": {"tuned_match_rate": 0.9, "untuned_match_rate": 0.4},
                "best_next_pass": {"tuned_match_rate": 0.2, "untuned_match_rate": 0.5},
            },
        },
    )

    assert feedback_loop["learned_default"] is not None
    assert feedback_loop["learned_default"]["suggested_controls"]["groove_preserve"] == 8
    assert feedback_loop["learned_default"]["control_bias"]["primary_driver"] == "outcome"
    assert feedback_loop["learned_default"]["control_bias"]["groove_delta"] == -6
    assert feedback_loop["learned_default"]["control_bias"]["effectiveness_delta"] == 0.5


def test_feedback_loop_leaves_learned_default_controls_alone_without_positive_recommendation_history():
    feedback_loop = build_feedback_loop(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        style_profile={"profile": "balanced", "confidence": 0.8},
        timing_metrics={"improvement_pct": 1.0},
        warp_selection="hybrid_candidate",
        ml_used=True,
        learned_behavior={
            "evidence_count": 4,
            "guided_groove_preserve": 14,
            "preferred_mode": "hybrid",
            "confidence": 0.58,
        },
        recommendation_behavior={
            "selection_biases": {"learned_default": -0.04, "best_next_pass": 0.04},
            "outcome_biases": {"learned_default": -0.08, "best_next_pass": 0.08},
            "rerun_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "adherence_biases": {"learned_default": -0.03, "best_next_pass": 0.03},
            "score_biases": {"learned_default": -0.15, "best_next_pass": 0.15},
            "total_selections": 6,
            "total_outcomes": 4,
            "total_reruns": 2,
        },
    )

    assert feedback_loop["learned_default"] is not None
    assert feedback_loop["learned_default"]["suggested_controls"]["groove_preserve"] == 14
    assert "control_bias" not in feedback_loop["learned_default"]


def test_build_segment_feedback_emits_focus_sections():
    segment_feedback = build_segment_feedback(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        ml_used=True,
        total_duration_sec=12.0,
        segment_summaries=[
            {
                "segment_index": 0,
                "start_sec": 0.0,
                "end_sec": 6.0,
                "duration_sec": 6.0,
                "delta_vs_baseline_sec": 0.002,
                "timing_metrics": {"avg_abs_error_after_sec": 0.05, "improvement_pct": 2.0},
            }
        ],
    )

    assert len(segment_feedback) == 1
    assert segment_feedback[0]["segment_index"] == 0
    assert segment_feedback[0]["time_range_label"] == "0:00-0:06"
    assert segment_feedback[0]["position_bucket"] == "early"
    assert segment_feedback[0]["quick_actions"][1]["suggested_controls"]["mode"] == "hybrid"


def test_build_segment_feedback_prioritizes_position_guidance():
    segment_feedback = build_segment_feedback(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        ml_used=True,
        total_duration_sec=20.0,
        position_guidance={
            "diagnostics": [
                {
                    "position_bucket": "late",
                    "dominant_feedback": "too_loose",
                    "message": "Late sections for this style tend to come back too loose.",
                    "suggested_controls": {"mode": "hybrid", "groove_preserve": 2},
                }
            ]
        },
        segment_summaries=[
            {
                "segment_index": 0,
                "start_sec": 2.0,
                "end_sec": 5.0,
                "duration_sec": 3.0,
                "delta_vs_baseline_sec": 0.003,
                "timing_metrics": {"avg_abs_error_after_sec": 0.07, "improvement_pct": 1.0},
            },
            {
                "segment_index": 1,
                "start_sec": 14.0,
                "end_sec": 18.0,
                "duration_sec": 4.0,
                "delta_vs_baseline_sec": 0.0,
                "timing_metrics": {"avg_abs_error_after_sec": 0.03, "improvement_pct": 10.0},
            },
        ],
    )

    assert segment_feedback[0]["segment_index"] == 1
    assert segment_feedback[0]["position_guidance"]["position_bucket"] == "late"
    assert "Historical feedback suggests late sections" in segment_feedback[0]["summary"]
    tighten_action = next(item for item in segment_feedback[0]["quick_actions"] if item["feedback"] == "too_loose")
    assert tighten_action["suggested_controls"]["mode"] == "hybrid"
    assert tighten_action["suggested_controls"]["groove_preserve"] == 0


def test_build_segment_feedback_biases_controls_for_warbly_guidance():
    segment_feedback = build_segment_feedback(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        ml_used=True,
        total_duration_sec=20.0,
        position_guidance={
            "diagnostics": [
                {
                    "position_bucket": "early",
                    "dominant_feedback": "warbly",
                    "message": "Early sections for this style tend to be artifact-prone.",
                    "suggested_controls": {"mode": "dtw", "groove_preserve": 32},
                }
            ]
        },
        segment_summaries=[
            {
                "segment_index": 0,
                "start_sec": 2.0,
                "end_sec": 5.0,
                "duration_sec": 3.0,
                "delta_vs_baseline_sec": 0.0,
                "timing_metrics": {"avg_abs_error_after_sec": 0.04, "improvement_pct": 12.0},
            }
        ],
    )

    tighten_action = next(item for item in segment_feedback[0]["quick_actions"] if item["feedback"] == "too_loose")
    artifact_action = next(item for item in segment_feedback[0]["quick_actions"] if item["feedback"] == "warbly")
    assert tighten_action["suggested_controls"]["mode"] == "dtw"
    assert tighten_action["suggested_controls"]["groove_preserve"] == 10
    assert artifact_action["suggested_controls"]["mode"] == "dtw"


def test_build_segment_feedback_biases_controls_for_too_tight_guidance():
    segment_feedback = build_segment_feedback(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        ml_used=True,
        total_duration_sec=20.0,
        position_guidance={
            "diagnostics": [
                {
                    "position_bucket": "late",
                    "dominant_feedback": "too_tight",
                    "message": "Late sections for this style tend to come back too tight.",
                    "suggested_controls": {"mode": "hybrid", "groove_preserve": 32},
                }
            ]
        },
        segment_summaries=[
            {
                "segment_index": 0,
                "start_sec": 14.0,
                "end_sec": 18.0,
                "duration_sec": 4.0,
                "delta_vs_baseline_sec": 0.0,
                "timing_metrics": {"avg_abs_error_after_sec": 0.03, "improvement_pct": 10.0},
            }
        ],
    )

    keep_action = next(item for item in segment_feedback[0]["quick_actions"] if item["feedback"] == "good")
    loosen_action = next(item for item in segment_feedback[0]["quick_actions"] if item["feedback"] == "too_tight")
    assert keep_action["suggested_controls"]["groove_preserve"] == 24
    assert loosen_action["suggested_controls"]["groove_preserve"] == 40


def test_build_best_next_pass_uses_top_guided_section():
    recommendation = build_best_next_pass(
        [
            {
                "segment_index": 1,
                "label": "Segment 2",
                "time_range_label": "0:14-0:18",
                "position_guidance": {"dominant_feedback": "warbly", "message": "Late sections tend to be artifact-prone."},
                "quick_actions": [
                    {"feedback": "warbly", "suggested_controls": {"mode": "dtw", "groove_preserve": 32}},
                ],
            }
        ]
    )

    assert recommendation is not None
    assert recommendation["label"] == "Try Segment 2 Next"
    assert recommendation["feedback"] == "warbly"
    assert recommendation["suggested_controls"]["mode"] == "dtw"


def test_feedback_loop_biases_best_next_pass_controls_when_retry_history_is_trusted():
    feedback_loop = build_feedback_loop(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        style_profile={"profile": "balanced", "confidence": 0.8},
        timing_metrics={"improvement_pct": 1.0},
        warp_selection="hybrid_candidate",
        ml_used=True,
        recommendation_behavior={
            "selection_biases": {"learned_default": -0.04, "best_next_pass": 0.04},
            "outcome_biases": {"learned_default": -0.08, "best_next_pass": 0.08},
            "rerun_biases": {"learned_default": -0.02, "best_next_pass": 0.02},
            "adherence_biases": {"learned_default": -0.03, "best_next_pass": 0.03},
            "score_biases": {"learned_default": -0.17, "best_next_pass": 0.17},
            "total_selections": 6,
            "total_outcomes": 4,
            "total_reruns": 4,
            "control_bias_effectiveness": {
                "learned_default": {"tuned_match_rate": 0.3, "untuned_match_rate": 0.6},
                "best_next_pass": {"tuned_match_rate": 1.0, "untuned_match_rate": 0.2},
            },
        },
        best_next_pass={
            "label": "Try Segment 2 Next",
            "description": "Late sections tend to be artifact-prone.",
            "segment_index": 1,
            "time_range_label": "0:14-0:18",
            "feedback": "warbly",
            "confidence": 0.65,
            "suggested_controls": {"mode": "hybrid", "target_bpm": 100.0, "resolution": 8, "groove_preserve": 24},
        },
    )

    assert feedback_loop["best_next_pass"] is not None
    assert feedback_loop["best_next_pass"]["suggested_controls"]["mode"] == "dtw"
    assert feedback_loop["best_next_pass"]["suggested_controls"]["groove_preserve"] == 30
    assert feedback_loop["best_next_pass"]["control_bias"]["primary_driver"] == "outcome"
    assert feedback_loop["best_next_pass"]["control_bias"]["groove_delta"] == 6


def test_feedback_loop_keeps_best_next_pass_conservative_when_tuned_effectiveness_is_weaker_than_raw():
    feedback_loop = build_feedback_loop(
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        style_profile={"profile": "balanced", "confidence": 0.8},
        timing_metrics={"improvement_pct": 1.0},
        warp_selection="hybrid_candidate",
        ml_used=True,
        recommendation_behavior={
            "selection_biases": {"learned_default": -0.02, "best_next_pass": 0.02},
            "outcome_biases": {"learned_default": 0.0, "best_next_pass": 0.04},
            "rerun_biases": {"learned_default": 0.0, "best_next_pass": 0.01},
            "adherence_biases": {"learned_default": 0.0, "best_next_pass": 0.01},
            "score_biases": {"learned_default": -0.02, "best_next_pass": 0.08},
            "total_selections": 6,
            "total_outcomes": 4,
            "total_reruns": 4,
            "control_bias_effectiveness": {
                "learned_default": {"tuned_match_rate": 0.6, "untuned_match_rate": 0.6},
                "best_next_pass": {"tuned_match_rate": 0.2, "untuned_match_rate": 0.6},
            },
        },
        best_next_pass={
            "label": "Try Segment 2 Next",
            "description": "Late sections tend to be artifact-prone.",
            "segment_index": 1,
            "time_range_label": "0:14-0:18",
            "feedback": "warbly",
            "confidence": 0.65,
            "suggested_controls": {"mode": "hybrid", "target_bpm": 100.0, "resolution": 8, "groove_preserve": 24},
        },
    )

    assert feedback_loop["best_next_pass"] is not None
    assert feedback_loop["best_next_pass"]["suggested_controls"]["groove_preserve"] == 24
    assert "control_bias" not in feedback_loop["best_next_pass"]


def test_rank_next_pass_recommendations_prefers_stronger_signal():
    ranking = rank_next_pass_recommendations(
        {"label": "Use Learned Default", "confidence": 0.42, "suggested_controls": {"mode": "hybrid"}},
        {"confidence": 0.42},
        {"label": "Try Segment 2 Next", "confidence": 0.75, "suggested_controls": {"mode": "dtw"}},
    )

    assert ranking["preferred_kind"] == "best_next_pass"
    assert ranking["options"][0]["kind"] == "best_next_pass"
    assert "outranks Use Learned Default by 0.33" in ranking["summary"]


def test_update_recommendation_memory_tracks_selection_counts():
    updated = update_recommendation_memory({}, style_profile_name="balanced", recommendation_kind="best_next_pass")
    assert updated["profiles"]["balanced"]["selection_counts"]["best_next_pass"] == 1
    assert updated["total_selections"] == 1


def test_summarize_recommendation_memory_emits_style_bias():
    summary = summarize_recommendation_memory(
        {
            "profiles": {
                "balanced": {
                    "selection_counts": {
                        "learned_default": 1,
                        "best_next_pass": 4,
                    },
                    "total_selections": 5,
                }
            }
        },
        style_profile_name="balanced",
    )

    assert summary["preferred_kind"] == "best_next_pass"
    assert summary["score_biases"]["best_next_pass"] > 0
    assert summary["score_biases"]["learned_default"] < 0
    assert "strongest local retry" in summary["summary"]


def test_recommendation_outcome_memory_biases_toward_successful_follow_up():
    memory = update_recommendation_outcome_memory(
        {},
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        feedback="good",
    )
    memory = update_recommendation_outcome_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        feedback="good",
    )
    memory = update_recommendation_outcome_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="best_next_pass",
        feedback="warbly",
    )
    memory = update_recommendation_outcome_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="best_next_pass",
        feedback="too_tight",
    )

    summary = summarize_recommendation_memory(memory, style_profile_name="balanced")

    assert summary["outcome_biases"]["learned_default"] > 0
    assert summary["outcome_biases"]["best_next_pass"] < 0
    assert "better follow-up outcomes" in summary["summary"]


def test_recommendation_rerun_memory_tracks_iterated_recommendations():
    memory = update_recommendation_rerun_memory(
        {},
        style_profile_name="balanced",
        recommendation_kind="best_next_pass",
        matched_suggestion=True,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="best_next_pass",
        matched_suggestion=False,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=True,
    )

    summary = summarize_recommendation_memory(memory, style_profile_name="balanced")

    assert summary["rerun_counts"]["best_next_pass"] == 2
    assert summary["total_reruns"] == 3
    assert summary["rerun_biases"]["best_next_pass"] > 0
    assert "more likely to rerun" in summary["summary"]


def test_recommendation_adherence_memory_prefers_exactly_followed_option():
    memory = update_recommendation_rerun_memory(
        {},
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=True,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=True,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="best_next_pass",
        matched_suggestion=False,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="best_next_pass",
        matched_suggestion=False,
    )

    summary = summarize_recommendation_memory(memory, style_profile_name="balanced")

    assert summary["adherence_counts"]["learned_default"]["matched"] == 2
    assert summary["adherence_counts"]["best_next_pass"]["modified"] == 2
    assert summary["adherence_biases"]["learned_default"] > 0
    assert summary["adherence_biases"]["best_next_pass"] < 0
    assert "trusted as-is" in summary["summary"]


def test_recommendation_control_bias_effectiveness_tracks_tuned_vs_untuned_follow_rates():
    memory = update_recommendation_rerun_memory(
        {},
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=True,
        used_control_bias=True,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=True,
        used_control_bias=True,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=False,
        used_control_bias=False,
    )
    memory = update_recommendation_rerun_memory(
        memory,
        style_profile_name="balanced",
        recommendation_kind="learned_default",
        matched_suggestion=False,
        used_control_bias=False,
    )

    summary = summarize_recommendation_memory(memory, style_profile_name="balanced")

    assert summary["control_bias_counts"]["learned_default"]["tuned"] == 2
    assert summary["control_bias_counts"]["learned_default"]["untuned"] == 2
    assert summary["control_adherence_counts"]["learned_default"]["tuned_matched"] == 2
    assert summary["control_adherence_counts"]["learned_default"]["untuned_modified"] == 2
    assert summary["control_bias_effectiveness"]["learned_default"]["tuned_match_rate"] == 1.0
    assert summary["control_bias_effectiveness"]["learned_default"]["untuned_match_rate"] == 0.0
    assert summary["effectiveness_biases"]["learned_default"] > 0
    assert "Tuned whole-track defaults are being followed as-is more often" in summary["summary"]


def test_summarize_segment_feedback_emits_repeat_issue_guidance():
    summary = summarize_segment_feedback(
        [
            {
                "segment_index": 0,
                "label": "Segment 1",
                "time_range_label": "0:00-0:06",
            }
        ],
        [
            {"scope": "segment", "segment_index": 0, "feedback": "too_tight", "suggested_controls": {"groove_preserve": 30}},
            {"scope": "segment", "segment_index": 0, "feedback": "too_tight", "suggested_controls": {"groove_preserve": 32}},
        ],
    )

    assert summary["summary"] == "Segment 1 is the clearest repeat issue so far."
    assert summary["diagnostics"][0]["dominant_feedback"] == "too_tight"
    assert summary["rerun_focus"][0]["suggested_controls"]["groove_preserve"] == 32


def test_classify_segment_bucket_uses_song_position():
    assert classify_segment_bucket(0.0, 4.0, 40.0) == "intro"
    assert classify_segment_bucket(8.0, 12.0, 40.0) == "early"
    assert classify_segment_bucket(18.0, 22.0, 40.0) == "middle"
    assert classify_segment_bucket(28.0, 32.0, 40.0) == "late"
    assert classify_segment_bucket(36.0, 40.0, 40.0) == "outro"


def test_summarize_position_feedback_memory_emits_bucket_guidance():
    guidance = summarize_position_feedback_memory(
        {
            "profiles": {
                "balanced": {
                    "buckets": {
                        "intro": {"counts": {"warbly": 2}},
                    }
                }
            }
        },
        style_profile_name="balanced",
        segment_feedback=[{"position_bucket": "intro"}],
        mode_requested="hybrid",
        target_bpm=100.0,
        resolution=8,
        groove_preserve=20,
        effective_groove_preserve=12,
        ml_used=True,
    )

    assert guidance["summary"] == "Intro sections for this style tend to be artifact-prone."
    assert guidance["diagnostics"][0]["position_bucket"] == "intro"
    assert guidance["diagnostics"][0]["suggested_controls"]["mode"] == "dtw"


def test_feedback_submission_records_latest_entry(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    stereo = np.stack([0.2 * np.sin(2 * np.pi * 220 * t), 0.2 * np.sin(2 * np.pi * 224 * t)], axis=1).astype(np.float32)

    source = tmp_path / "feedback.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("feedback.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    memory_before = read_json(OUTPUTS_DIR / "feedback_memory.json", default={})
    before_total = int(memory_before.get("total_entries", 0))

    feedback = client.post(
        "/api/feedback",
        json={"job_id": job_id, "feedback": "too_loose", "notes": "Needs a harder pull to the grid"},
    )
    assert feedback.status_code == 200
    payload = feedback.json()
    assert payload["status"] == "recorded"
    assert payload["entry"]["feedback"] == "too_loose"
    assert payload["entry"]["suggested_controls"]["groove_preserve"] <= 20

    report = read_json(Path("outputs") / job_id / "report.json")
    assert report["feedback_loop"]["history_count"] == 1
    assert report["feedback_loop"]["latest"]["feedback"] == "too_loose"
    assert "learned_behavior" in report["feedback_loop"]

    feedback_store = read_json(Path("outputs") / job_id / "feedback.json")
    assert len(feedback_store["entries"]) == 1

    memory_after = read_json(OUTPUTS_DIR / "feedback_memory.json", default={})
    assert int(memory_after.get("total_entries", 0)) >= before_total + 1


def test_segment_feedback_submission_records_segment_entry_without_learning_update(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "segment_feedback.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("segment_feedback.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    report_before = read_json(Path("outputs") / job_id / "report.json")
    segment_feedback = list((report_before.get("feedback_loop") or {}).get("segment_feedback") or [])
    assert segment_feedback
    segment_index = int(segment_feedback[0]["segment_index"])

    memory_before = read_json(OUTPUTS_DIR / "feedback_memory.json", default={})

    feedback = client.post(
        "/api/feedback",
        json={"job_id": job_id, "feedback": "too_tight", "notes": "intro should breathe more", "segment_index": segment_index},
    )
    assert feedback.status_code == 200
    payload = feedback.json()
    assert payload["entry"]["scope"] == "segment"
    assert payload["entry"]["segment_index"] == segment_index

    report_after = read_json(Path("outputs") / job_id / "report.json")
    updated_segment = next(
        item for item in report_after["feedback_loop"]["segment_feedback"] if int(item["segment_index"]) == segment_index
    )
    assert updated_segment["latest"]["feedback"] == "too_tight"
    assert updated_segment["history_count"] == 1
    assert report_after["feedback_loop"]["segment_feedback_summary"]["diagnostics"] == []

    memory_after = read_json(OUTPUTS_DIR / "feedback_memory.json", default={})
    assert int(memory_after.get("total_entries", 0)) == int(memory_before.get("total_entries", 0))


def test_repeated_segment_feedback_creates_report_side_diagnostic(tmp_path: Path):
    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "segment_feedback_repeat.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("segment_feedback_repeat.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    report_before = read_json(Path("outputs") / job_id / "report.json")
    segment_index = int(report_before["feedback_loop"]["segment_feedback"][0]["segment_index"])

    for _ in range(2):
        feedback = client.post(
            "/api/feedback",
            json={"job_id": job_id, "feedback": "warbly", "notes": "", "segment_index": segment_index},
        )
        assert feedback.status_code == 200

    report_after = read_json(Path("outputs") / job_id / "report.json")
    diagnostics = report_after["feedback_loop"]["segment_feedback_summary"]["diagnostics"]
    assert diagnostics
    assert diagnostics[0]["dominant_feedback"] == "warbly"
    assert diagnostics[0]["segment_index"] == segment_index


def test_repeated_segment_feedback_updates_position_guidance(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "_segment_feedback_memory_path", tmp_path / "segment_feedback_memory.json")
    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "segment_feedback_position.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("segment_feedback_position.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    report_before = read_json(Path("outputs") / job_id / "report.json")
    segment = report_before["feedback_loop"]["segment_feedback"][0]
    segment_index = int(segment["segment_index"])
    position_bucket = str(segment["position_bucket"])

    for _ in range(2):
        feedback = client.post(
            "/api/feedback",
            json={"job_id": job_id, "feedback": "too_loose", "notes": "", "segment_index": segment_index},
        )
        assert feedback.status_code == 200

    report_after = read_json(Path("outputs") / job_id / "report.json")
    position_guidance = report_after["feedback_loop"]["position_guidance"]["diagnostics"]
    assert position_guidance
    assert position_guidance[0]["position_bucket"] == position_bucket
    assert position_guidance[0]["dominant_feedback"] == "too_loose"
    assert position_guidance[0]["suggested_controls"]["mode"] == "hybrid"


def test_new_report_uses_existing_position_guidance_for_initial_section_priority(tmp_path: Path, monkeypatch):
    memory_path = tmp_path / "segment_feedback_memory.json"
    monkeypatch.setattr(app_module, "_segment_feedback_memory_path", memory_path)
    memory_path.write_text(
        '{"profiles":{"balanced":{"buckets":{"early":{"counts":{"warbly":2}}}}},"total_entries":2}',
        encoding="utf-8",
    )

    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "position_guidance_seeded.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("position_guidance_seeded.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    report = read_json(Path("outputs") / job_id / "report.json")
    assert report["feedback_loop"]["position_guidance"]["diagnostics"]
    guided_sections = [item for item in report["feedback_loop"]["segment_feedback"] if item.get("position_guidance")]
    assert guided_sections
    assert "Historical feedback suggests" in guided_sections[0]["summary"]
    assert report["feedback_loop"]["best_next_pass"] is not None
    assert report["feedback_loop"]["best_next_pass"]["feedback"] == "warbly"
    assert report["feedback_loop"]["recommendation_rank"]["preferred_kind"] == "best_next_pass"


def test_recommendation_selection_records_choice(tmp_path: Path, monkeypatch):
    recommendation_memory_path = tmp_path / "recommendation_memory.json"
    monkeypatch.setattr(app_module, "_recommendation_memory_path", recommendation_memory_path)
    memory_path = tmp_path / "segment_feedback_memory.json"
    monkeypatch.setattr(app_module, "_segment_feedback_memory_path", memory_path)
    memory_path.write_text(
        '{"profiles":{"balanced":{"buckets":{"early":{"counts":{"warbly":2}}}}},"total_entries":2}',
        encoding="utf-8",
    )
    recommendation_memory_path.write_text(
        (
            '{"profiles":{"balanced":{"selection_counts":{"best_next_pass":4,"learned_default":1},'
            '"outcome_counts":{"best_next_pass":{"good":2},"learned_default":{"too_tight":2}},'
            '"rerun_counts":{"best_next_pass":2,"learned_default":0},'
            '"adherence_counts":{"best_next_pass":{"matched":2},"learned_default":{"modified":2}},'
            '"total_selections":5,"total_outcomes":4,"total_reruns":2}}}'
        ),
        encoding="utf-8",
    )

    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "recommendation_selection.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("recommendation_selection.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    selection = client.post(
        "/api/recommendation-selection",
        json={"job_id": job_id, "recommendation_kind": "best_next_pass"},
    )
    assert selection.status_code == 200
    payload = selection.json()
    assert payload["entry"]["recommendation_kind"] == "best_next_pass"
    assert payload["entry"]["control_bias"]["applied"] is True

    selection_store = read_json(Path("outputs") / job_id / "recommendation_selection.json")
    assert len(selection_store["entries"]) == 1
    assert selection_store["entries"][0]["control_bias"]["applied"] is True

    recommendation_memory = read_json(tmp_path / "recommendation_memory.json")
    assert recommendation_memory["profiles"]["balanced"]["selection_counts"]["best_next_pass"] == 5


def test_rank_next_pass_recommendations_uses_selection_history_to_break_close_races():
    recommendation_behavior = summarize_recommendation_memory(
        {
            "profiles": {
                "balanced": {
                    "selection_counts": {
                        "learned_default": 1,
                        "best_next_pass": 5,
                    },
                    "total_selections": 6,
                }
            }
        },
        style_profile_name="balanced",
    )

    ranking = rank_next_pass_recommendations(
        {"label": "Use Learned Default", "confidence": 0.54, "suggested_controls": {"mode": "hybrid"}},
        {"confidence": 0.54},
        {"label": "Try Segment 2 Next", "confidence": 0.50, "suggested_controls": {"mode": "dtw"}},
        recommendation_behavior,
    )

    assert ranking["preferred_kind"] == "best_next_pass"
    assert ranking["options"][0]["kind"] == "best_next_pass"
    assert ranking["options"][0]["memory_bias"] > 0
    assert "operator selections for this style" in ranking["options"][0]["reason"]


def test_rank_next_pass_recommendations_mentions_stronger_outcome_track_record():
    ranking = rank_next_pass_recommendations(
        {"label": "Use Learned Default", "confidence": 0.56, "suggested_controls": {"mode": "hybrid"}},
        {"confidence": 0.56},
        {"label": "Try Segment 2 Next", "confidence": 0.52, "suggested_controls": {"mode": "dtw"}},
        {
            "selection_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "outcome_biases": {"learned_default": 0.08, "best_next_pass": -0.08},
            "score_biases": {"learned_default": 0.08, "best_next_pass": -0.08},
        },
    )

    assert ranking["preferred_kind"] == "learned_default"
    assert "stronger follow-up track record for this style" in ranking["summary"]


def test_rank_next_pass_recommendations_mentions_stronger_adherence_track_record():
    ranking = rank_next_pass_recommendations(
        {"label": "Use Learned Default", "confidence": 0.56, "suggested_controls": {"mode": "hybrid"}},
        {"confidence": 0.56},
        {"label": "Try Segment 2 Next", "confidence": 0.52, "suggested_controls": {"mode": "dtw"}},
        {
            "selection_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "outcome_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "rerun_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "adherence_biases": {"learned_default": 0.03, "best_next_pass": -0.03},
            "score_biases": {"learned_default": 0.03, "best_next_pass": -0.03},
        },
    )

    assert ranking["preferred_kind"] == "learned_default"
    assert "more often trusted as-is for this style" in ranking["summary"]
    assert "trusting it as-is for this style" in ranking["options"][0]["reason"]


def test_rank_next_pass_recommendations_uses_effectiveness_bias_to_break_close_races():
    ranking = rank_next_pass_recommendations(
        {"label": "Use Learned Default", "confidence": 0.55, "suggested_controls": {"mode": "hybrid"}},
        {"confidence": 0.55},
        {"label": "Try Segment 2 Next", "confidence": 0.54, "suggested_controls": {"mode": "dtw"}},
        {
            "selection_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "outcome_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "rerun_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "adherence_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "effectiveness_biases": {"learned_default": -0.02, "best_next_pass": 0.04},
            "score_biases": {"learned_default": -0.02, "best_next_pass": 0.04},
        },
    )

    assert ranking["preferred_kind"] == "best_next_pass"
    assert ranking["options"][0]["kind"] == "best_next_pass"
    assert "adaptive tuning has held up better than the raw version" in ranking["options"][0]["reason"]


def test_rank_next_pass_recommendations_mentions_stronger_effectiveness_track_record():
    ranking = rank_next_pass_recommendations(
        {"label": "Use Learned Default", "confidence": 0.56, "suggested_controls": {"mode": "hybrid"}},
        {"confidence": 0.56},
        {"label": "Try Segment 2 Next", "confidence": 0.52, "suggested_controls": {"mode": "dtw"}},
        {
            "selection_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "outcome_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "rerun_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "adherence_biases": {"learned_default": 0.0, "best_next_pass": 0.0},
            "effectiveness_biases": {"learned_default": 0.04, "best_next_pass": -0.02},
            "score_biases": {"learned_default": 0.04, "best_next_pass": -0.02},
        },
    )

    assert ranking["preferred_kind"] == "learned_default"
    assert "adaptive tuning has also proven more trustworthy" in ranking["summary"]


def test_global_feedback_attributes_outcome_to_latest_recommendation_selection(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(app_module, "_recommendation_memory_path", tmp_path / "recommendation_memory.json")
    memory_path = tmp_path / "segment_feedback_memory.json"
    monkeypatch.setattr(app_module, "_segment_feedback_memory_path", memory_path)
    memory_path.write_text(
        '{"profiles":{"balanced":{"buckets":{"early":{"counts":{"warbly":2}}}}},"total_entries":2}',
        encoding="utf-8",
    )

    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "recommendation_outcome.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("recommendation_outcome.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    selection = client.post(
        "/api/recommendation-selection",
        json={"job_id": job_id, "recommendation_kind": "best_next_pass"},
    )
    assert selection.status_code == 200

    feedback = client.post(
        "/api/feedback",
        json={"job_id": job_id, "feedback": "good", "notes": "worked well"},
    )
    assert feedback.status_code == 200

    selection_store = read_json(Path("outputs") / job_id / "recommendation_selection.json")
    assert selection_store["entries"][0]["outcome_feedback"] == "good"

    recommendation_memory = read_json(tmp_path / "recommendation_memory.json")
    assert recommendation_memory["profiles"]["balanced"]["outcome_counts"]["best_next_pass"]["good"] == 1

    report = read_json(Path("outputs") / job_id / "report.json")
    assert report["feedback_loop"]["latest_recommendation_outcome"]["recommendation_kind"] == "best_next_pass"
    assert report["feedback_loop"]["latest_recommendation_outcome"]["feedback"] == "good"


def test_successful_second_quantize_marks_recommendation_as_rerun(tmp_path: Path, monkeypatch):
    recommendation_memory_path = tmp_path / "recommendation_memory.json"
    monkeypatch.setattr(app_module, "_recommendation_memory_path", recommendation_memory_path)
    memory_path = tmp_path / "segment_feedback_memory.json"
    monkeypatch.setattr(app_module, "_segment_feedback_memory_path", memory_path)
    memory_path.write_text(
        '{"profiles":{"balanced":{"buckets":{"early":{"counts":{"warbly":2}}}}},"total_entries":2}',
        encoding="utf-8",
    )
    recommendation_memory_path.write_text(
        (
            '{"profiles":{"balanced":{"selection_counts":{"best_next_pass":4,"learned_default":1},'
            '"outcome_counts":{"best_next_pass":{"good":2},"learned_default":{"too_tight":2}},'
            '"rerun_counts":{"best_next_pass":2,"learned_default":0},'
            '"adherence_counts":{"best_next_pass":{"matched":2},"learned_default":{"modified":2}},'
            '"total_selections":5,"total_outcomes":4,"total_reruns":2}}}'
        ),
        encoding="utf-8",
    )

    sr = 22050
    t = np.linspace(0, 6.0, int(sr * 6.0), endpoint=False)
    pulse = np.zeros_like(t, dtype=np.float32)
    for beat in np.arange(0.0, 6.0, 0.5):
        idx = int(beat * sr)
        pulse[idx : idx + 600] += np.hanning(min(600, len(pulse) - idx)).astype(np.float32) * 0.5
    stereo = np.stack([pulse, pulse], axis=1).astype(np.float32)

    source = tmp_path / "recommendation_rerun.wav"
    sf.write(str(source), stereo, sr)

    with source.open("rb") as handle:
        upload = client.post("/api/upload", files={"file": ("recommendation_rerun.wav", handle, "audio/wav")})
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    quantize = client.post(
        "/api/quantize",
        json={"job_id": job_id, "target_bpm": 100, "resolution": 8, "groove_preserve": 20, "mode": "hybrid"},
    )
    assert quantize.status_code == 200

    report = read_json(Path("outputs") / job_id / "report.json")
    best_next_pass = dict(report["feedback_loop"]["best_next_pass"])

    selection = client.post(
        "/api/recommendation-selection",
        json={"job_id": job_id, "recommendation_kind": "best_next_pass"},
    )
    assert selection.status_code == 200

    rerun = client.post(
        "/api/quantize",
        json={
            "job_id": job_id,
            "target_bpm": best_next_pass["suggested_controls"]["target_bpm"],
            "resolution": best_next_pass["suggested_controls"]["resolution"],
            "groove_preserve": best_next_pass["suggested_controls"]["groove_preserve"],
            "mode": best_next_pass["suggested_controls"]["mode"],
        },
    )
    assert rerun.status_code == 200

    selection_store = read_json(Path("outputs") / job_id / "recommendation_selection.json")
    assert selection_store["entries"][0]["rerun_recorded"] is True
    assert selection_store["entries"][0]["rerun_matched_suggestion"] is True
    assert selection_store["entries"][0]["control_bias"]["applied"] is True

    recommendation_memory = read_json(tmp_path / "recommendation_memory.json")
    assert recommendation_memory["profiles"]["balanced"]["rerun_counts"]["best_next_pass"] == 3
    assert recommendation_memory["profiles"]["balanced"]["adherence_counts"]["best_next_pass"]["matched"] == 3
    assert recommendation_memory["profiles"]["balanced"]["control_bias_counts"]["best_next_pass"]["tuned"] == 1
    assert recommendation_memory["profiles"]["balanced"]["control_adherence_counts"]["best_next_pass"]["tuned_matched"] == 1

    rerun_report = read_json(Path("outputs") / job_id / "report.json")
    assert rerun_report["feedback_loop"]["latest_recommendation_rerun"]["recommendation_kind"] == "best_next_pass"
    assert rerun_report["feedback_loop"]["latest_recommendation_rerun"]["matched_suggestion"] is True
    assert rerun_report["feedback_loop"]["latest_recommendation_rerun"]["used_control_bias"] is True
