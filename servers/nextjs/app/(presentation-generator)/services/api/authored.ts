import { getApiUrl } from "@/utils/api";

import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";

export interface AuthoredStylePreview {
  bg: string;
  accent: string;
  palette: string[];
  variant: string;
  image?: string;
}

/** Public, backwards-compatible result contract for authored quality review. */
export type AuthoredReviewRepairPath = "local_patch" | "full_fallback" | "none";

export interface AuthoredQualityReviewElementChange {
  element_id?: string;
  before?: string;
  after?: string;
  /** Whether this element-level change was retained in the saved presentation. */
  applied: boolean;
  resolved_issues: string[];
  remaining_issues: string[];
  path: AuthoredReviewRepairPath;
  /** Exact presentation properties changed by this candidate, when available. */
  changed_properties: string[];
}

export type AuthoredOcrStatus =
  "executed" | "routed_skip" | "unavailable" | "timeout" | "unknown";

/** Counts only: OCR source text is intentionally not retained for UI display. */
export interface AuthoredQualityReviewOcrOmissions {
  text: number;
  numbers: number;
  symbols: number;
}

export interface AuthoredQualityReviewOcrComparison {
  accepted?: boolean;
  strict_improvement?: boolean;
  no_regression?: boolean;
  reason?: string | null;
  resolved?: AuthoredQualityReviewOcrOmissions;
  remaining?: AuthoredQualityReviewOcrOmissions;
  regressions?: AuthoredQualityReviewOcrOmissions;
}

export interface AuthoredQualityReviewOcrCheck {
  status: AuthoredOcrStatus;
  executed: boolean;
  reason?: string | null;
  baseline?: AuthoredQualityReviewOcrOmissions;
  candidate?: AuthoredQualityReviewOcrOmissions;
  comparison?: AuthoredQualityReviewOcrComparison;
}

export interface AuthoredQualityReviewSlideResult {
  slide_index: number;
  /** A repair candidate was evaluated for this slide. */
  attempted: boolean;
  /** The evaluated repair was retained in the saved presentation. */
  applied: boolean;
  /** The candidate passed validation; this is not evidence that it was published. */
  accepted: boolean;
  apply_status: string;
  publish_status: string;
  post_verify_status: string;
  rolled_back: boolean;
  /** Why an attempted repair was not retained, when supplied by the backend. */
  rejection_reason?: string | null;
  /** Count of plans actually proposed; trigger/attempted alone is not a candidate. */
  candidate_count: number;
  /** Candidate-specific rejection codes, excluding preflight/no-candidate exits. */
  candidate_rejection_reasons: string[];
  /** Human review corrected a detector hit; source content was intentionally unchanged. */
  corrected_false_positive: boolean;
  correction_reason?: string | null;
  /** Issues reported by the detector before proposal generation. */
  detected_issues: string[];
  element_changes: AuthoredQualityReviewElementChange[];
  resolved_issues: string[];
  remaining_issues: string[];
  path: AuthoredReviewRepairPath;
  ocr?: AuthoredQualityReviewOcrCheck;
}

export interface AuthoredQualityReviewPercentiles {
  p50_ms?: number;
  p95_ms?: number;
  p99_ms?: number;
  samples?: number;
}

export interface AuthoredQualityReviewStageUsage {
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cost_usd?: number;
  cache_hits?: number;
  cache_misses?: number;
}

export interface AuthoredQualityReviewMetrics {
  stage_timings_ms: Record<string, number>;
  total_ms?: number;
  cache_hits?: number;
  cache_misses?: number;
  llm_calls?: number;
  llm_candidates?: number;
  render_calls?: number;
  dom_calls?: number;
  vqa_calls?: number;
  patch_count?: number;
  fallback_count?: number;
  patch_attempted?: number;
  patch_succeeded?: number;
  patch_failed?: number;
  fallback_attempted?: number;
  fallback_succeeded?: number;
  fallback_failed?: number;
  candidate_proposed?: number;
  candidate_rendered?: number;
  candidate_accepted?: number;
  validation_passed?: number;
  validation_rejected?: number;
  validation_inconclusive?: number;
  apply_committed?: number;
  publish_active?: number;
  postverify_passed?: number;
  applied?: number;
  rolled_back?: number;
  legacy_preserved?: number;
  corrected_false_positives?: number;
  /** True positives proven by canonical executor evidence (not UI inference). */
  true_positive_executor_verified?: number;
  /** True positives that remain unresolved because canonical evidence is absent or incomplete. */
  true_positive_pending?: number;
  /** Low-cardinality deterministic layout solver telemetry. */
  solver_total_ms?: number;
  solver_branches_evaluated?: number;
  solver_unknown_hard?: number;
  solver_unsat_core_size?: number;
  solver_search_complete?: boolean;
  /** True only when the server-side SlideSpec rollback switch routed to legacy. */
  kill_switch_active?: boolean;
  deterministic_resolved?: number;
  llm_resolved?: number;
  ocr_executed?: boolean;
  ocr_calls?: number;
  ocr_cache_hits?: number;
  ocr_total_ms?: number;
  /** Per-stage percentile snapshots, separated by cold and warm replay runs. */
  latency_percentiles?: {
    cold?: Record<string, AuthoredQualityReviewPercentiles>;
    warm?: Record<string, AuthoredQualityReviewPercentiles>;
  };
  /** Token, cost, and cache accounting by pipeline stage. */
  stage_usage?: Record<string, AuthoredQualityReviewStageUsage>;
}

export interface AuthoredQualityReviewResult {
  presentation_id?: string;
  mode?: "analyze_and_fix";
  scope?: "all" | "current" | "subset";
  reviewed_count: number;
  issue_slide_count: number;
  fixed_count: number;
  fixed_slide_indices: number[];
  remaining_issue_count: number;
  remaining_slide_indices: number[];
  version_saved: boolean;
  slide_results: AuthoredQualityReviewSlideResult[];
  metrics: AuthoredQualityReviewMetrics;
}

export const AUTHORED_STYLE_CATEGORIES = [
  "general",
  "business",
  "technology",
  "research",
  "editorial",
  "creative",
] as const;

export type AuthoredStyleCategory = (typeof AUTHORED_STYLE_CATEGORIES)[number];

export interface AuthoredStyleSummary {
  id: string;
  name: string;
  description: string;
  category: AuthoredStyleCategory;
  tags: string[];
  use_cases: string[];
  preview: AuthoredStylePreview;
}

export const DEFAULT_AUTHORED_STYLE: AuthoredStyleSummary = {
  id: "default",
  name: "기본 블루프린트",
  description: "깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일",
  category: "general",
  tags: ["범용", "깔끔한", "컨설팅"],
  use_cases: ["회사 소개", "프로젝트 제안", "일반 보고서"],
  preview: {
    bg: "#F8FAFC",
    accent: "#2563EB",
    palette: ["#F8FAFC", "#FFFFFF", "#0F172A", "#64748B", "#2563EB"],
    variant: "clean-light",
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();
  return value.flatMap((item) => {
    if (typeof item !== "string") return [];
    const normalized = item.trim();
    if (!normalized || seen.has(normalized)) return [];
    seen.add(normalized);
    return [normalized];
  });
}

function normalizeNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

function normalizeIndexList(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return [
    ...new Set(
      value.filter(
        (item): item is number =>
          typeof item === "number" && Number.isInteger(item) && item >= 0,
      ),
    ),
  ].sort((left, right) => left - right);
}

function normalizeIssueList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string") return item.trim() ? [item.trim()] : [];
    if (!isRecord(item)) return [];
    const detail = item.detail ?? item.message ?? item.code;
    return typeof detail === "string" && detail.trim() ? [detail.trim()] : [];
  });
}

function normalizeRepairPath(value: unknown): AuthoredReviewRepairPath {
  return value === "local_patch" ||
    value === "full_fallback" ||
    value === "none"
    ? value
    : "none";
}

function normalizeOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeApplied(value: unknown, legacyValue: boolean): boolean {
  return typeof value === "boolean" ? value : legacyValue;
}

function normalizeBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function firstDefined(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined);
}

function normalizeOcrStatus(value: unknown): AuthoredOcrStatus | undefined {
  if (typeof value !== "string") return undefined;
  switch (value.trim().toLowerCase().replace(/[ -]/g, "_")) {
    case "executed":
    case "complete":
    case "completed":
    case "ready":
    case "success":
      return "executed";
    case "routed_skip":
    case "routing_skip":
    case "skipped":
    case "local_routing_skip":
      return "routed_skip";
    case "unavailable":
    case "not_available":
    case "disabled":
      return "unavailable";
    case "timeout":
    case "timed_out":
      return "timeout";
    default:
      return "unknown";
  }
}

function normalizeOcrOmissions(
  value: unknown,
): AuthoredQualityReviewOcrOmissions | undefined {
  if (!isRecord(value)) return undefined;
  const source = isRecord(value.omissions)
    ? value.omissions
    : isRecord(value.missing)
      ? value.missing
      : value;
  const count = (candidate: unknown): number | undefined => {
    if (Array.isArray(candidate)) return candidate.length;
    const numeric = normalizeNumber(candidate);
    if (numeric !== undefined) return numeric;
    if (!isRecord(candidate)) return undefined;
    return normalizeNumber(
      firstDefined(candidate.count, candidate.total, candidate.length),
    );
  };
  const text = count(
    firstDefined(
      source.text,
      source.texts,
      source.missing_text,
      source.text_omissions,
    ),
  );
  const numbers = count(
    firstDefined(
      source.numbers,
      source.number,
      source.missing_numbers,
      source.number_omissions,
    ),
  );
  const symbols = count(
    firstDefined(
      source.symbols,
      source.symbol,
      source.missing_symbols,
      source.symbol_omissions,
    ),
  );
  if (text === undefined && numbers === undefined && symbols === undefined)
    return undefined;
  return { text: text ?? 0, numbers: numbers ?? 0, symbols: symbols ?? 0 };
}

function deriveResolvedOmissions(
  baseline?: AuthoredQualityReviewOcrOmissions,
  candidate?: AuthoredQualityReviewOcrOmissions,
): AuthoredQualityReviewOcrOmissions | undefined {
  if (!baseline || !candidate) return undefined;
  return {
    text: Math.max(0, baseline.text - candidate.text),
    numbers: Math.max(0, baseline.numbers - candidate.numbers),
    symbols: Math.max(0, baseline.symbols - candidate.symbols),
  };
}

function normalizeOcrCheck(
  value: unknown,
): AuthoredQualityReviewOcrCheck | undefined {
  if (!isRecord(value)) return undefined;
  const hasMetrics = isRecord(value.metrics);
  const comparisonSource = isRecord(value.comparison) ? value.comparison : {};
  const executed = normalizeBoolean(
    firstDefined(value.executed, value.ocr_executed),
  );
  const available = normalizeBoolean(value.available);
  const status = normalizeOcrStatus(
    firstDefined(value.status, value.ocr_status),
  );
  const reason = normalizeOptionalString(
    firstDefined(
      value.failure_reason,
      value.skip_reason,
      value.reason,
      value.error,
      value.message,
    ),
  );
  // The current backend exposes compact, privacy-safe omission counts at the
  // top level while retaining nested result metadata for diagnostics. Prefer
  // those counts; the nested object intentionally contains no OCR source text.
  const baseline = normalizeOcrOmissions(
    firstDefined(value.baseline_omissions, value.baseline),
  );
  const candidate = normalizeOcrOmissions(
    firstDefined(value.candidate_omissions, value.candidate),
  );
  const comparison: AuthoredQualityReviewOcrComparison = {
    accepted: normalizeBoolean(
      firstDefined(comparisonSource.accepted, value.accepted),
    ),
    strict_improvement: normalizeBoolean(
      firstDefined(
        comparisonSource.strict_improvement,
        value.strict_improvement,
      ),
    ),
    no_regression: normalizeBoolean(
      firstDefined(comparisonSource.no_regression, value.no_regression),
    ),
    reason: normalizeOptionalString(
      firstDefined(comparisonSource.reason, comparisonSource.failure_reason),
    ),
    resolved: normalizeOcrOmissions(
      firstDefined(comparisonSource.resolved, value.resolved),
    ),
    remaining: normalizeOcrOmissions(
      firstDefined(comparisonSource.remaining, value.remaining),
    ),
    regressions: normalizeOcrOmissions(
      firstDefined(
        comparisonSource.regressions,
        comparisonSource.regression,
        value.regressions,
      ),
    ),
  };
  if (!comparison.resolved)
    comparison.resolved = deriveResolvedOmissions(baseline, candidate);
  const hasComparison = Object.values(comparison).some(
    (item) => item !== undefined && item !== null,
  );
  const hasEvidence =
    executed !== undefined ||
    available !== undefined ||
    status !== undefined ||
    reason !== null ||
    baseline !== undefined ||
    candidate !== undefined ||
    hasComparison ||
    hasMetrics ||
    value.routed !== undefined;
  if (!hasEvidence) return undefined;
  const inferredStatus =
    status ??
    (executed === true
      ? "executed"
      : available === false
        ? "unavailable"
        : value.routed === false || reason !== null
          ? "routed_skip"
          : "unknown");
  return {
    status: inferredStatus,
    executed: executed ?? inferredStatus === "executed",
    ...(reason === null ? {} : { reason }),
    ...(baseline ? { baseline } : {}),
    ...(candidate ? { candidate } : {}),
    ...(hasComparison ? { comparison } : {}),
  };
}

function normalizeDisplayValue(value: unknown): string | undefined {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  return undefined;
}

function normalizeElementChange(
  value: unknown,
  legacyApplied: boolean,
): AuthoredQualityReviewElementChange | null {
  if (!isRecord(value)) return null;
  const path = normalizeRepairPath(value.path);
  const change: AuthoredQualityReviewElementChange = {
    applied: normalizeApplied(value.applied, legacyApplied),
    resolved_issues: normalizeIssueList(value.resolved_issues),
    remaining_issues: normalizeIssueList(value.remaining_issues),
    path,
    changed_properties: [],
  };
  const elementId = value.element_id ?? value.elementId ?? value.qid;
  const before = normalizeDisplayValue(value.before);
  const after = normalizeDisplayValue(value.after);
  const changedProperties = normalizeStringList(
    firstDefined(value.changed_properties, value.changedProperties, value.properties),
  );
  if (typeof elementId === "string" && elementId.trim()) {
    change.element_id = elementId.trim();
  }
  if (before !== undefined) change.before = before;
  if (after !== undefined) change.after = after;
  change.changed_properties = changedProperties;
  return change;
}

function normalizeSlideResult(
  value: unknown,
  fixedSlideIndices: ReadonlySet<number>,
  ocr?: AuthoredQualityReviewOcrCheck,
): AuthoredQualityReviewSlideResult | null {
  if (!isRecord(value)) {
    return null;
  }
  const slideIndex = value.slide_index;
  if (
    typeof slideIndex !== "number" ||
    !Number.isInteger(slideIndex) ||
    slideIndex < 0
  ) {
    return null;
  }
  const path = normalizeRepairPath(value.path);
  const rawChanges = Array.isArray(value.element_changes)
    ? value.element_changes
    : Array.isArray(value.changes)
      ? value.changes
      : [];
  const rejectionReason = normalizeOptionalString(value.rejection_reason);
  const correctionReason = normalizeOptionalString(
    value.correction_reason ?? value.false_positive_reason,
  );
  const candidateMetrics = isRecord(value.metrics) && isRecord(value.metrics.candidates)
    ? value.metrics.candidates
    : {};
  const rawOutcomes = Array.isArray(value.outcomes) ? value.outcomes : [];
  const candidateCount = normalizeNumber(
    firstDefined(
      value.candidate_count,
      value.candidate_proposed,
      candidateMetrics.proposed,
    ),
  ) ?? rawOutcomes.filter((outcome) => isRecord(outcome) && outcome.proposal_status === "proposed").length;
  const candidateRejectionReasons = [
    ...rawOutcomes.flatMap((outcome) =>
      isRecord(outcome) && outcome.validation_status === "rejected"
        ? normalizeIssueList(outcome.reason_codes)
        : [],
    ),
  ];
  if (Array.isArray(value.candidate_rejections)) {
    for (const rejection of value.candidate_rejections) {
      if (isRecord(rejection)) candidateRejectionReasons.push(...normalizeIssueList(rejection.reason_codes));
    }
  }
  // Older payloads did not expose acceptance. Only a fixed-slide index is
  // reliable evidence of a saved change; path alone may describe a rejected
  // candidate.
  const applied = normalizeApplied(
    value.applied,
    fixedSlideIndices.has(slideIndex),
  );
  const attempted =
    typeof value.attempted === "boolean"
      ? value.attempted
      : path !== "none" || rawChanges.length > 0 || rejectionReason !== null;
  const accepted = value.accepted === true;
  const changes = rawChanges.flatMap((change) => {
    const normalized = normalizeElementChange(change, applied);
    return normalized ? [normalized] : [];
  });
  return {
    slide_index: slideIndex,
    attempted,
    accepted,
    applied,
    apply_status:
      typeof value.apply_status === "string" ? value.apply_status : "not_started",
    publish_status:
      typeof value.publish_status === "string"
        ? value.publish_status
        : "not_started",
    post_verify_status:
      typeof value.post_verify_status === "string"
        ? value.post_verify_status
        : "not_started",
    rolled_back: value.rolled_back === true,
    ...(rejectionReason === null ? {} : { rejection_reason: rejectionReason }),
    candidate_count: candidateCount,
    candidate_rejection_reasons: [...new Set(candidateRejectionReasons)],
    corrected_false_positive:
      value.corrected_false_positive === true ||
      value.detector_disposition === "false_positive",
    ...(correctionReason === null ? {} : { correction_reason: correctionReason }),
    detected_issues: normalizeIssueList(
      value.detected_issues ?? value.detected ?? value.issues_before,
    ),
    element_changes: changes,
    resolved_issues: normalizeIssueList(value.resolved_issues),
    remaining_issues: normalizeIssueList(
      value.remaining_issues ?? value.issues,
    ),
    path,
    ...(ocr ? { ocr } : {}),
  };
}

function normalizeStageTimings(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([stage, duration]) => {
      const normalized = normalizeNumber(duration);
      return normalized === undefined ? [] : [[stage, normalized]];
    }),
  );
}

function normalizePercentiles(
  value: unknown,
): AuthoredQualityReviewPercentiles | undefined {
  if (!isRecord(value)) return undefined;
  const result: AuthoredQualityReviewPercentiles = {};
  const aliases = {
    p50_ms: ["p50", "p50Ms"],
    p95_ms: ["p95", "p95Ms"],
    p99_ms: ["p99", "p99Ms"],
    samples: ["count", "sample_count"],
  } as const;
  for (const [key, candidates] of Object.entries(aliases) as Array<
    [keyof AuthoredQualityReviewPercentiles, readonly string[]]
  >) {
    const normalized = normalizeNumber(
      [key, ...candidates]
        .map((candidate) => value[candidate])
        .find((candidate) => candidate !== undefined),
    );
    if (normalized !== undefined) result[key] = normalized;
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

function normalizeLatencyPercentiles(
  value: unknown,
): AuthoredQualityReviewMetrics["latency_percentiles"] | undefined {
  if (!isRecord(value)) return undefined;
  const normalizeRun = (run: unknown) => {
    if (!isRecord(run)) return undefined;
    const stages = Object.fromEntries(
      Object.entries(run).flatMap(([stage, percentiles]) => {
        const normalized = normalizePercentiles(percentiles);
        return normalized ? [[stage, normalized]] : [];
      }),
    );
    return Object.keys(stages).length > 0 ? stages : undefined;
  };
  const cold = normalizeRun(value.cold);
  const warm = normalizeRun(value.warm);
  return cold || warm ? { ...(cold ? { cold } : {}), ...(warm ? { warm } : {}) } : undefined;
}

function normalizeStageUsage(
  value: unknown,
): Record<string, AuthoredQualityReviewStageUsage> | undefined {
  if (!isRecord(value)) return undefined;
  const metricAliases = {
    input_tokens: ["prompt_tokens"],
    output_tokens: ["completion_tokens"],
    total_tokens: ["tokens"],
    cost_usd: ["usd", "cost"],
    cache_hits: [],
    cache_misses: [],
  } as const;
  const stages = Object.fromEntries(
    Object.entries(value).flatMap(([stage, rawUsage]) => {
      if (!isRecord(rawUsage)) return [];
      const usage: AuthoredQualityReviewStageUsage = {};
      for (const [key, aliases] of Object.entries(metricAliases) as Array<
        [keyof AuthoredQualityReviewStageUsage, readonly string[]]
      >) {
        const normalized = normalizeNumber(
          [key, ...aliases]
            .map((candidate) => rawUsage[candidate])
            .find((candidate) => candidate !== undefined),
        );
        if (normalized !== undefined) usage[key] = normalized;
      }
      return Object.keys(usage).length > 0 ? [[stage, usage]] : [];
    }),
  );
  return Object.keys(stages).length > 0 ? stages : undefined;
}

function normalizeSlideOcr(
  value: unknown,
): AuthoredQualityReviewOcrCheck | undefined {
  if (!isRecord(value)) return undefined;
  const nested = firstDefined(
    isRecord(value.ocr) ? value.ocr : undefined,
    isRecord(value.ocr_check) ? value.ocr_check : undefined,
    isRecord(value.ocr_result) ? value.ocr_result : undefined,
    isRecord(value.ocr_validation) ? value.ocr_validation : undefined,
  );
  if (nested !== undefined) return normalizeOcrCheck(nested);
  const hasOcrFields = [
    "ocr_executed",
    "ocr_status",
    "routed",
    "skip_reason",
    "failure_reason",
    "baseline",
    "candidate",
    "comparison",
  ].some((key) => value[key] !== undefined);
  return hasOcrFields ? normalizeOcrCheck(value) : undefined;
}

/**
 * Parse both the original compact task payload and the hybrid-review payload.
 * Unknown additions are ignored so server rollouts remain forwards compatible.
 */
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
    total_ms: [],
    cache_hits: [],
    cache_misses: [],
    llm_calls: [],
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
      corrected_false_positive: false,
      detected_issues: [],
      element_changes: [],
      resolved_issues: [],
      remaining_issues: [],
      path: "none",
      ocr,
    });
  }
  slideResults.sort((left, right) => left.slide_index - right.slide_index);
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
    fixed_count: normalizeNumber(payload.fixed_count) ?? 0,
    fixed_slide_indices: fixedSlideIndices,
    remaining_issue_count: normalizeNumber(payload.remaining_issue_count) ?? 0,
    remaining_slide_indices: normalizeIndexList(
      payload.remaining_slide_indices,
    ),
    version_saved: payload.version_saved === true,
    slide_results: slideResults,
    metrics,
  };
}

function normalizeCategory(value: unknown): AuthoredStyleCategory {
  return typeof value === "string" &&
    AUTHORED_STYLE_CATEGORIES.includes(value as AuthoredStyleCategory)
    ? (value as AuthoredStyleCategory)
    : "general";
}

function toAuthoredStyleSummary(value: unknown): AuthoredStyleSummary | null {
  if (!isRecord(value) || !isRecord(value.preview)) return null;

  const { id, name, description, preview } = value;
  if (
    typeof id !== "string" ||
    !id.trim() ||
    typeof name !== "string" ||
    !name.trim() ||
    typeof description !== "string" ||
    typeof preview.bg !== "string" ||
    !preview.bg.trim() ||
    typeof preview.accent !== "string" ||
    !preview.accent.trim()
  ) {
    return null;
  }

  const previewShape: AuthoredStylePreview = {
    bg: preview.bg.trim(),
    accent: preview.accent.trim(),
    palette: (() => {
      const palette = normalizeStringList(preview.palette);
      return palette.length > 0
        ? palette
        : normalizeStringList([preview.bg, preview.accent]);
    })(),
    variant:
      typeof preview.variant === "string" && preview.variant.trim()
        ? preview.variant.trim()
        : "clean-light",
  };
  // Only attach `image` when the backend supplied one — emitting `image: undefined`
  // would leak an own property that breaks deep-equality against the public shape.
  if (typeof preview.image === "string" && preview.image.trim()) {
    previewShape.image = getApiUrl(preview.image.trim());
  }

  // Rebuild the public shape so internal fields such as `brief` never reach UI state.
  return {
    id: id.trim(),
    name: name.trim(),
    description: description.trim(),
    category: normalizeCategory(value.category),
    tags: normalizeStringList(value.tags),
    use_cases: normalizeStringList(value.use_cases),
    preview: previewShape,
  };
}

export function normalizeAuthoredStyles(
  payload: unknown,
): AuthoredStyleSummary[] {
  const uniqueStyles: AuthoredStyleSummary[] = [];
  const seenIds = new Set<string>();

  if (Array.isArray(payload)) {
    for (const value of payload) {
      const style = toAuthoredStyleSummary(value);
      if (!style || seenIds.has(style.id)) continue;
      seenIds.add(style.id);
      uniqueStyles.push(style);
    }
  }

  const serverDefault = uniqueStyles.find(
    (style) => style.id === DEFAULT_AUTHORED_STYLE.id,
  );
  const remainingStyles = uniqueStyles.filter(
    (style) => style.id !== DEFAULT_AUTHORED_STYLE.id,
  );

  return [serverDefault ?? DEFAULT_AUTHORED_STYLE, ...remainingStyles];
}

class AuthoredStylesApi {
  static async getStyles(): Promise<AuthoredStyleSummary[]> {
    try {
      const response = await fetch(getApiUrl("/api/v1/ppt/authored/styles"), {
        method: "GET",
        headers: getHeader(),
        cache: "no-store",
      });
      const payload: unknown = await ApiResponseHandler.handleResponse(
        response,
        "Failed to get authored styles",
      );
      return normalizeAuthoredStyles(payload);
    } catch (error) {
      console.error("Error getting authored styles:", error);
      throw error;
    }
  }
}

export default AuthoredStylesApi;
