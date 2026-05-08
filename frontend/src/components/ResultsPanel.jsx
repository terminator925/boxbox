import { useEffect, useState } from "react";
import { downloadUrl, recordRecommendationSelection, submitFeedback } from "../api";
import RuntimeStrip from "./RuntimeStrip.jsx";

export default function ResultsPanel({ jobId, result, setControls }) {
  const [report, setReport] = useState(null);
  const [feedbackState, setFeedbackState] = useState({ status: "idle", message: "" });

  async function loadReport(activeJobId) {
    if (!activeJobId) return;
    try {
      const response = await fetch(downloadUrl(activeJobId, "report.json"));
      const data = await response.json();
      setReport(data);
    } catch {
      setReport(null);
    }
  }

  useEffect(() => {
    if (!result || !jobId) return;
    loadReport(jobId);
  }, [jobId, result]);

  async function onFeedback(action, segmentIndex = null) {
    if (!jobId || !action?.feedback) return;
    setFeedbackState({ status: "saving", message: "" });
    try {
      const response = await submitFeedback({
        job_id: jobId,
        feedback: action.feedback,
        notes: "",
        segment_index: Number.isInteger(segmentIndex) ? segmentIndex : null,
      });
      await loadReport(jobId);
      if (action.suggested_controls && setControls) {
        setControls((current) => ({ ...current, ...action.suggested_controls }));
      }
      const scopeLabel = response.entry?.scope === "segment" ? "Section" : "Feedback";
      setFeedbackState({ status: "saved", message: `${scopeLabel} saved. Controls updated for the next pass.` });
    } catch (error) {
      setFeedbackState({ status: "error", message: error.message });
    }
  }

  async function onApplySuggestedControls(action, recommendationKind = null) {
    if (!action?.suggested_controls || !setControls) return;
    if (jobId && recommendationKind) {
      try {
        await recordRecommendationSelection({ job_id: jobId, recommendation_kind: recommendationKind });
      } catch {
        // ignore telemetry-style failures and still apply controls
      }
    }
    setControls((current) => ({ ...current, ...action.suggested_controls }));
    const biasMessage = action.control_bias?.applied ? ` ${action.control_bias.message}` : "";
    setFeedbackState({ status: "saved", message: `${action.label} applied to the controls.${biasMessage}` });
  }

  function recommendationMeta(kind) {
    return report?.feedback_loop?.recommendation_rank?.options?.find((item) => item.kind === kind) || null;
  }

  function recommendationOutcomeMeta(kind) {
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const outcomeCounts = behavior.outcome_counts?.[kind] || {};
    const totalOutcomes = Object.values(outcomeCounts).reduce((sum, value) => sum + Number(value || 0), 0);
    if (totalOutcomes < 2) return null;
    const goodCount = Number(outcomeCounts.good || 0);
    const correctiveCount = Math.max(0, totalOutcomes - goodCount);
    if (goodCount > correctiveCount) {
      return `${goodCount}/${totalOutcomes} follow-ups for this style came back good.`;
    }
    if (correctiveCount > goodCount) {
      return `${correctiveCount}/${totalOutcomes} follow-ups for this style still needed correction.`;
    }
    return `${goodCount}/${totalOutcomes} follow-ups for this style came back good so far.`;
  }

  function latestRecommendationOutcome(kind) {
    const latest = report?.feedback_loop?.latest_recommendation_outcome;
    if (!latest || latest.recommendation_kind !== kind) return null;
    return `Latest attributed outcome: ${latest.feedback}.`;
  }

  function recommendationRerunMeta(kind) {
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const rerunCounts = behavior.rerun_counts || {};
    const totalReruns = Object.values(rerunCounts).reduce((sum, value) => sum + Number(value || 0), 0);
    const kindReruns = Number(rerunCounts[kind] || 0);
    if (totalReruns < 2 || kindReruns === 0) return null;
    return `${kindReruns}/${totalReruns} style-matched reruns carried this option into another pass.`;
  }

  function recommendationAdherenceMeta(kind) {
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const adherenceCounts = behavior.adherence_counts?.[kind] || {};
    const matched = Number(adherenceCounts.matched || 0);
    const modified = Number(adherenceCounts.modified || 0);
    const total = matched + modified;
    if (total < 2) return null;
    if (matched > modified) {
      return `${matched}/${total} reruns for this style followed this suggestion exactly.`;
    }
    if (modified > matched) {
      return `${modified}/${total} reruns for this style adapted this suggestion before rerun.`;
    }
    return `${matched}/${total} reruns for this style matched this suggestion exactly so far.`;
  }

  function latestRecommendationRerun(kind) {
    const latest = report?.feedback_loop?.latest_recommendation_rerun;
    if (!latest || latest.recommendation_kind !== kind) return null;
    const tuningLabel = latest.used_control_bias ? " tuned" : " raw";
    return latest.matched_suggestion
      ? `Latest attributed rerun matched the${tuningLabel} suggested controls.`
      : `Latest attributed rerun used a modified version of this${tuningLabel} recommendation.`;
  }

  function latestRecommendationRerunBadge(kind) {
    const latest = report?.feedback_loop?.latest_recommendation_rerun;
    if (!latest || latest.recommendation_kind !== kind) return null;
    return latest.matched_suggestion ? "Matched Suggestion" : "Modified Suggestion";
  }

  function recommendationEvidenceSummary(kind) {
    const meta = recommendationMeta(kind);
    if (!meta) return null;
    const parts = [
      `Confidence ${(meta.score * 100).toFixed(0)}%`,
      meta.reason,
    ];
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const effectivenessBias = Number(behavior.effectiveness_biases?.[kind] || 0);
    if (effectivenessBias > 0) {
      parts.push("Adaptive tuning has been a net trust gain for this style.");
    } else if (effectivenessBias < 0) {
      parts.push("Adaptive tuning has been less trusted than raw behavior for this style.");
    }
    const outcome = recommendationOutcomeMeta(kind);
    if (outcome) parts.push(outcome);
    const rerun = recommendationRerunMeta(kind);
    if (rerun) parts.push(rerun);
    const adherence = recommendationAdherenceMeta(kind);
    if (adherence) parts.push(adherence);
    return parts.join(" ");
  }

  function recommendationControlBiasMeta(kind) {
    const recommendation = report?.feedback_loop?.[kind];
    const controlBias = recommendation?.control_bias;
    if (!controlBias?.applied) return null;
    const grooveDelta = Number(controlBias.groove_delta || 0);
    const deltaLabel = grooveDelta === 0 ? "" : ` Groove ${grooveDelta > 0 ? "+" : ""}${grooveDelta}.`;
    const effectivenessDelta = Number(controlBias.effectiveness_delta || 0);
    let effectivenessLabel = "";
    if (effectivenessDelta >= 0.2) {
      effectivenessLabel = " Tuned follow-through is materially stronger than raw history.";
    } else if (effectivenessDelta >= 0.1) {
      effectivenessLabel = " Tuned follow-through is stronger than raw history.";
    } else if (effectivenessDelta <= -0.1) {
      effectivenessLabel = " Tuning stayed conservative because raw history has been stronger.";
    }
    return `${controlBias.message}${deltaLabel}${effectivenessLabel}`;
  }

  function recommendationTunedEffectivenessMeta(kind) {
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const effectiveness = behavior.control_bias_effectiveness?.[kind] || {};
    const tunedRate = effectiveness.tuned_match_rate;
    const untunedRate = effectiveness.untuned_match_rate;
    if (typeof tunedRate !== "number" || typeof untunedRate !== "number") return null;
    const tunedPct = Math.round(tunedRate * 100);
    const untunedPct = Math.round(untunedRate * 100);
    if (tunedRate > untunedRate) {
      return `Tuned versions of this recommendation are followed as-is more often for this style (${tunedPct}% vs ${untunedPct}%).`;
    }
    if (untunedRate > tunedRate) {
      return `Raw versions of this recommendation are followed as-is more often for this style (${untunedPct}% vs ${tunedPct}%).`;
    }
    return `Tuned and raw versions of this recommendation are being followed at the same rate for this style (${tunedPct}%).`;
  }

  function recommendationEvidenceBadge(kind) {
    const preferredKind = report?.feedback_loop?.recommendation_rank?.preferred_kind;
    if (preferredKind !== kind) return null;
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const outcomeBias = Number(behavior.outcome_biases?.[kind] || 0);
    const rerunBias = Number(behavior.rerun_biases?.[kind] || 0);
    const adherenceBias = Number(behavior.adherence_biases?.[kind] || 0);
    const selectionBias = Number(behavior.selection_biases?.[kind] || 0);
    const effectivenessBias = Number(behavior.effectiveness_biases?.[kind] || 0);
    if (effectivenessBias > 0 && effectivenessBias >= Math.max(outcomeBias, rerunBias, adherenceBias, selectionBias, 0)) {
      return "Adaptive-trust-led";
    }
    if (outcomeBias > 0 && outcomeBias >= Math.max(rerunBias, adherenceBias, selectionBias, 0)) {
      return "Outcome-led";
    }
    if (rerunBias > 0 && rerunBias >= Math.max(outcomeBias, adherenceBias, selectionBias, 0)) {
      return "Rerun-led";
    }
    if (adherenceBias > 0 && adherenceBias >= Math.max(outcomeBias, rerunBias, selectionBias, 0)) {
      return "Adherence-led";
    }
    if (selectionBias > 0 && selectionBias >= Math.max(outcomeBias, rerunBias, adherenceBias, 0)) {
      return "Choice-led";
    }
    return "Confidence-led";
  }

  function recommendationTrustSignal(kind) {
    const preferredKind = report?.feedback_loop?.recommendation_rank?.preferred_kind;
    if (preferredKind === kind) return null;
    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const effectivenessBias = Number(behavior.effectiveness_biases?.[kind] || 0);
    if (effectivenessBias >= 0.035) {
      return "Adaptive Trust Strong";
    }
    if (effectivenessBias <= -0.035) {
      return "Adaptive Trust Weak";
    }
    return null;
  }

  function recommendationGroupLabel(kind) {
    const preferredKind = report?.feedback_loop?.recommendation_rank?.preferred_kind;
    if (preferredKind === kind) return "Preferred Right Now";
    const trustSignal = recommendationTrustSignal(kind);
    if (trustSignal === "Adaptive Trust Strong") return "Historically Trustworthy Alternative";
    if (trustSignal === "Adaptive Trust Weak") return "Lower-Trust Alternative";
    return "Alternative Option";
  }

  function orderedRecommendations() {
    const items = [
      { kind: "learned_default", data: report?.feedback_loop?.learned_default || null },
      { kind: "best_next_pass", data: report?.feedback_loop?.best_next_pass || null },
    ].filter((item) => item.data);
    const preferredKind = report?.feedback_loop?.recommendation_rank?.preferred_kind;
    return items.sort((left, right) => {
      const leftPreferred = left.kind === preferredKind ? 1 : 0;
      const rightPreferred = right.kind === preferredKind ? 1 : 0;
      if (leftPreferred !== rightPreferred) return rightPreferred - leftPreferred;

      const leftTrust = recommendationTrustSignal(left.kind);
      const rightTrust = recommendationTrustSignal(right.kind);
      const trustRank = { "Adaptive Trust Strong": 2, "Adaptive Trust Weak": 0 };
      const leftTrustRank = trustRank[leftTrust] ?? 1;
      const rightTrustRank = trustRank[rightTrust] ?? 1;
      if (leftTrustRank !== rightTrustRank) return rightTrustRank - leftTrustRank;

      const leftScore = Number(recommendationMeta(left.kind)?.score || 0);
      const rightScore = Number(recommendationMeta(right.kind)?.score || 0);
      return rightScore - leftScore;
    });
  }

  function recommendationContrastHint(kind) {
    const rank = report?.feedback_loop?.recommendation_rank;
    if (!rank || rank.preferred_kind !== kind) return null;
    const winner = rank.options?.find((item) => item.kind === kind);
    const runnerUp = rank.options?.find((item) => item.kind !== kind);
    if (!winner || !runnerUp) return null;

    const behavior = report?.feedback_loop?.recommendation_behavior || {};
    const winnerOutcome = Number(behavior.outcome_biases?.[kind] || 0);
    const runnerOutcome = Number(behavior.outcome_biases?.[runnerUp.kind] || 0);
    const winnerRerun = Number(behavior.rerun_biases?.[kind] || 0);
    const runnerRerun = Number(behavior.rerun_biases?.[runnerUp.kind] || 0);
    const winnerAdherence = Number(behavior.adherence_biases?.[kind] || 0);
    const runnerAdherence = Number(behavior.adherence_biases?.[runnerUp.kind] || 0);
    const winnerChoice = Number(behavior.selection_biases?.[kind] || 0);
    const runnerChoice = Number(behavior.selection_biases?.[runnerUp.kind] || 0);
    const winnerEffectiveness = Number(behavior.effectiveness_biases?.[kind] || 0);
    const runnerEffectiveness = Number(behavior.effectiveness_biases?.[runnerUp.kind] || 0);

    if (winnerEffectiveness > runnerEffectiveness && winnerEffectiveness > 0 && runnerEffectiveness <= 0) {
      return `${runnerUp.label} has weaker adaptive-tuning trust for this style.`;
    }

    if (winnerOutcome > runnerOutcome && winnerOutcome > 0 && runnerOutcome <= 0) {
      return `${runnerUp.label} lacks the same outcome support.`;
    }
    if (winnerRerun > runnerRerun && winnerRerun > 0 && runnerRerun <= 0) {
      return `${runnerUp.label} has weaker rerun support.`;
    }
    if (winnerAdherence > runnerAdherence && winnerAdherence > 0 && runnerAdherence <= 0) {
      return `${runnerUp.label} is adapted more often instead of trusted as-is.`;
    }
    if (winnerChoice > runnerChoice && winnerChoice > 0 && runnerChoice <= 0) {
      return `${runnerUp.label} has weaker operator preference history.`;
    }
    if (Number(winner.base_score || 0) > Number(runnerUp.base_score || 0)) {
      return `${runnerUp.label} is leaning more on memory than current confidence.`;
    }
    return `${runnerUp.label} is not carrying the stronger evidence mix right now.`;
  }

  function sectionTrustSignal(item) {
    const count = Number(item?.count || item?.position_guidance?.count || 0);
    if (count >= 4) return "Section Trust Strong";
    if (count >= 2) return "Section Trust Building";
    return null;
  }

  function sectionGroupLabel(segment) {
    if (segment?.position_guidance?.count >= 4) return "Historically Trustworthy Section";
    if (segment?.position_guidance?.count >= 2) return "Historically Sensitive Section";
    return "Section Option";
  }

  function sectionActionTrustSignal(segment, action) {
    const guidance = segment?.position_guidance || {};
    const dominantFeedback = String(guidance.dominant_feedback || "");
    const count = Number(guidance.count || 0);
    if (String(action?.feedback || "") === dominantFeedback && count >= 4) {
      return "Direction Trust Strong";
    }
    if (String(action?.feedback || "") === dominantFeedback && count >= 2) {
      return "Direction Trust Building";
    }
    return null;
  }

  function sectionActionLabel(segment, action) {
    const trust = sectionActionTrustSignal(segment, action);
    if (!trust) return action.label;
    return `${action.label} (${trust})`;
  }

  function orderedSectionActions(segment) {
    return [...(segment?.quick_actions || [])].sort((left, right) => {
      const leftTrust = sectionActionTrustSignal(segment, left);
      const rightTrust = sectionActionTrustSignal(segment, right);
      const trustRank = { "Direction Trust Strong": 2, "Direction Trust Building": 1 };
      const leftTrustRank = trustRank[leftTrust] ?? 0;
      const rightTrustRank = trustRank[rightTrust] ?? 0;
      if (leftTrustRank !== rightTrustRank) return rightTrustRank - leftTrustRank;
      const order = { warbly: 3, too_loose: 2, too_tight: 1, good: 0 };
      return (order[right?.feedback] ?? -1) - (order[left?.feedback] ?? -1);
    });
  }

  function orderedPositionGuidance() {
    return [...(report?.feedback_loop?.position_guidance?.diagnostics || [])].sort(
      (left, right) => Number(right?.count || 0) - Number(left?.count || 0),
    );
  }

  function orderedSegmentDiagnostics() {
    return [...(report?.feedback_loop?.segment_feedback_summary?.diagnostics || [])].sort((left, right) => {
      const leftCount = Number(left?.count || 0);
      const rightCount = Number(right?.count || 0);
      if (leftCount !== rightCount) return rightCount - leftCount;
      return Number(left?.segment_index || 0) - Number(right?.segment_index || 0);
    });
  }

  function orderedSegments() {
    return [...(report?.feedback_loop?.segment_feedback || [])].sort((left, right) => {
      const leftCount = Number(left?.position_guidance?.count || 0);
      const rightCount = Number(right?.position_guidance?.count || 0);
      if (leftCount !== rightCount) return rightCount - leftCount;
      const leftDelta = Number(left?.delta_vs_baseline_sec || 0);
      const rightDelta = Number(right?.delta_vs_baseline_sec || 0);
      if (leftDelta !== rightDelta) return rightDelta - leftDelta;
      return Number(left?.segment_index || 0) - Number(right?.segment_index || 0);
    });
  }

  function pct(value) {
    if (typeof value !== "number" || Number.isNaN(value)) return "n/a";
    return `${Math.round(value * 100)}%`;
  }

  function sec(value) {
    if (typeof value !== "number" || Number.isNaN(value)) return "n/a";
    return `${(value * 1000).toFixed(1)} ms`;
  }

  function clock(value) {
    if (typeof value !== "number" || Number.isNaN(value)) return "n/a";
    const total = Math.max(0, Math.round(value));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  function lockLabel(verdict) {
    if (verdict === "daw_locked") return "DAW locked";
    if (verdict === "mostly_locked") return "Mostly locked";
    if (verdict === "drifts_late") return "Drifts late";
    if (verdict === "drifts_mid_song") return "Drifts mid-song";
    if (verdict === "unstable") return "Unstable";
    return verdict || "Unknown";
  }

  function lockTone(verdict) {
    if (verdict === "daw_locked") return "good";
    if (verdict === "mostly_locked" || verdict === "drifts_late" || verdict === "drifts_mid_song") return "warn";
    if (verdict === "unstable") return "bad";
    return "neutral";
  }

  function dawLockSummary() {
    const lock = report?.metronome_lock;
    if (!lock) return null;
    const fixed = lock.fixed_windows || {};
    const continuity = lock.warp_continuity || {};
    const effectiveFixed = typeof fixed.effective_locked_ratio === "number" ? fixed.effective_locked_ratio : fixed.locked_ratio;
    return {
      verdict: lock.verdict,
      tone: lockTone(lock.verdict),
      segmentLock: lock.locked_ratio,
      fixedLock: effectiveFixed,
      rawFixedLock: fixed.locked_ratio,
      excludedWindows: fixed.excluded_windows || 0,
      unstable: Number(lock.unstable_segments || 0) + Number(fixed.unstable_windows || 0),
      meltdown: Number(lock.meltdown_segments || 0) + Number(fixed.meltdown_windows || 0),
      continuity: continuity.verdict || "unknown",
      offsetJump: continuity.max_window_offset_jump_sec,
      offsetJumpFrom: continuity.max_window_offset_jump_from_sec,
      offsetJumpTo: continuity.max_window_offset_jump_to_sec,
      offsetBefore: continuity.max_window_offset_before_sec,
      offsetAfter: continuity.max_window_offset_after_sec,
      diagnosticSummary: report?.daw_lock_diagnostics?.summary,
      avgError: report?.timing_metrics?.avg_abs_error_after_sec,
    };
  }

  const dawLock = dawLockSummary();

  return (
    <div className="panel">
      <h3>Results</h3>
      {!result && <p>Run quantization to generate outputs.</p>}
      {result && (
        <>
          <div className="download-row">
            <a className="btn secondary" href={downloadUrl(jobId, result.output_files?.browser_audio || "quantized.wav")}>Download WAV</a>
            {result.output_files?.master_audio && (
              <a className="btn secondary" href={downloadUrl(jobId, result.output_files.master_audio)}>Download Master</a>
            )}
            {result.output_files?.source_match_audio && (
              <a className="btn secondary" href={downloadUrl(jobId, result.output_files.source_match_audio)}>Download Source Match</a>
            )}
            {result.output_files?.metronome_check_audio && (
              <a className="btn secondary" href={downloadUrl(jobId, result.output_files.metronome_check_audio)}>Download Grid Check</a>
            )}
            <a className="btn secondary" href={downloadUrl(jobId, "report.json")}>Download JSON</a>
            <a className="btn secondary" href={downloadUrl(jobId, "tempo_map.mid")}>Download MIDI</a>
          </div>
          <RuntimeStrip config={report?.runtime_config} />
          {report?.timing_metrics && (
            <div className="meta-list">
              <div>Mode: {report.mode_effective}</div>
              <div>Error Before: {report.timing_metrics.avg_abs_error_before_sec.toFixed(4)} s</div>
              <div>Error After: {report.timing_metrics.avg_abs_error_after_sec.toFixed(4)} s</div>
              <div>Improvement: {report.timing_metrics.improvement_pct.toFixed(2)}%</div>
              <div>Warp Method: {report.warp_method}</div>
              {report.warp_selection && <div>Selection: {report.warp_selection}</div>}
              <div>Quantizer: {report.quantize_method}</div>
              <div>Anchors: {report.anchor_count}</div>
              {typeof report.ml_confidence === "number" && report.ml_used && (
                <div>ML Confidence: {report.ml_confidence.toFixed(3)}</div>
              )}
              {typeof report.ml_blend_alpha === "number" && report.mode_requested === "hybrid" && (
                <div>Hybrid Blend: {report.ml_blend_alpha.toFixed(3)}</div>
              )}
              {report.ml_blend_diagnostics?.ml_agreement_scale !== undefined && report.mode_requested === "hybrid" && (
                <div>ML Agreement: {report.ml_blend_diagnostics.ml_agreement_scale.toFixed(3)}</div>
              )}
            </div>
          )}
          {dawLock && (
            <div className={`lock-card ${dawLock.tone}`}>
              <div className="lock-card-head">
                <div>
                  <div className="lock-kicker">DAW Lock</div>
                  <strong>{lockLabel(dawLock.verdict)}</strong>
                </div>
                <span>{pct(dawLock.fixedLock)}</span>
              </div>
              <div className="lock-grid">
                <div>
                  <span>Segment lock</span>
                  <strong>{pct(dawLock.segmentLock)}</strong>
                </div>
                <div>
                  <span>Fixed-window lock</span>
                  <strong>{pct(dawLock.fixedLock)}</strong>
                </div>
                <div>
                  <span>Avg timing error</span>
                  <strong>{sec(dawLock.avgError)}</strong>
                </div>
                <div>
                  <span>Continuity</span>
                  <strong>{dawLock.continuity}</strong>
                </div>
              </div>
              <div className="lock-note">
                Max offset jump: {sec(dawLock.offsetJump)}. Unstable/meltdown flags: {dawLock.unstable}/{dawLock.meltdown}.
                {typeof dawLock.offsetJumpFrom === "number" && typeof dawLock.offsetJumpTo === "number" && (
                  <> Largest jump around {clock(dawLock.offsetJumpFrom)} to {clock(dawLock.offsetJumpTo)} ({sec(dawLock.offsetBefore)} to {sec(dawLock.offsetAfter)} offset).</>
                )}
                {dawLock.diagnosticSummary && (
                  <> {dawLock.diagnosticSummary}</>
                )}
                {dawLock.excludedWindows > 0 && (
                  <> Raw fixed-window lock was {pct(dawLock.rawFixedLock)}; {dawLock.excludedWindows} sparse tail window{dawLock.excludedWindows === 1 ? "" : "s"} excluded.</>
                )}
                {report?.metronome_check?.generated && (
                  <> Grid check: {report.metronome_check.target_bpm} BPM click, {report.metronome_check.beats_per_bar}/4 accents.</>
                )}
              </div>
            </div>
          )}
          {report?.feedback_loop && (
            <div className="meta-list">
              <div><strong>Feedback Loop</strong></div>
              <div>{report.feedback_loop.summary}</div>
              {report.feedback_loop.recommendation_rank?.summary && (
                <div>{report.feedback_loop.recommendation_rank.summary}</div>
              )}
              {orderedRecommendations().map((item) => (
                <div key={item.kind}>
                  <div>{recommendationGroupLabel(item.kind)}</div>
                  <button
                    className="btn secondary"
                    type="button"
                    onClick={() => onApplySuggestedControls(item.data, item.kind)}
                    disabled={feedbackState.status === "saving"}
                  >
                    {report.feedback_loop.recommendation_rank?.preferred_kind === item.kind
                      ? `${item.data.label} (Preferred)`
                      : item.data.label}
                  </button>
                  {recommendationEvidenceBadge(item.kind) && (
                    <div>
                      {recommendationEvidenceBadge(item.kind)}
                      {recommendationContrastHint(item.kind) ? ` - ${recommendationContrastHint(item.kind)}` : ""}
                    </div>
                  )}
                  {recommendationTrustSignal(item.kind) && (
                    <div>{recommendationTrustSignal(item.kind)}</div>
                  )}
                  {recommendationMeta(item.kind) && (
                    <>
                      <div>{recommendationEvidenceSummary(item.kind)}</div>
                      {recommendationControlBiasMeta(item.kind) && (
                        <div>{recommendationControlBiasMeta(item.kind)}</div>
                      )}
                      {recommendationTunedEffectivenessMeta(item.kind) && (
                        <div>{recommendationTunedEffectivenessMeta(item.kind)}</div>
                      )}
                      {latestRecommendationOutcome(item.kind) && (
                        <div>{latestRecommendationOutcome(item.kind)}</div>
                      )}
                      {latestRecommendationRerun(item.kind) && (
                        <>
                          <div>{latestRecommendationRerunBadge(item.kind)}</div>
                          <div>{latestRecommendationRerun(item.kind)}</div>
                        </>
                      )}
                    </>
                  )}
                </div>
              ))}
              {report.feedback_loop.quick_actions?.map((action) => (
                <button
                  key={action.feedback}
                  className="btn secondary"
                  type="button"
                  onClick={() => onFeedback(action)}
                  disabled={feedbackState.status === "saving"}
                >
                  {action.label}
                </button>
              ))}
              {report.feedback_loop.latest && (
                <div>Latest Feedback: {report.feedback_loop.latest.feedback}</div>
              )}
              {typeof report.feedback_loop.history_count === "number" && (
                <div>Feedback Entries: {report.feedback_loop.history_count}</div>
              )}
              {report.feedback_loop.segment_feedback_summary?.summary && (
                <div>{report.feedback_loop.segment_feedback_summary.summary}</div>
              )}
              {report.feedback_loop.position_guidance?.summary && (
                <div>{report.feedback_loop.position_guidance.summary}</div>
              )}
              {report.feedback_loop.best_next_pass?.description && (
                <div>{report.feedback_loop.best_next_pass.description}</div>
              )}
              {feedbackState.message && <div>{feedbackState.message}</div>}
            </div>
          )}
          {report?.feedback_loop?.segment_feedback?.length > 0 && (
            <div className="meta-list">
              <div><strong>Section Feedback</strong></div>
              <div>Priority Guidance</div>
              {orderedPositionGuidance().map((item) => (
                <div key={`position-${item.position_bucket}`}>
                  <div>{sectionTrustSignal(item) || "Position Guidance"}</div>
                  <div>{item.message}</div>
                  {item.suggested_controls && (
                    <button
                      className="btn secondary"
                      type="button"
                      onClick={() => onApplySuggestedControls({ label: `Apply ${item.position_bucket}`, suggested_controls: item.suggested_controls })}
                      disabled={feedbackState.status === "saving"}
                    >
                      Apply Position Retry
                    </button>
                  )}
                </div>
              ))}
              <div>Repeat Issues</div>
              {orderedSegmentDiagnostics().map((item) => (
                <div key={`diag-${item.segment_index}`}>
                  <div>{sectionTrustSignal(item) || "Repeat-Issue Retry"}</div>
                  <div>{item.message}</div>
                  {item.suggested_controls && (
                    <button
                      className="btn secondary"
                      type="button"
                      onClick={() => onApplySuggestedControls({ label: `Apply ${item.label}`, suggested_controls: item.suggested_controls })}
                      disabled={feedbackState.status === "saving"}
                    >
                      Apply Suggested Retry
                    </button>
                  )}
                </div>
              ))}
              <div>Section Cards</div>
              {orderedSegments().map((segment) => (
                <div key={segment.segment_index}>
                  <div>{sectionGroupLabel(segment)}</div>
                  <div>
                    {segment.label} ({segment.time_range_label})
                  </div>
                  {sectionTrustSignal(segment) && <div>{sectionTrustSignal(segment)}</div>}
                  <div>{segment.summary}</div>
                  {segment.timing_metrics?.avg_abs_error_after_sec !== undefined && (
                    <div>Section Error After: {segment.timing_metrics.avg_abs_error_after_sec.toFixed(4)} s</div>
                  )}
                  {typeof segment.delta_vs_baseline_sec === "number" && (
                    <div>Vs Baseline: {segment.delta_vs_baseline_sec >= 0 ? "+" : ""}{segment.delta_vs_baseline_sec.toFixed(4)} s</div>
                  )}
                  {orderedSectionActions(segment).map((action) => (
                    <button
                      key={`${segment.segment_index}-${action.feedback}`}
                      className="btn secondary"
                      type="button"
                      onClick={() => onFeedback(action, segment.segment_index)}
                      disabled={feedbackState.status === "saving"}
                    >
                      {sectionActionLabel(segment, action)}
                    </button>
                  ))}
                  {segment.latest && <div>Latest Section Feedback: {segment.latest.feedback}</div>}
                  {typeof segment.history_count === "number" && <div>Section Entries: {segment.history_count}</div>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
