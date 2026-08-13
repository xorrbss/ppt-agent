import type {
  AuthoredOcrStatus,
  AuthoredQualityEvidenceAtom,
  AuthoredQualityEvidenceState,
  AuthoredQualityIncompleteMeasurement,
  AuthoredQualityReviewElementChange,
  AuthoredQualityReviewMetrics,
  AuthoredQualityReviewOcrCheck,
  AuthoredQualityReviewOcrComparison,
  AuthoredQualityReviewOcrOmissions,
  AuthoredQualityReviewPercentiles,
  AuthoredQualityReviewResult,
  AuthoredQualityReviewSlideResult,
  AuthoredQualityReviewStageUsage,
  AuthoredReviewRepairPath,
} from "./authored";

import { deriveResolvedOmissions, firstDefined, isRecord, normalizeApplied, normalizeBoolean, normalizeIndexList, normalizeIssueList, normalizeNumber, normalizeOcrCheck, normalizeOcrOmissions, normalizeOcrStatus, normalizeOptionalString, normalizeRepairPath, normalizeStringList, normalizeUnattributedIssues } from "./authored-quality-normalize-base";
import { normalizeDisplayValue, normalizeElementChange, normalizeEvidenceAtoms, normalizeEvidenceState, normalizeIncompleteMeasurements, normalizeRepairBlockers, normalizeSlideResult } from "./authored-quality-normalize-evidence";
import { normalizeLatencyPercentiles, normalizePercentiles, normalizeSlideOcr, normalizeStageTimings, normalizeStageUsage } from "./authored-quality-normalize-metrics";

export function normalizeAuthoredQualityReviewResult(
  value: unknown,
): AuthoredQualityReviewResult {
  const payload = isRecord(value) ? value : {};
  const rawSlideResults = Array.isArray(payload.slide_results)
    ? payload.slide_results
    : Array.isArray(payload.review_details)
      ? payload.review_details
      : Array.isArray(payload.change_details)
        ? payload.change_details
        : [];
  const metricsSource = isRecord(payload.metrics)
    ? payload.metrics
    : isRecord(payload.telemetry)
      ? payload.telemetry
      : {};
  // SlideSpec's server contract groups low-cardinality operational counters.
  // Preserve the compact legacy fields above as the public compatibility layer,
  // rather than making the UI depend on a particular pipeline implementation.
  const solverMetrics = isRecord(metricsSource.solver)
    ? metricsSource.solver
    : isRecord(payload.solver)
      ? payload.solver
      : {};
  const candidateMetrics = isRecord(metricsSource.candidates)
    ? metricsSource.candidates
    : isRecord(payload.candidates)
      ? payload.candidates
      : {};
  const llmMetrics = isRecord(metricsSource.llm)
    ? metricsSource.llm
    : isRecord(metricsSource.llm_funnel)
      ? metricsSource.llm_funnel
      : isRecord(payload.llm)
        ? payload.llm
        : {};
  const transactionMetrics = isRecord(metricsSource.transaction)
    ? metricsSource.transaction
    : isRecord(payload.transaction)
      ? payload.transaction
      : {};
  const rolloutMetrics = isRecord(metricsSource.rollout)
    ? metricsSource.rollout
    : isRecord(payload.rollout)
      ? payload.rollout
      : {};
  const stageTimings = normalizeStageTimings(
    metricsSource.stage_timings_ms ??
      metricsSource.stage_timings ??
      payload.stage_timings_ms,
  );
  const metrics: AuthoredQualityReviewMetrics = {
    stage_timings_ms: stageTimings,
  };
  const derivedAggregateStageTime = Object.values(stageTimings).reduce(
    (total, duration) => total + duration,
    0,
  );
  const latencyPercentiles = normalizeLatencyPercentiles(
    firstDefined(
      metricsSource.latency_percentiles,
      metricsSource.percentiles,
      payload.latency_percentiles,
    ),
  );
  if (latencyPercentiles) metrics.latency_percentiles = latencyPercentiles;
  const stageUsage = normalizeStageUsage(
    firstDefined(
      metricsSource.stage_usage,
      metricsSource.usage_by_stage,
      payload.stage_usage,
    ),
  );
  if (stageUsage) metrics.stage_usage = stageUsage;
  const metricAliases = {
    wall_time_ms: ["wall_ms", "elapsed_ms"],
    aggregate_stage_time_ms: ["stage_aggregate_ms", "aggregate_ms"],
    total_ms: [],
    cache_hits: [],
    cache_misses: [],
    llm_eligible: ["llm_eligible_count", "eligible", "eligible_count"],
    llm_calls: [],
    llm_responses: ["llm_response_count", "provider_responses"],
    llm_valid_plans: ["llm_valid_plan_count", "valid_plans"],
    llm_schema_passes: ["llm_schema_pass_count", "schema_passes"],
    llm_provider_failures: ["llm_provider_failure_count", "provider_failures"],
    llm_empty: ["llm_empty_count", "empty_responses"],
    llm_schema_rejected: ["llm_schema_rejects", "llm_schema_reject_count", "schema_rejected"],
    proposal_preflight_unprojectable: [
      "proposal_preflight_rejected",
      "proposal_preflight_unprojectable_count",
      "preflight_unprojectable",
    ],
    llm_preflight_passes: ["llm_preflight_pass_count", "preflight_passes"],
    llm_rendered: ["llm_render_count", "llm_rendered_count", "rendered"],
    llm_negative_cache_hits: ["negative_cache_hits", "negative_cache_hit_count", "llm_cache_negative_hits"],
    llm_circuit_open: ["llm_circuit_open_count", "circuit_open", "circuit_open_count"],
    llm_candidates: [],
    render_calls: [],
    dom_calls: [],
    vqa_calls: [],
    patch_count: [],
    fallback_count: [],
    patch_attempted: ["patch_attempts"],
    patch_succeeded: ["patch_successes"],
    patch_failed: ["patch_failures"],
    fallback_attempted: ["fallback_attempts"],
    fallback_succeeded: ["fallback_successes"],
    fallback_failed: ["fallback_failures"],
    candidate_proposed: [],
    candidate_rendered: [],
    candidate_accepted: [],
    validation_passed: [],
    validation_rejected: [],
    validation_inconclusive: [],
    apply_committed: [],
    publish_active: [],
    postverify_passed: [],
    applied: [],
    rolled_back: [],
    legacy_preserved: [],
    corrected_false_positives: ["corrected_false_positive_count"],
    true_positive_executor_verified: [],
    true_positive_pending: [],
    solver_total_ms: ["solver_ms", "solver_duration_ms"],
    solver_branches_evaluated: ["solver_branches", "branches_evaluated"],
    solver_unknown_hard: ["solver_unknown_count", "unknown_hard_count"],
    solver_unsat_core_size: ["unsat_core_size"],
    deterministic_resolved: [],
    llm_resolved: [],
    pattern_cluster_count: [],
    pattern_representative_count: ["pattern_representatives"],
    pattern_recipe_count: [],
    pattern_reuse_attempts: [],
    pattern_reuse_hits: [],
    pattern_reuse_applied: [],
    composite_plan_count: [],
    composite_operations_merged: [],
    render_candidates_rejected_early: ["early_render_rejections"],
    full_render_count: ["full_renders"],
    llm_pattern_call_count: ["llm_pattern_calls"],
    individual_fallback_count: ["individual_fallbacks"],
  } as const;
  for (const [key, aliases] of Object.entries(metricAliases) as Array<
    [keyof typeof metricAliases, readonly string[]]
  >) {
    const candidates = [key, ...aliases];
    const raw = candidates
      .map((candidate) => metricsSource[candidate] ?? payload[candidate])
      .find((candidate) => candidate !== undefined);
    const normalized = normalizeNumber(raw);
    if (normalized !== undefined) metrics[key] = normalized;
  }
  if (
    metrics.aggregate_stage_time_ms === undefined &&
    Object.keys(stageTimings).length > 0
  ) {
    metrics.aggregate_stage_time_ms = derivedAggregateStageTime;
  }
  // `total_ms` was the original wall-clock field. Keep it readable but do not
  // use it as an aggregate stage duration when the explicit field is absent.
  if (metrics.wall_time_ms === undefined && metrics.total_ms !== undefined) {
    metrics.wall_time_ms = metrics.total_ms;
  }
  const assignNestedMetric = (
    key:
      | "solver_total_ms"
      | "solver_branches_evaluated"
      | "solver_unknown_hard"
      | "solver_unsat_core_size"
      | "candidate_proposed"
      | "candidate_rendered"
      | "candidate_accepted"
      | "validation_rejected"
      | "rolled_back",
    value: unknown,
  ) => {
    const normalized = normalizeNumber(value);
    if (normalized !== undefined) metrics[key] = normalized;
  };
  assignNestedMetric(
    "solver_total_ms",
    firstDefined(metrics.solver_total_ms, solverMetrics.elapsed_ms),
  );
  assignNestedMetric(
    "solver_branches_evaluated",
    firstDefined(metrics.solver_branches_evaluated, solverMetrics.branches),
  );
  assignNestedMetric(
    "solver_unknown_hard",
    firstDefined(metrics.solver_unknown_hard, solverMetrics.unknown),
  );
  assignNestedMetric(
    "solver_unsat_core_size",
    firstDefined(metrics.solver_unsat_core_size, solverMetrics.unsat_core_size),
  );
  assignNestedMetric(
    "candidate_proposed",
    firstDefined(metrics.candidate_proposed, candidateMetrics.proposed),
  );
  assignNestedMetric(
    "candidate_rendered",
    firstDefined(metrics.candidate_rendered, candidateMetrics.rendered),
  );
  assignNestedMetric(
    "candidate_accepted",
    firstDefined(metrics.candidate_accepted, candidateMetrics.accepted),
  );
  assignNestedMetric(
    "validation_rejected",
    firstDefined(metrics.validation_rejected, candidateMetrics.rejected),
  );
  assignNestedMetric(
    "rolled_back",
    firstDefined(metrics.rolled_back, transactionMetrics.rollback_count),
  );
  const assignLlmMetric = (
    key:
      | "llm_calls"
      | "llm_responses"
      | "llm_valid_plans"
      | "llm_schema_passes"
      | "llm_provider_failures"
      | "llm_empty"
      | "llm_schema_rejected"
      | "proposal_preflight_unprojectable"
      | "llm_preflight_passes"
      | "llm_rendered"
      | "llm_negative_cache_hits"
      | "llm_eligible"
      | "llm_circuit_open",
    ...values: unknown[]
  ) => {
    const normalized = normalizeNumber(firstDefined(metrics[key], ...values));
    if (normalized !== undefined) metrics[key] = normalized;
  };
  assignLlmMetric("llm_calls", llmMetrics.calls, llmMetrics.call_count);
  assignLlmMetric("llm_eligible", llmMetrics.eligible, llmMetrics.eligible_count);
  assignLlmMetric("llm_responses", llmMetrics.responses, llmMetrics.response_count);
  assignLlmMetric("llm_valid_plans", llmMetrics.valid_plans, llmMetrics.valid_plan_count);
  assignLlmMetric("llm_schema_passes", llmMetrics.schema_passes, llmMetrics.schema_pass_count);
  assignLlmMetric("llm_provider_failures", llmMetrics.provider_failures, llmMetrics.provider_failure_count);
  assignLlmMetric("llm_empty", llmMetrics.empty, llmMetrics.empty_count);
  assignLlmMetric("llm_schema_rejected", llmMetrics.schema_rejected, llmMetrics.schema_rejects);
  assignLlmMetric(
    "proposal_preflight_unprojectable",
    llmMetrics.preflight_unprojectable,
    llmMetrics.proposal_preflight_unprojectable,
  );
  assignLlmMetric("llm_preflight_passes", llmMetrics.preflight_passes, llmMetrics.preflight_pass_count);
  assignLlmMetric("llm_rendered", llmMetrics.rendered, llmMetrics.render_count);
  assignLlmMetric("llm_negative_cache_hits", llmMetrics.negative_cache_hits);
  assignLlmMetric("llm_circuit_open", llmMetrics.circuit_open, llmMetrics.circuit_open_count);
  const noCandidateReasons = normalizeStringList(
    firstDefined(
      metricsSource.no_candidate_reasons,
      payload.no_candidate_reasons,
      llmMetrics.no_candidate_reasons,
    ),
  );
  if (noCandidateReasons.length > 0) {
    metrics.llm_no_candidate_reasons = noCandidateReasons;
  }
  const killSwitchActive = normalizeBoolean(
    firstDefined(
      metricsSource.kill_switch_active,
      payload.kill_switch_active,
      rolloutMetrics.kill_switch_enabled,
      isRecord(payload.routing) ? payload.routing.kill_switch_active : undefined,
    ),
  );
  if (killSwitchActive !== undefined) metrics.kill_switch_active = killSwitchActive;
  const solverSearchComplete = normalizeBoolean(
    firstDefined(
      metricsSource.solver_search_complete,
      payload.solver_search_complete,
      solverMetrics.search_complete,
    ),
  );
  if (solverSearchComplete !== undefined) {
    metrics.solver_search_complete = solverSearchComplete;
  }
  const ocrExecuted = normalizeBoolean(
    firstDefined(metricsSource.ocr_executed, payload.ocr_executed),
  );
  if (ocrExecuted !== undefined) metrics.ocr_executed = ocrExecuted;
  const ocrMetricAliases = {
    ocr_calls: ["ocr_call_count", "ocr_requests"],
    ocr_cache_hits: ["ocr_cache_hit_count", "ocr_cached"],
    ocr_total_ms: ["ocr_ms", "ocr_duration_ms"],
  } as const;
  for (const [key, aliases] of Object.entries(ocrMetricAliases) as Array<
    [keyof typeof ocrMetricAliases, readonly string[]]
  >) {
    const raw =
      [key, ...aliases]
        .map((candidate) => metricsSource[candidate] ?? payload[candidate])
        .find((candidate) => candidate !== undefined) ??
      (key === "ocr_total_ms" ? stageTimings.ocr : undefined);
    const normalized = normalizeNumber(raw);
    if (normalized !== undefined) metrics[key] = normalized;
  }
  const fixedSlideIndices = normalizeIndexList(payload.fixed_slide_indices);
  const fixedSlideIndexSet = new Set(fixedSlideIndices);
  const ocrChecks = new Map<number, AuthoredQualityReviewOcrCheck>();
  const rawOcrChecks = Array.isArray(payload.ocr_checks)
    ? payload.ocr_checks
    : [];
  for (const rawCheck of rawOcrChecks) {
    if (
      !isRecord(rawCheck) ||
      typeof rawCheck.slide_index !== "number" ||
      !Number.isInteger(rawCheck.slide_index) ||
      rawCheck.slide_index < 0
    )
      continue;
    const normalized = normalizeOcrCheck(rawCheck);
    if (normalized) ocrChecks.set(rawCheck.slide_index, normalized);
  }
  const slideResults = rawSlideResults.flatMap((slide) => {
    if (!isRecord(slide) || typeof slide.slide_index !== "number") return [];
    const ocr = normalizeSlideOcr(slide) ?? ocrChecks.get(slide.slide_index);
    const normalized = normalizeSlideResult(slide, fixedSlideIndexSet, ocr);
    return normalized ? [normalized] : [];
  });
  const listedSlides = new Set(slideResults.map((slide) => slide.slide_index));
  for (const [slideIndex, ocr] of ocrChecks) {
    if (listedSlides.has(slideIndex)) continue;
    slideResults.push({
      slide_index: slideIndex,
      attempted: false,
      accepted: false,
      applied: false,
      apply_status: "not_started",
      publish_status: "not_started",
      post_verify_status: "not_started",
      rolled_back: false,
      candidate_count: 0,
      candidate_rejection_reasons: [],
      repair_blockers: [],
      corrected_false_positive: false,
      detected_issues: [],
      evidence_atoms: [],
      incomplete_measurements: [],
      publish_blockers: [],
      confirmed_issue_count: 0,
      unresolved_confirmed_issue_count: 0,
      element_changes: [],
      resolved_issues: [],
      remaining_issues: [],
      path: "none",
      ocr,
    });
  }
  slideResults.sort((left, right) => left.slide_index - right.slide_index);
  const confirmedIssueCount = normalizeNumber(payload.confirmed_issue_count) ??
    slideResults.reduce((total, slide) => total + slide.confirmed_issue_count, 0);
  const incompleteMeasurementCount = normalizeNumber(
    payload.incomplete_measurement_count,
  ) ?? slideResults.reduce(
    (total, slide) => total + slide.incomplete_measurements.length,
    0,
  );
  const unresolvedConfirmedIssueCount = normalizeNumber(
    payload.unresolved_confirmed_issue_count,
  ) ?? slideResults.reduce(
    (total, slide) => total + slide.unresolved_confirmed_issue_count,
    0,
  );
  const cleanSlideCount = normalizeNumber(payload.clean_slide_count) ??
    Math.max(0, (normalizeNumber(payload.reviewed_count) ?? 0) - new Set(
      slideResults
        .filter((slide) =>
          slide.confirmed_issue_count > 0 ||
          slide.incomplete_measurements.length > 0 ||
          slide.remaining_issues.length > 0,
        )
        .map((slide) => slide.slide_index),
    ).size);
  const detailedIssueCount = normalizeNumber(payload.detailed_issue_count) ??
    confirmedIssueCount;
  const resolvedIssueCount = normalizeNumber(payload.resolved_issue_count) ??
    Math.max(0, detailedIssueCount - unresolvedConfirmedIssueCount);
  const remainingSlideIndices = normalizeIndexList(
    payload.remaining_slide_indices ?? payload.remaining_issue_indices,
  );
  const remainingSlideCount = normalizeNumber(payload.remaining_slide_count) ??
    remainingSlideIndices.length;
  const unattributedIssues = normalizeUnattributedIssues(payload, slideResults);
  return {
    presentation_id:
      typeof payload.presentation_id === "string"
        ? payload.presentation_id
        : undefined,
    mode: payload.mode === "analyze_and_fix" ? payload.mode : undefined,
    scope:
      payload.scope === "all" ||
      payload.scope === "current" ||
      payload.scope === "subset"
        ? payload.scope
        : undefined,
    reviewed_count: normalizeNumber(payload.reviewed_count) ?? 0,
    issue_slide_count: normalizeNumber(payload.issue_slide_count) ?? 0,
    detailed_issue_count: detailedIssueCount,
    fixed_count: normalizeNumber(payload.fixed_count) ?? 0,
    resolved_issue_count: resolvedIssueCount,
    fixed_slide_indices: fixedSlideIndices,
    remaining_slide_count: remainingSlideCount,
    remaining_issue_count: normalizeNumber(payload.remaining_issue_count) ?? 0,
    remaining_slide_indices: remainingSlideIndices,
    version_saved: payload.version_saved === true,
    confirmed_issue_count: confirmedIssueCount,
    incomplete_measurement_count: incompleteMeasurementCount,
    unresolved_confirmed_issue_count: unresolvedConfirmedIssueCount,
    clean_slide_count: cleanSlideCount,
    unattributed_issues: unattributedIssues,
    slide_results: slideResults,
    metrics,
  };
}
