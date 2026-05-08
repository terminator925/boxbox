from __future__ import annotations

from typing import Any


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _format_time_label(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def classify_segment_bucket(start_sec: float, end_sec: float, total_duration_sec: float) -> str:
    total = max(float(total_duration_sec), 1e-3)
    midpoint = max(0.0, min(total, (float(start_sec) + float(end_sec)) * 0.5))
    ratio = midpoint / total
    if ratio <= 0.12:
        return "intro"
    if ratio <= 0.35:
        return "early"
    if ratio < 0.65:
        return "middle"
    if ratio < 0.88:
        return "late"
    return "outro"


def _base_suggested_controls(
    *,
    mode_requested: str,
    target_bpm: float,
    resolution: int,
    groove_preserve: int,
    effective_groove_preserve: int,
    ml_used: bool,
) -> dict[str, dict[str, Any]]:
    next_mode = "hybrid" if ml_used else "dtw"
    tighten_groove = _clamp_int(min(groove_preserve, effective_groove_preserve) - 10, 0, 100)
    loosen_groove = _clamp_int(max(groove_preserve, effective_groove_preserve) + 12, 0, 100)
    return {
        "good": {
            "mode": mode_requested,
            "target_bpm": float(target_bpm),
            "resolution": int(resolution),
            "groove_preserve": int(groove_preserve),
        },
        "too_loose": {
            "mode": next_mode,
            "target_bpm": float(target_bpm),
            "resolution": int(resolution),
            "groove_preserve": int(tighten_groove),
        },
        "too_tight": {
            "mode": mode_requested,
            "target_bpm": float(target_bpm),
            "resolution": int(resolution),
            "groove_preserve": int(loosen_groove),
        },
        "warbly": {
            "mode": "dtw",
            "target_bpm": float(target_bpm),
            "resolution": int(resolution),
            "groove_preserve": int(max(loosen_groove, 20)),
        },
    }


def _position_guidance_lookup(position_guidance: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for item in list((position_guidance or {}).get("diagnostics") or []):
        bucket = str(item.get("position_bucket") or "").strip()
        if bucket:
            lookup[bucket] = dict(item)
    return lookup


def _bias_segment_controls(
    controls: dict[str, dict[str, Any]],
    guidance: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    biased = {key: dict(value) for key, value in controls.items()}
    dominant_feedback = str((guidance or {}).get("dominant_feedback") or "")
    if dominant_feedback == "warbly":
        biased["warbly"]["mode"] = "dtw"
        biased["too_loose"]["mode"] = "dtw"
        biased["too_loose"]["groove_preserve"] = _clamp_int(int(biased["too_loose"].get("groove_preserve", 0)) + 8, 0, 100)
    elif dominant_feedback == "too_loose":
        biased["too_loose"]["mode"] = "hybrid"
        biased["too_loose"]["groove_preserve"] = _clamp_int(int(biased["too_loose"].get("groove_preserve", 0)) - 6, 0, 100)
        biased["warbly"]["mode"] = "hybrid" if str(biased["warbly"].get("mode")) != "dtw" else "dtw"
    elif dominant_feedback == "too_tight":
        biased["too_tight"]["groove_preserve"] = _clamp_int(int(biased["too_tight"].get("groove_preserve", 0)) + 8, 0, 100)
        biased["good"]["groove_preserve"] = _clamp_int(int(biased["good"].get("groove_preserve", 0)) + 4, 0, 100)
    return biased


def _recommendation_support(
    recommendation_behavior: dict[str, Any] | None,
    *,
    recommendation_kind: str,
) -> dict[str, Any]:
    behavior = recommendation_behavior or {}
    selection_bias = float(dict(behavior.get("selection_biases") or {}).get(recommendation_kind, 0.0))
    outcome_bias = float(dict(behavior.get("outcome_biases") or {}).get(recommendation_kind, 0.0))
    rerun_bias = float(dict(behavior.get("rerun_biases") or {}).get(recommendation_kind, 0.0))
    adherence_bias = float(dict(behavior.get("adherence_biases") or {}).get(recommendation_kind, 0.0))
    score_bias = float(dict(behavior.get("score_biases") or {}).get(recommendation_kind, 0.0))
    drivers: list[str] = []
    if outcome_bias > 0.0:
        drivers.append("outcome")
    if adherence_bias > 0.0:
        drivers.append("adherence")
    if rerun_bias > 0.0:
        drivers.append("rerun")
    if selection_bias > 0.0:
        drivers.append("selection")
    evidence_total = (
        int(behavior.get("total_selections", 0))
        + int(behavior.get("total_outcomes", 0))
        + int(behavior.get("total_reruns", 0))
    )
    effectiveness = dict(dict(behavior.get("control_bias_effectiveness") or {}).get(recommendation_kind) or {})
    tuned_match_rate = effectiveness.get("tuned_match_rate")
    untuned_match_rate = effectiveness.get("untuned_match_rate")
    effectiveness_delta = 0.0
    if isinstance(tuned_match_rate, (int, float)) and isinstance(untuned_match_rate, (int, float)):
        effectiveness_delta = float(tuned_match_rate) - float(untuned_match_rate)
    return {
        "selection_bias": selection_bias,
        "outcome_bias": outcome_bias,
        "rerun_bias": rerun_bias,
        "adherence_bias": adherence_bias,
        "score_bias": score_bias,
        "drivers": drivers,
        "evidence_total": evidence_total,
        "tuned_match_rate": tuned_match_rate,
        "untuned_match_rate": untuned_match_rate,
        "effectiveness_delta": effectiveness_delta,
    }


def _bias_recommendation_controls(
    controls: dict[str, Any] | None,
    *,
    recommendation_kind: str,
    recommendation_behavior: dict[str, Any] | None,
    feedback: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    adjusted = dict(controls or {})
    if not adjusted:
        return adjusted, None

    support = _recommendation_support(
        recommendation_behavior,
        recommendation_kind=recommendation_kind,
    )
    positive_support = float(
        support["outcome_bias"] + support["adherence_bias"] + support["rerun_bias"] + max(0.0, support["selection_bias"])
    )
    effectiveness_delta = float(support.get("effectiveness_delta", 0.0))
    support_threshold = 0.035
    if effectiveness_delta <= -0.1:
        support_threshold = 0.09
    elif effectiveness_delta < 0.0:
        support_threshold = 0.05
    elif effectiveness_delta >= 0.2:
        support_threshold = 0.025
    elif effectiveness_delta >= 0.1:
        support_threshold = 0.03

    if support["evidence_total"] < 2 or positive_support < support_threshold or not support["drivers"]:
        return adjusted, None

    groove_preserve = _clamp_int(int(adjusted.get("groove_preserve", 0)), 0, 100)
    mode = str(adjusted.get("mode", "hybrid"))
    if effectiveness_delta <= -0.1:
        delta = 1
    elif effectiveness_delta >= 0.2:
        delta = 6 if positive_support >= 0.09 else 4
    elif effectiveness_delta >= 0.1:
        delta = 4 if positive_support >= 0.07 else 3
    else:
        delta = 2 if positive_support < 0.09 else 4
    groove_delta = 0

    if feedback == "warbly":
        adjusted["mode"] = "dtw"
        groove_delta = delta
    elif feedback == "too_loose":
        groove_delta = -delta
    elif feedback == "too_tight":
        groove_delta = delta
    elif recommendation_kind == "learned_default":
        if mode == "dtw":
            groove_delta = delta
        elif mode == "hybrid":
            groove_delta = -delta

    adjusted["groove_preserve"] = _clamp_int(groove_preserve + groove_delta, 0, 100)

    primary_driver = support["drivers"][0]
    if groove_delta == 0 and adjusted.get("mode") == mode:
        return adjusted, None
    if recommendation_kind == "learned_default":
        message = "Trusted whole-track history slightly tuned this default toward its proven mode."
    elif feedback == "warbly":
        message = "Trusted retry history nudged this section recommendation toward a safer artifact-reduction pass."
    elif feedback == "too_loose":
        message = "Trusted retry history nudged this section recommendation slightly tighter."
    elif feedback == "too_tight":
        message = "Trusted retry history nudged this section recommendation to preserve a bit more feel."
    else:
        message = "Trusted recommendation history slightly tuned these controls."
    return adjusted, {
        "applied": True,
        "primary_driver": primary_driver,
        "drivers": support["drivers"],
        "support_score": round(positive_support, 6),
        "effectiveness_delta": round(effectiveness_delta, 6),
        "groove_delta": int(groove_delta),
        "message": message,
    }


def build_best_next_pass(segment_feedback: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for segment in list(segment_feedback or []):
        guidance = dict(segment.get("position_guidance") or {})
        dominant_feedback = str(guidance.get("dominant_feedback") or "")
        if not dominant_feedback:
            continue
        action = next(
            (item for item in list(segment.get("quick_actions") or []) if str(item.get("feedback")) == dominant_feedback),
            None,
        )
        if action is None:
            continue
        return {
            "label": f"Try {segment.get('label', 'This Section')} Next",
            "description": str(guidance.get("message") or segment.get("summary") or "").strip(),
            "segment_index": int(segment.get("segment_index", 0)),
            "time_range_label": str(segment.get("time_range_label", "")),
            "feedback": dominant_feedback,
            "confidence": round(min(0.95, 0.45 + (0.1 * float(guidance.get("count", 0)))), 6),
            "suggested_controls": dict(action.get("suggested_controls") or {}),
        }
    return None


def update_recommendation_memory(
    memory: dict[str, Any],
    *,
    style_profile_name: str,
    recommendation_kind: str,
) -> dict[str, Any]:
    updated = dict(memory or {})
    profiles = dict(updated.get("profiles") or {})
    profile_stats = dict(profiles.get(style_profile_name) or {})
    counts = dict(profile_stats.get("selection_counts") or {})
    counts[recommendation_kind] = int(counts.get(recommendation_kind, 0)) + 1
    profile_stats["selection_counts"] = counts
    profile_stats["total_selections"] = int(profile_stats.get("total_selections", 0)) + 1
    profiles[style_profile_name] = profile_stats
    updated["profiles"] = profiles
    updated["total_selections"] = int(updated.get("total_selections", 0)) + 1
    return updated


def update_recommendation_outcome_memory(
    memory: dict[str, Any],
    *,
    style_profile_name: str,
    recommendation_kind: str,
    feedback: str,
) -> dict[str, Any]:
    updated = dict(memory or {})
    profiles = dict(updated.get("profiles") or {})
    profile_stats = dict(profiles.get(style_profile_name) or {})
    outcome_counts = dict(profile_stats.get("outcome_counts") or {})
    recommendation_outcomes = dict(outcome_counts.get(recommendation_kind) or {})
    recommendation_outcomes[feedback] = int(recommendation_outcomes.get(feedback, 0)) + 1
    outcome_counts[recommendation_kind] = recommendation_outcomes
    profile_stats["outcome_counts"] = outcome_counts
    profile_stats["total_outcomes"] = int(profile_stats.get("total_outcomes", 0)) + 1
    profiles[style_profile_name] = profile_stats
    updated["profiles"] = profiles
    updated["total_outcomes"] = int(updated.get("total_outcomes", 0)) + 1
    return updated


def update_recommendation_rerun_memory(
    memory: dict[str, Any],
    *,
    style_profile_name: str,
    recommendation_kind: str,
    matched_suggestion: bool,
    used_control_bias: bool = False,
) -> dict[str, Any]:
    updated = dict(memory or {})
    profiles = dict(updated.get("profiles") or {})
    profile_stats = dict(profiles.get(style_profile_name) or {})
    rerun_counts = dict(profile_stats.get("rerun_counts") or {})
    rerun_counts[recommendation_kind] = int(rerun_counts.get(recommendation_kind, 0)) + 1
    profile_stats["rerun_counts"] = rerun_counts
    adherence_counts = dict(profile_stats.get("adherence_counts") or {})
    recommendation_adherence = dict(adherence_counts.get(recommendation_kind) or {})
    adherence_key = "matched" if matched_suggestion else "modified"
    recommendation_adherence[adherence_key] = int(recommendation_adherence.get(adherence_key, 0)) + 1
    adherence_counts[recommendation_kind] = recommendation_adherence
    profile_stats["adherence_counts"] = adherence_counts
    control_bias_counts = dict(profile_stats.get("control_bias_counts") or {})
    recommendation_control_bias = dict(control_bias_counts.get(recommendation_kind) or {})
    control_key = "tuned" if used_control_bias else "untuned"
    recommendation_control_bias[control_key] = int(recommendation_control_bias.get(control_key, 0)) + 1
    control_bias_counts[recommendation_kind] = recommendation_control_bias
    profile_stats["control_bias_counts"] = control_bias_counts
    control_adherence_counts = dict(profile_stats.get("control_adherence_counts") or {})
    recommendation_control_adherence = dict(control_adherence_counts.get(recommendation_kind) or {})
    adherence_control_key = f"{control_key}_{adherence_key}"
    recommendation_control_adherence[adherence_control_key] = (
        int(recommendation_control_adherence.get(adherence_control_key, 0)) + 1
    )
    control_adherence_counts[recommendation_kind] = recommendation_control_adherence
    profile_stats["control_adherence_counts"] = control_adherence_counts
    profile_stats["total_reruns"] = int(profile_stats.get("total_reruns", 0)) + 1
    profiles[style_profile_name] = profile_stats
    updated["profiles"] = profiles
    updated["total_reruns"] = int(updated.get("total_reruns", 0)) + 1
    return updated


def summarize_recommendation_memory(
    memory: dict[str, Any],
    *,
    style_profile_name: str,
) -> dict[str, Any]:
    profiles = dict(memory.get("profiles") or {})
    stats = dict(profiles.get(style_profile_name) or {})
    counts = {
        "learned_default": int(dict(stats.get("selection_counts") or {}).get("learned_default", 0)),
        "best_next_pass": int(dict(stats.get("selection_counts") or {}).get("best_next_pass", 0)),
    }
    raw_outcome_counts = dict(stats.get("outcome_counts") or {})
    outcome_counts = {
        "learned_default": dict(raw_outcome_counts.get("learned_default") or {}),
        "best_next_pass": dict(raw_outcome_counts.get("best_next_pass") or {}),
    }
    rerun_counts = {
        "learned_default": int(dict(stats.get("rerun_counts") or {}).get("learned_default", 0)),
        "best_next_pass": int(dict(stats.get("rerun_counts") or {}).get("best_next_pass", 0)),
    }
    raw_adherence_counts = dict(stats.get("adherence_counts") or {})
    adherence_counts = {
        "learned_default": dict(raw_adherence_counts.get("learned_default") or {}),
        "best_next_pass": dict(raw_adherence_counts.get("best_next_pass") or {}),
    }
    raw_control_bias_counts = dict(stats.get("control_bias_counts") or {})
    control_bias_counts = {
        "learned_default": dict(raw_control_bias_counts.get("learned_default") or {}),
        "best_next_pass": dict(raw_control_bias_counts.get("best_next_pass") or {}),
    }
    raw_control_adherence_counts = dict(stats.get("control_adherence_counts") or {})
    control_adherence_counts = {
        "learned_default": dict(raw_control_adherence_counts.get("learned_default") or {}),
        "best_next_pass": dict(raw_control_adherence_counts.get("best_next_pass") or {}),
    }
    total_selections = int(stats.get("total_selections", sum(counts.values())))
    total_outcomes = int(stats.get("total_outcomes", sum(sum(int(value) for value in kind.values()) for kind in outcome_counts.values())))
    total_reruns = int(stats.get("total_reruns", sum(rerun_counts.values())))
    selection_biases = {"learned_default": 0.0, "best_next_pass": 0.0}
    if total_selections >= 2:
        learned_share = counts["learned_default"] / max(total_selections, 1)
        best_next_share = counts["best_next_pass"] / max(total_selections, 1)
        selection_biases = {
            "learned_default": round(max(-0.12, min(0.12, (learned_share - 0.5) * 0.24)), 6),
            "best_next_pass": round(max(-0.12, min(0.12, (best_next_share - 0.5) * 0.24)), 6),
        }

    outcome_biases = {"learned_default": 0.0, "best_next_pass": 0.0}
    outcome_summary = None
    if total_outcomes >= 2:
        for kind in outcome_biases:
            kind_counts = outcome_counts[kind]
            kind_total = int(sum(int(value) for value in kind_counts.values()))
            if kind_total < 2:
                continue
            good = int(kind_counts.get("good", 0))
            corrective = kind_total - good
            quality = (good - corrective) / max(kind_total, 1)
            outcome_biases[kind] = round(max(-0.08, min(0.08, quality * 0.08)), 6)
        if outcome_biases["learned_default"] > outcome_biases["best_next_pass"]:
            outcome_summary = "Whole-track learned defaults have produced better follow-up outcomes for this style."
        elif outcome_biases["best_next_pass"] > outcome_biases["learned_default"]:
            outcome_summary = "Section-guided next passes have produced better follow-up outcomes for this style."

    rerun_biases = {"learned_default": 0.0, "best_next_pass": 0.0}
    rerun_summary = None
    if total_reruns >= 2:
        learned_rerun_share = rerun_counts["learned_default"] / max(total_reruns, 1)
        best_next_rerun_share = rerun_counts["best_next_pass"] / max(total_reruns, 1)
        rerun_biases = {
            "learned_default": round(max(-0.04, min(0.04, (learned_rerun_share - 0.5) * 0.08)), 6),
            "best_next_pass": round(max(-0.04, min(0.04, (best_next_rerun_share - 0.5) * 0.08)), 6),
        }
        if rerun_counts["learned_default"] > rerun_counts["best_next_pass"]:
            rerun_summary = "Operators are more likely to rerun after applying the whole-track learned default for this style."
        elif rerun_counts["best_next_pass"] > rerun_counts["learned_default"]:
            rerun_summary = "Operators are more likely to rerun after applying the strongest local retry for this style."

    adherence_summary = None
    adherence_biases = {"learned_default": 0.0, "best_next_pass": 0.0}
    for kind in adherence_biases:
        kind_counts = adherence_counts[kind]
        matched = int(kind_counts.get("matched", 0))
        modified = int(kind_counts.get("modified", 0))
        total = matched + modified
        if total < 2:
            continue
        adherence_biases[kind] = round(max(-0.03, min(0.03, ((matched - modified) / max(total, 1)) * 0.03)), 6)
    if adherence_biases["learned_default"] > adherence_biases["best_next_pass"] and adherence_biases["learned_default"] > 0.0:
        adherence_summary = "Whole-track learned defaults are more often trusted as-is for this style."
    elif adherence_biases["best_next_pass"] > adherence_biases["learned_default"] and adherence_biases["best_next_pass"] > 0.0:
        adherence_summary = "Section-guided next passes are more often trusted as-is for this style."

    control_bias_summary = None
    control_bias_effectiveness = {
        "learned_default": {"tuned_match_rate": None, "untuned_match_rate": None},
        "best_next_pass": {"tuned_match_rate": None, "untuned_match_rate": None},
    }
    effectiveness_biases = {"learned_default": 0.0, "best_next_pass": 0.0}
    for kind in control_bias_effectiveness:
        kind_counts = control_adherence_counts[kind]
        tuned_total = int(kind_counts.get("tuned_matched", 0)) + int(kind_counts.get("tuned_modified", 0))
        untuned_total = int(kind_counts.get("untuned_matched", 0)) + int(kind_counts.get("untuned_modified", 0))
        tuned_rate = None if tuned_total < 2 else round(int(kind_counts.get("tuned_matched", 0)) / max(tuned_total, 1), 6)
        untuned_rate = None if untuned_total < 2 else round(int(kind_counts.get("untuned_matched", 0)) / max(untuned_total, 1), 6)
        control_bias_effectiveness[kind] = {
            "tuned_match_rate": tuned_rate,
            "untuned_match_rate": untuned_rate,
        }
        if tuned_rate is not None and untuned_rate is not None:
            effectiveness_biases[kind] = round(max(-0.05, min(0.05, (float(tuned_rate) - float(untuned_rate)) * 0.08)), 6)
    for kind in ("learned_default", "best_next_pass"):
        tuned_rate = control_bias_effectiveness[kind]["tuned_match_rate"]
        untuned_rate = control_bias_effectiveness[kind]["untuned_match_rate"]
        if tuned_rate is None or untuned_rate is None:
            continue
        if tuned_rate > untuned_rate:
            if kind == "learned_default":
                control_bias_summary = "Tuned whole-track defaults are being followed as-is more often than raw ones for this style."
            else:
                control_bias_summary = "Tuned section-guided retries are being followed as-is more often than raw ones for this style."
            break

    preferred_kind = None
    if counts["learned_default"] > counts["best_next_pass"]:
        preferred_kind = "learned_default"
    elif counts["best_next_pass"] > counts["learned_default"]:
        preferred_kind = "best_next_pass"

    summary = None
    if preferred_kind == "learned_default":
        summary = "Operators usually choose the whole-track learned default for this style."
    elif preferred_kind == "best_next_pass":
        summary = "Operators usually choose the strongest local retry for this style."
    if outcome_summary:
        summary = f"{summary} {outcome_summary}".strip() if summary else outcome_summary
    if rerun_summary:
        summary = f"{summary} {rerun_summary}".strip() if summary else rerun_summary
    if adherence_summary:
        summary = f"{summary} {adherence_summary}".strip() if summary else adherence_summary
    if control_bias_summary:
        summary = f"{summary} {control_bias_summary}".strip() if summary else control_bias_summary

    score_biases = {
        "learned_default": round(
            float(selection_biases["learned_default"])
            + float(outcome_biases["learned_default"])
            + float(rerun_biases["learned_default"])
            + float(adherence_biases["learned_default"])
            + float(effectiveness_biases["learned_default"]),
            6,
        ),
        "best_next_pass": round(
            float(selection_biases["best_next_pass"])
            + float(outcome_biases["best_next_pass"])
            + float(rerun_biases["best_next_pass"])
            + float(adherence_biases["best_next_pass"])
            + float(effectiveness_biases["best_next_pass"]),
            6,
        ),
    }

    return {
        "style_profile": style_profile_name,
        "total_selections": total_selections,
        "total_outcomes": total_outcomes,
        "total_reruns": total_reruns,
        "preferred_kind": preferred_kind,
        "selection_biases": selection_biases,
        "outcome_biases": outcome_biases,
        "rerun_biases": rerun_biases,
        "adherence_biases": adherence_biases,
        "effectiveness_biases": effectiveness_biases,
        "score_biases": score_biases,
        "outcome_counts": outcome_counts,
        "rerun_counts": rerun_counts,
        "adherence_counts": adherence_counts,
        "control_bias_counts": control_bias_counts,
        "control_adherence_counts": control_adherence_counts,
        "control_bias_effectiveness": control_bias_effectiveness,
        "summary": summary,
    }


def rank_next_pass_recommendations(
    learned_default: dict[str, Any] | None,
    learned_behavior: dict[str, Any] | None,
    best_next_pass: dict[str, Any] | None,
    recommendation_behavior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    learned_behavior = learned_behavior or {}
    recommendation_behavior = recommendation_behavior or {}
    score_biases = dict(recommendation_behavior.get("score_biases") or {})
    outcome_biases = dict(recommendation_behavior.get("outcome_biases") or {})
    adherence_biases = dict(recommendation_behavior.get("adherence_biases") or {})
    effectiveness_biases = dict(recommendation_behavior.get("effectiveness_biases") or {})
    scored: list[dict[str, Any]] = []
    if learned_default:
        memory_bias = round(float(score_biases.get("learned_default", 0.0)), 6)
        reason = "Whole-track learned history for this style."
        selection_bias = float(dict(recommendation_behavior.get("selection_biases") or {}).get("learned_default", 0.0))
        outcome_bias = float(dict(recommendation_behavior.get("outcome_biases") or {}).get("learned_default", 0.0))
        adherence_bias = float(dict(recommendation_behavior.get("adherence_biases") or {}).get("learned_default", 0.0))
        effectiveness_bias = float(effectiveness_biases.get("learned_default", 0.0))
        if outcome_bias > 0.0:
            reason += " Reinforced by successful follow-up outcomes for this style."
        elif adherence_bias > 0.0:
            reason += " Reinforced by operators trusting it as-is for this style."
        elif effectiveness_bias > 0.0:
            reason += " Reinforced because its adaptive tuning has held up better than the raw version for this style."
        elif outcome_bias < 0.0:
            reason += " Slightly discounted by weaker follow-up outcomes for this style."
        elif adherence_bias < 0.0:
            reason += " Slightly discounted because operators often adapt it first for this style."
        elif effectiveness_bias < 0.0:
            reason += " Slightly discounted because its tuned version has not outperformed the raw version for this style."
        elif selection_bias > 0.0:
            reason += " Reinforced by operator selections for this style."
        elif selection_bias < 0.0:
            reason += " Slightly discounted by operator selections for this style."
        scored.append(
            {
                "kind": "learned_default",
                "label": str(learned_default.get("label", "Use Learned Default")),
                "base_score": round(float(learned_behavior.get("confidence", 0.0)), 6),
                "memory_bias": memory_bias,
                "score": round(float(learned_behavior.get("confidence", 0.0)) + memory_bias, 6),
                "reason": reason,
            }
        )
    if best_next_pass:
        memory_bias = round(float(score_biases.get("best_next_pass", 0.0)), 6)
        reason = "Strongest current section-guided retry."
        selection_bias = float(dict(recommendation_behavior.get("selection_biases") or {}).get("best_next_pass", 0.0))
        outcome_bias = float(dict(recommendation_behavior.get("outcome_biases") or {}).get("best_next_pass", 0.0))
        adherence_bias = float(dict(recommendation_behavior.get("adherence_biases") or {}).get("best_next_pass", 0.0))
        effectiveness_bias = float(effectiveness_biases.get("best_next_pass", 0.0))
        if outcome_bias > 0.0:
            reason += " Reinforced by successful follow-up outcomes for this style."
        elif adherence_bias > 0.0:
            reason += " Reinforced by operators trusting it as-is for this style."
        elif effectiveness_bias > 0.0:
            reason += " Reinforced because its adaptive tuning has held up better than the raw version for this style."
        elif outcome_bias < 0.0:
            reason += " Slightly discounted by weaker follow-up outcomes for this style."
        elif adherence_bias < 0.0:
            reason += " Slightly discounted because operators often adapt it first for this style."
        elif effectiveness_bias < 0.0:
            reason += " Slightly discounted because its tuned version has not outperformed the raw version for this style."
        elif selection_bias > 0.0:
            reason += " Reinforced by operator selections for this style."
        elif selection_bias < 0.0:
            reason += " Slightly discounted by operator selections for this style."
        scored.append(
            {
                "kind": "best_next_pass",
                "label": str(best_next_pass.get("label", "Best Next Pass")),
                "base_score": round(float(best_next_pass.get("confidence", 0.0)), 6),
                "memory_bias": memory_bias,
                "score": round(float(best_next_pass.get("confidence", 0.0)) + memory_bias, 6),
                "reason": reason,
            }
        )

    scored.sort(key=lambda item: (-float(item["score"]), item["kind"]))
    preferred_kind = str(scored[0]["kind"]) if scored else None
    summary = None
    if len(scored) == 1:
        summary = f"{scored[0]['label']} is the only recommendation available right now."
    elif len(scored) >= 2:
        winner = scored[0]
        runner_up = scored[1]
        delta = max(0.0, float(winner["score"]) - float(runner_up["score"]))
        winner_outcome_bias = float(outcome_biases.get(str(winner["kind"]), 0.0))
        runner_up_outcome_bias = float(outcome_biases.get(str(runner_up["kind"]), 0.0))
        winner_adherence_bias = float(adherence_biases.get(str(winner["kind"]), 0.0))
        runner_up_adherence_bias = float(adherence_biases.get(str(runner_up["kind"]), 0.0))
        winner_effectiveness_bias = float(effectiveness_biases.get(str(winner["kind"]), 0.0))
        runner_up_effectiveness_bias = float(effectiveness_biases.get(str(runner_up["kind"]), 0.0))
        outcome_clause = ""
        adherence_clause = ""
        effectiveness_clause = ""
        if winner_outcome_bias > runner_up_outcome_bias and winner_outcome_bias > 0.0:
            outcome_clause = " It also has the stronger follow-up track record for this style."
        if winner_adherence_bias > runner_up_adherence_bias and winner_adherence_bias > 0.0:
            adherence_clause = " It is also more often trusted as-is for this style."
        if winner_effectiveness_bias > runner_up_effectiveness_bias and winner_effectiveness_bias > 0.0:
            effectiveness_clause = " Its adaptive tuning has also proven more trustworthy than the runner-up's for this style."
        summary = (
            f"{winner['label']} is stronger right now because {winner['reason'].rstrip('.').lower()} "
            f"It outranks {runner_up['label']} by {delta:.2f}.{outcome_clause}{adherence_clause}{effectiveness_clause}"
        )
    return {
        "preferred_kind": preferred_kind,
        "options": scored,
        "summary": summary,
        "recommendation_behavior": recommendation_behavior,
    }


def build_segment_feedback(
    *,
    mode_requested: str,
    target_bpm: float,
    resolution: int,
    groove_preserve: int,
    effective_groove_preserve: int,
    ml_used: bool,
    segment_summaries: list[dict[str, Any]] | None = None,
    total_duration_sec: float = 0.0,
    position_guidance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    controls = _base_suggested_controls(
        mode_requested=mode_requested,
        target_bpm=target_bpm,
        resolution=resolution,
        groove_preserve=groove_preserve,
        effective_groove_preserve=effective_groove_preserve,
        ml_used=ml_used,
    )
    items: list[dict[str, Any]] = []
    guidance_lookup = _position_guidance_lookup(position_guidance)

    for segment in list(segment_summaries or []):
        segment_index = int(segment.get("segment_index", 0))
        start_sec = float(segment.get("start_sec", 0.0))
        end_sec = float(segment.get("end_sec", start_sec))
        position_bucket = str(segment.get("position_bucket") or classify_segment_bucket(start_sec, end_sec, total_duration_sec))
        timing_metrics = dict(segment.get("timing_metrics") or {})
        after_sec = float(timing_metrics.get("avg_abs_error_after_sec", 0.0))
        improvement_pct = float(timing_metrics.get("improvement_pct", 0.0))
        delta_vs_baseline = float(segment.get("delta_vs_baseline_sec", 0.0))
        guidance = guidance_lookup.get(position_bucket, {})
        if after_sec <= 0.0:
            continue

        if delta_vs_baseline > 0.001:
            summary = "This section got slightly worse than the baseline pass and is worth a targeted retry."
        elif improvement_pct < 5.0:
            summary = "This section barely improved, so a local adjustment may help more than a whole-song rerun."
        else:
            summary = "This section improved, but you can still nudge it tighter or looser if the feel is off."
        if guidance:
            summary += f" Historical feedback suggests {position_bucket} sections in this style need extra attention."
        segment_controls = _bias_segment_controls(controls, guidance)

        items.append(
            {
                "segment_index": segment_index,
                "label": f"Segment {segment_index + 1}",
                "time_range_label": f"{_format_time_label(start_sec)}-{_format_time_label(end_sec)}",
                "summary": summary,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "position_bucket": position_bucket,
                "duration_sec": float(segment.get("duration_sec", max(0.0, end_sec - start_sec))),
                "delta_vs_baseline_sec": delta_vs_baseline,
                "timing_metrics": timing_metrics,
                "position_guidance": guidance or None,
                "quick_actions": [
                    {
                        "feedback": "good",
                        "label": "Keep Section",
                        "description": "This segment feels right.",
                        "suggested_controls": segment_controls["good"],
                    },
                    {
                        "feedback": "too_loose",
                        "label": "Tighten Section",
                        "description": "This segment needs a harder pull to the grid.",
                        "suggested_controls": segment_controls["too_loose"],
                    },
                    {
                        "feedback": "too_tight",
                        "label": "Loosen Section",
                        "description": "This segment should keep more of its original feel.",
                        "suggested_controls": segment_controls["too_tight"],
                    },
                    {
                        "feedback": "warbly",
                        "label": "Reduce Artifacts",
                        "description": "This section needs the safer deterministic path.",
                        "suggested_controls": segment_controls["warbly"],
                    },
                ],
                "history_count": 0,
                "latest": None,
            }
        )

    items.sort(
        key=lambda item: (
            -int(bool(item.get("position_guidance"))),
            -max(float(item.get("delta_vs_baseline_sec", 0.0)), 0.0),
            -float((item.get("timing_metrics") or {}).get("avg_abs_error_after_sec", 0.0)),
            int(item.get("segment_index", 0)),
        )
    )
    return items[:4]


def update_segment_feedback_memory(
    memory: dict[str, Any],
    *,
    style_profile_name: str,
    position_bucket: str,
    feedback: str,
) -> dict[str, Any]:
    updated = dict(memory or {})
    profiles = dict(updated.get("profiles") or {})
    profile_stats = dict(profiles.get(style_profile_name) or {})
    buckets = dict(profile_stats.get("buckets") or {})
    bucket_stats = dict(buckets.get(position_bucket) or {})
    counts = dict(bucket_stats.get("counts") or {})
    counts[feedback] = int(counts.get(feedback, 0)) + 1
    bucket_stats["counts"] = counts
    bucket_stats["total_entries"] = int(bucket_stats.get("total_entries", 0)) + 1
    buckets[position_bucket] = bucket_stats
    profile_stats["buckets"] = buckets
    profile_stats["total_entries"] = int(profile_stats.get("total_entries", 0)) + 1
    profiles[style_profile_name] = profile_stats
    updated["profiles"] = profiles
    updated["total_entries"] = int(updated.get("total_entries", 0)) + 1
    return updated


def summarize_position_feedback_memory(
    memory: dict[str, Any],
    *,
    style_profile_name: str,
    segment_feedback: list[dict[str, Any]] | None,
    mode_requested: str,
    target_bpm: float,
    resolution: int,
    groove_preserve: int,
    effective_groove_preserve: int,
    ml_used: bool,
) -> dict[str, Any]:
    controls = _base_suggested_controls(
        mode_requested=mode_requested,
        target_bpm=target_bpm,
        resolution=resolution,
        groove_preserve=groove_preserve,
        effective_groove_preserve=effective_groove_preserve,
        ml_used=ml_used,
    )
    profile_stats = dict((dict(memory.get("profiles") or {})).get(style_profile_name) or {})
    buckets = dict(profile_stats.get("buckets") or {})
    available_buckets = {str(item.get("position_bucket")) for item in list(segment_feedback or []) if item.get("position_bucket")}
    diagnostics: list[dict[str, Any]] = []

    for bucket_name, bucket_stats in buckets.items():
        if available_buckets and bucket_name not in available_buckets:
            continue
        counts = dict(bucket_stats.get("counts") or {})
        if not counts:
            continue
        dominant_feedback = max(counts, key=counts.get)
        dominant_count = int(counts.get(dominant_feedback, 0))
        if dominant_feedback == "good" or dominant_count < 2:
            continue
        if dominant_feedback == "too_tight":
            message = f"{bucket_name.title()} sections for this style tend to come back too tight."
        elif dominant_feedback == "too_loose":
            message = f"{bucket_name.title()} sections for this style tend to come back too loose."
        else:
            message = f"{bucket_name.title()} sections for this style tend to be artifact-prone."
        diagnostics.append(
            {
                "position_bucket": bucket_name,
                "dominant_feedback": dominant_feedback,
                "count": dominant_count,
                "message": message,
                "suggested_controls": controls.get(dominant_feedback, controls["good"]),
            }
        )

    diagnostics.sort(key=lambda item: (-int(item["count"]), str(item["position_bucket"])))
    summary = None
    if diagnostics:
        summary = diagnostics[0]["message"]
    return {
        "summary": summary,
        "diagnostics": diagnostics[:3],
    }


def summarize_segment_feedback(
    segment_feedback: list[dict[str, Any]] | None,
    entries: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    sections = {int(item.get("segment_index", -1)): dict(item) for item in list(segment_feedback or [])}
    section_entries: dict[int, list[dict[str, Any]]] = {}
    for entry in list(entries or []):
        if entry.get("scope") != "segment":
            continue
        segment_index = entry.get("segment_index")
        if segment_index is None:
            continue
        section_entries.setdefault(int(segment_index), []).append(dict(entry))

    diagnostics: list[dict[str, Any]] = []
    rerun_focus: list[dict[str, Any]] = []
    for segment_index, entries_for_segment in section_entries.items():
        counts: dict[str, int] = {}
        latest = entries_for_segment[-1]
        for entry in entries_for_segment:
            feedback = str(entry.get("feedback", ""))
            counts[feedback] = int(counts.get(feedback, 0)) + 1
        total = len(entries_for_segment)
        dominant_feedback = max(counts, key=counts.get)
        dominant_count = int(counts[dominant_feedback])
        if dominant_feedback == "good" or dominant_count < 2:
            continue

        segment = sections.get(segment_index, {})
        time_range = str(segment.get("time_range_label") or "unknown range")
        if dominant_feedback == "too_tight":
            message = f"{segment.get('label', f'Segment {segment_index + 1}')} ({time_range}) keeps coming back too tight."
        elif dominant_feedback == "too_loose":
            message = f"{segment.get('label', f'Segment {segment_index + 1}')} ({time_range}) keeps coming back too loose."
        else:
            message = f"{segment.get('label', f'Segment {segment_index + 1}')} ({time_range}) keeps sounding warbly."

        suggested_controls = dict(latest.get("suggested_controls") or {})
        diagnostics.append(
            {
                "segment_index": segment_index,
                "label": segment.get("label", f"Segment {segment_index + 1}"),
                "time_range_label": time_range,
                "dominant_feedback": dominant_feedback,
                "count": dominant_count,
                "total_entries": total,
                "message": message,
                "suggested_controls": suggested_controls,
            }
        )
        rerun_focus.append(
            {
                "segment_index": segment_index,
                "label": segment.get("label", f"Segment {segment_index + 1}"),
                "reason": dominant_feedback,
                "message": message,
                "suggested_controls": suggested_controls,
            }
        )

    diagnostics.sort(key=lambda item: (-int(item["count"]), int(item["segment_index"])))
    rerun_focus.sort(key=lambda item: int(item["segment_index"]))

    summary = None
    if diagnostics:
        top = diagnostics[0]
        summary = f"{top['label']} is the clearest repeat issue so far."

    return {
        "summary": summary,
        "diagnostics": diagnostics[:3],
        "rerun_focus": rerun_focus[:3],
    }


def summarize_feedback_memory(memory: dict[str, Any], style_profile_name: str, requested_groove_preserve: int) -> dict[str, Any]:
    profiles = dict(memory.get("profiles") or {})
    stats = dict(profiles.get(style_profile_name) or {})
    counts = dict(stats.get("counts") or {})
    evidence_count = int(sum(int(value) for value in counts.values()))
    requested = _clamp_int(requested_groove_preserve, 0, 100)

    if evidence_count < 2:
        return {
            "style_profile": style_profile_name,
            "evidence_count": evidence_count,
            "applied_groove_delta": 0,
            "guided_groove_preserve": requested,
            "preferred_mode": None,
            "confidence": 0.0,
        }

    too_loose = int(counts.get("too_loose", 0))
    too_tight = int(counts.get("too_tight", 0))
    warbly = int(counts.get("warbly", 0))
    groove_delta = round(((-6 * too_loose) + (6 * too_tight) + (8 * warbly)) / max(evidence_count, 1))
    groove_delta = _clamp_int(groove_delta, -12, 12)
    preferred_mode = None
    if warbly >= 2 and warbly >= max(too_loose, too_tight):
        preferred_mode = "dtw"
    elif too_loose >= 2 and too_loose > max(too_tight, warbly):
        preferred_mode = "hybrid"

    confidence = min(0.95, max(abs(groove_delta) / 12.0, max(too_loose, too_tight, warbly) / max(evidence_count, 1)))
    return {
        "style_profile": style_profile_name,
        "evidence_count": evidence_count,
        "applied_groove_delta": int(groove_delta),
        "guided_groove_preserve": _clamp_int(requested + groove_delta, 0, 100),
        "preferred_mode": preferred_mode,
        "confidence": round(float(confidence), 6),
    }


def update_feedback_memory(memory: dict[str, Any], style_profile_name: str, feedback: str) -> dict[str, Any]:
    updated = dict(memory or {})
    profiles = dict(updated.get("profiles") or {})
    profile_stats = dict(profiles.get(style_profile_name) or {})
    counts = dict(profile_stats.get("counts") or {})
    counts[feedback] = int(counts.get(feedback, 0)) + 1
    profile_stats["counts"] = counts
    profile_stats["total_entries"] = int(profile_stats.get("total_entries", 0)) + 1
    profiles[style_profile_name] = profile_stats
    updated["profiles"] = profiles
    updated["total_entries"] = int(updated.get("total_entries", 0)) + 1
    return updated


def build_feedback_loop(
    *,
    mode_requested: str,
    target_bpm: float,
    resolution: int,
    groove_preserve: int,
    effective_groove_preserve: int,
    style_profile: dict[str, Any],
    timing_metrics: dict[str, Any],
    warp_selection: str,
    ml_used: bool,
    learned_behavior: dict[str, Any] | None = None,
    recommendation_behavior: dict[str, Any] | None = None,
    segment_feedback: list[dict[str, Any]] | None = None,
    best_next_pass: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = str(style_profile.get("profile", "balanced"))
    confidence = float(style_profile.get("confidence", 0.0))
    improvement_pct = float(timing_metrics.get("improvement_pct", 0.0))
    learned_behavior = learned_behavior or {}
    next_mode = "hybrid" if ml_used else "dtw"

    controls = _base_suggested_controls(
        mode_requested=mode_requested,
        target_bpm=target_bpm,
        resolution=resolution,
        groove_preserve=groove_preserve,
        effective_groove_preserve=effective_groove_preserve,
        ml_used=ml_used,
    )
    learned_mode = str(learned_behavior.get("preferred_mode") or (next_mode if ml_used else mode_requested))
    learned_groove = _clamp_int(int(learned_behavior.get("guided_groove_preserve", groove_preserve)), 0, 100)

    quick_actions = [
        {
            "feedback": "good",
            "label": "Keep This",
            "description": "The timing feels right. Keep the current strategy as the preferred preset.",
            "suggested_controls": controls["good"],
        },
        {
            "feedback": "too_loose",
            "label": "Make Tighter",
            "description": "Try stronger timing correction with less groove preservation.",
            "suggested_controls": controls["too_loose"],
        },
        {
            "feedback": "too_tight",
            "label": "Keep More Feel",
            "description": "Try preserving more of the original groove and drift.",
            "suggested_controls": controls["too_tight"],
        },
        {
            "feedback": "warbly",
            "label": "Reduce Artifacts",
            "description": "Fall back to the safer deterministic path with more preserved feel.",
            "suggested_controls": controls["warbly"],
        },
    ]

    if profile == "percussive_tight":
        summary = "Percussive material can usually tolerate tighter quantization."
    elif profile == "harmonic_expressive":
        summary = "Expressive harmonic material may need more groove preservation and safer warping."
    elif profile == "mixed_drifting":
        summary = "Mixed drifting material is a good candidate for segmented hybrid correction."
    elif profile == "dense_rhythmic":
        summary = "Dense rhythmic material benefits from controlled tightening without extreme groove removal."
    else:
        summary = "Balanced material may need small groove-preserve adjustments rather than major mode changes."

    if improvement_pct <= 0.0 and warp_selection != "hybrid_candidate":
        summary += " The current pass did not clearly beat baseline, so user feedback is especially valuable here."
    if int(learned_behavior.get("evidence_count", 0)) >= 2:
        summary += (
            f" Learned history for this style currently suggests groove_preserve="
            f"{int(learned_behavior.get('guided_groove_preserve', groove_preserve))}."
        )

    learned_default = None
    if int(learned_behavior.get("evidence_count", 0)) >= 2:
        learned_controls, learned_control_bias = _bias_recommendation_controls(
            {
                "mode": learned_mode,
                "target_bpm": float(target_bpm),
                "resolution": int(resolution),
                "groove_preserve": int(learned_groove),
            },
            recommendation_kind="learned_default",
            recommendation_behavior=recommendation_behavior,
        )
        learned_default = {
            "label": "Use Learned Default",
            "description": "Apply the current learned mode and groove settings for this style profile.",
            "confidence": round(float(learned_behavior.get("confidence", 0.0)), 6),
            "suggested_controls": learned_controls,
        }
        if learned_control_bias is not None:
            learned_default["control_bias"] = learned_control_bias
    if best_next_pass:
        best_next_pass = dict(best_next_pass)
        best_next_controls, best_next_control_bias = _bias_recommendation_controls(
            dict(best_next_pass.get("suggested_controls") or {}),
            recommendation_kind="best_next_pass",
            recommendation_behavior=recommendation_behavior,
            feedback=str(best_next_pass.get("feedback") or ""),
        )
        best_next_pass["suggested_controls"] = best_next_controls
        if best_next_control_bias is not None:
            best_next_pass["control_bias"] = best_next_control_bias
    recommendation_rank = rank_next_pass_recommendations(
        learned_default,
        learned_behavior,
        best_next_pass,
        recommendation_behavior,
    )

    return {
        "summary": summary,
        "style_profile": profile,
        "style_confidence": round(confidence, 6),
        "quick_actions": quick_actions,
        "learned_default": learned_default,
        "history_count": 0,
        "latest": None,
        "learned_behavior": learned_behavior,
        "recommendation_behavior": recommendation_behavior or {},
        "segment_feedback": list(segment_feedback or []),
        "best_next_pass": best_next_pass,
        "recommendation_rank": recommendation_rank,
    }


def record_feedback_entry(
    *,
    feedback: str,
    suggested_controls: dict[str, Any],
    notes: str = "",
    style_profile: str = "balanced",
    mode_requested: str = "hybrid",
    effective_groove_preserve: int = 0,
    scope: str = "global",
    segment_index: int | None = None,
    segment_start_sec: float | None = None,
    segment_end_sec: float | None = None,
    position_bucket: str | None = None,
) -> dict[str, Any]:
    return {
        "feedback": str(feedback),
        "notes": str(notes).strip(),
        "style_profile": str(style_profile),
        "mode_requested": str(mode_requested),
        "effective_groove_preserve": int(effective_groove_preserve),
        "scope": str(scope),
        "segment_index": None if segment_index is None else int(segment_index),
        "segment_start_sec": None if segment_start_sec is None else float(segment_start_sec),
        "segment_end_sec": None if segment_end_sec is None else float(segment_end_sec),
        "position_bucket": None if position_bucket is None else str(position_bucket),
        "suggested_controls": {
            "mode": str(suggested_controls.get("mode", "hybrid")),
            "target_bpm": float(suggested_controls.get("target_bpm", 100.0)),
            "resolution": int(suggested_controls.get("resolution", 8)),
            "groove_preserve": int(suggested_controls.get("groove_preserve", 0)),
        },
    }
