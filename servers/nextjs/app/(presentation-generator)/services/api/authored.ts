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
}

export type AuthoredOcrStatus =
  | "executed"
  | "routed_skip"
  | "unavailable"
  | "timeout"
  | "unknown";

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
  /** Why an attempted repair was not retained, when supplied by the backend. */
  rejection_reason?: string | null;
  element_changes: AuthoredQualityReviewElementChange[];
  resolved_issues: string[];
  remaining_issues: string[];
  path: AuthoredReviewRepairPath;
  ocr?: AuthoredQualityReviewOcrCheck;
}

export interface AuthoredQualityReviewMetrics {
  stage_timings_ms: Record<string, number>;
  total_ms?: number;
  cache_hits?: number;
  vqa_calls?: number;
  patch_count?: number;
  fallback_count?: number;
  patch_attempted?: number;
  patch_succeeded?: number;
  patch_failed?: number;
  fallback_attempted?: number;
  fallback_succeeded?: number;
  fallback_failed?: number;
  ocr_executed?: boolean;
  ocr_calls?: number;
  ocr_cache_hits?: number;
  ocr_total_ms?: number;
}

export interface AuthoredQualityReviewResult {
  presentation_id?: string;
  mode?: "analyze_only" | "analyze_and_fix";
  scope?: "all" | "current";
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

export type AuthoredStyleCategory =
  (typeof AUTHORED_STYLE_CATEGORIES)[number];

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
  description:
    "깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일",
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
  return [...new Set(value.filter((item): item is number =>
    typeof item === "number" && Number.isInteger(item) && item >= 0
  ))].sort((left, right) => left - right);
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
  return value === "local_patch" || value === "full_fallback" || value === "none"
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
  value: unknown
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
    return normalizeNumber(firstDefined(candidate.count, candidate.total, candidate.length));
  };
  const text = count(firstDefined(source.text, source.texts, source.missing_text, source.text_omissions));
  const numbers = count(firstDefined(source.numbers, source.number, source.missing_numbers, source.number_omissions));
  const symbols = count(firstDefined(source.symbols, source.symbol, source.missing_symbols, source.symbol_omissions));
  if (text === undefined && numbers === undefined && symbols === undefined) return undefined;
  return { text: text ?? 0, numbers: numbers ?? 0, symbols: symbols ?? 0 };
}

function deriveResolvedOmissions(
  baseline?: AuthoredQualityReviewOcrOmissions,
  candidate?: AuthoredQualityReviewOcrOmissions
): AuthoredQualityReviewOcrOmissions | undefined {
  if (!baseline || !candidate) return undefined;
  return {
    text: Math.max(0, baseline.text - candidate.text),
    numbers: Math.max(0, baseline.numbers - candidate.numbers),
    symbols: Math.max(0, baseline.symbols - candidate.symbols),
  };
}

function normalizeOcrCheck(value: unknown): AuthoredQualityReviewOcrCheck | undefined {
  if (!isRecord(value)) return undefined;
  const hasMetrics = isRecord(value.metrics);
  const comparisonSource = isRecord(value.comparison) ? value.comparison : {};
  const executed = normalizeBoolean(firstDefined(value.executed, value.ocr_executed));
  const available = normalizeBoolean(value.available);
  const status = normalizeOcrStatus(firstDefined(value.status, value.ocr_status));
  const reason = normalizeOptionalString(firstDefined(
    value.failure_reason, value.skip_reason, value.reason, value.error, value.message
  ));
  // The current backend exposes compact, privacy-safe omission counts at the
  // top level while retaining nested result metadata for diagnostics. Prefer
  // those counts; the nested object intentionally contains no OCR source text.
  const baseline = normalizeOcrOmissions(firstDefined(value.baseline_omissions, value.baseline));
  const candidate = normalizeOcrOmissions(firstDefined(value.candidate_omissions, value.candidate));
  const comparison: AuthoredQualityReviewOcrComparison = {
    accepted: normalizeBoolean(firstDefined(comparisonSource.accepted, value.accepted)),
    strict_improvement: normalizeBoolean(firstDefined(comparisonSource.strict_improvement, value.strict_improvement)),
    no_regression: normalizeBoolean(firstDefined(comparisonSource.no_regression, value.no_regression)),
    reason: normalizeOptionalString(firstDefined(comparisonSource.reason, comparisonSource.failure_reason)),
    resolved: normalizeOcrOmissions(firstDefined(comparisonSource.resolved, value.resolved)),
    remaining: normalizeOcrOmissions(firstDefined(comparisonSource.remaining, value.remaining)),
    regressions: normalizeOcrOmissions(firstDefined(comparisonSource.regressions, comparisonSource.regression, value.regressions)),
  };
  if (!comparison.resolved) comparison.resolved = deriveResolvedOmissions(baseline, candidate);
  const hasComparison = Object.values(comparison).some((item) => item !== undefined && item !== null);
  const hasEvidence = executed !== undefined || available !== undefined || status !== undefined || reason !== null ||
    baseline !== undefined || candidate !== undefined || hasComparison || hasMetrics || value.routed !== undefined;
  if (!hasEvidence) return undefined;
  const inferredStatus = status ?? (executed === true ? "executed" : available === false ? "unavailable" : value.routed === false || reason !== null ? "routed_skip" : "unknown");
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
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return undefined;
}

function normalizeElementChange(
  value: unknown,
  legacyApplied: boolean
): AuthoredQualityReviewElementChange | null {
  if (!isRecord(value)) return null;
  const path = normalizeRepairPath(value.path);
  const change: AuthoredQualityReviewElementChange = {
    applied: normalizeApplied(value.applied, legacyApplied),
    resolved_issues: normalizeIssueList(value.resolved_issues),
    remaining_issues: normalizeIssueList(value.remaining_issues),
    path,
  };
  const elementId = value.element_id ?? value.elementId ?? value.qid;
  const before = normalizeDisplayValue(value.before);
  const after = normalizeDisplayValue(value.after);
  if (typeof elementId === "string" && elementId.trim()) {
    change.element_id = elementId.trim();
  }
  if (before !== undefined) change.before = before;
  if (after !== undefined) change.after = after;
  return change;
}

function normalizeSlideResult(
  value: unknown,
  fixedSlideIndices: ReadonlySet<number>,
  ocr?: AuthoredQualityReviewOcrCheck
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
  // Older payloads did not expose acceptance. Only a fixed-slide index is
  // reliable evidence of a saved change; path alone may describe a rejected
  // candidate.
  const applied = normalizeApplied(
    value.applied,
    fixedSlideIndices.has(slideIndex)
  );
  const attempted = typeof value.attempted === "boolean"
    ? value.attempted
    : path !== "none" || rawChanges.length > 0 || rejectionReason !== null;
  const changes = rawChanges.flatMap((change) => {
    const normalized = normalizeElementChange(change, applied);
    return normalized ? [normalized] : [];
  });
  return {
    slide_index: slideIndex,
    attempted,
    applied,
    ...(rejectionReason === null ? {} : { rejection_reason: rejectionReason }),
    element_changes: changes,
    resolved_issues: normalizeIssueList(value.resolved_issues),
    remaining_issues: normalizeIssueList(value.remaining_issues ?? value.issues),
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
    })
  );
}

function normalizeSlideOcr(value: unknown): AuthoredQualityReviewOcrCheck | undefined {
  if (!isRecord(value)) return undefined;
  const nested = firstDefined(
    isRecord(value.ocr) ? value.ocr : undefined,
    isRecord(value.ocr_check) ? value.ocr_check : undefined,
    isRecord(value.ocr_result) ? value.ocr_result : undefined,
    isRecord(value.ocr_validation) ? value.ocr_validation : undefined
  );
  if (nested !== undefined) return normalizeOcrCheck(nested);
  const hasOcrFields = [
    "ocr_executed", "ocr_status", "routed", "skip_reason", "failure_reason",
    "baseline", "candidate", "comparison",
  ].some((key) => value[key] !== undefined);
  return hasOcrFields ? normalizeOcrCheck(value) : undefined;
}

/**
 * Parse both the original compact task payload and the hybrid-review payload.
 * Unknown additions are ignored so server rollouts remain forwards compatible.
 */
export function normalizeAuthoredQualityReviewResult(
  value: unknown
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
  const stageTimings = normalizeStageTimings(
    metricsSource.stage_timings_ms ?? metricsSource.stage_timings ?? payload.stage_timings_ms
  );
  const metrics: AuthoredQualityReviewMetrics = { stage_timings_ms: stageTimings };
  const metricAliases = {
    total_ms: [],
    cache_hits: [],
    vqa_calls: [],
    patch_count: [],
    fallback_count: [],
    patch_attempted: ["patch_attempts"],
    patch_succeeded: ["patch_successes"],
    patch_failed: ["patch_failures"],
    fallback_attempted: ["fallback_attempts"],
    fallback_succeeded: ["fallback_successes"],
    fallback_failed: ["fallback_failures"],
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
  const ocrExecuted = normalizeBoolean(firstDefined(
    metricsSource.ocr_executed, payload.ocr_executed
  ));
  if (ocrExecuted !== undefined) metrics.ocr_executed = ocrExecuted;
  const ocrMetricAliases = {
    ocr_calls: ["ocr_call_count", "ocr_requests"],
    ocr_cache_hits: ["ocr_cache_hit_count", "ocr_cached"],
    ocr_total_ms: ["ocr_ms", "ocr_duration_ms"],
  } as const;
  for (const [key, aliases] of Object.entries(ocrMetricAliases) as Array<
    [keyof typeof ocrMetricAliases, readonly string[]]
  >) {
    const raw = [key, ...aliases]
      .map((candidate) => metricsSource[candidate] ?? payload[candidate])
      .find((candidate) => candidate !== undefined) ?? (key === "ocr_total_ms" ? stageTimings.ocr : undefined);
    const normalized = normalizeNumber(raw);
    if (normalized !== undefined) metrics[key] = normalized;
  }
  const fixedSlideIndices = normalizeIndexList(payload.fixed_slide_indices);
  const fixedSlideIndexSet = new Set(fixedSlideIndices);
  const ocrChecks = new Map<number, AuthoredQualityReviewOcrCheck>();
  const rawOcrChecks = Array.isArray(payload.ocr_checks) ? payload.ocr_checks : [];
  for (const rawCheck of rawOcrChecks) {
    if (!isRecord(rawCheck) || typeof rawCheck.slide_index !== "number" ||
      !Number.isInteger(rawCheck.slide_index) || rawCheck.slide_index < 0) continue;
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
      applied: false,
      element_changes: [],
      resolved_issues: [],
      remaining_issues: [],
      path: "none",
      ocr,
    });
  }
  slideResults.sort((left, right) => left.slide_index - right.slide_index);
  return {
    presentation_id: typeof payload.presentation_id === "string" ? payload.presentation_id : undefined,
    mode: payload.mode === "analyze_only" || payload.mode === "analyze_and_fix" ? payload.mode : undefined,
    scope: payload.scope === "all" || payload.scope === "current" ? payload.scope : undefined,
    reviewed_count: normalizeNumber(payload.reviewed_count) ?? 0,
    issue_slide_count: normalizeNumber(payload.issue_slide_count) ?? 0,
    fixed_count: normalizeNumber(payload.fixed_count) ?? 0,
    fixed_slide_indices: fixedSlideIndices,
    remaining_issue_count: normalizeNumber(payload.remaining_issue_count) ?? 0,
    remaining_slide_indices: normalizeIndexList(payload.remaining_slide_indices),
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

export function normalizeAuthoredStyles(payload: unknown): AuthoredStyleSummary[] {
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
    (style) => style.id === DEFAULT_AUTHORED_STYLE.id
  );
  const remainingStyles = uniqueStyles.filter(
    (style) => style.id !== DEFAULT_AUTHORED_STYLE.id
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
        "Failed to get authored styles"
      );
      return normalizeAuthoredStyles(payload);
    } catch (error) {
      console.error("Error getting authored styles:", error);
      throw error;
    }
  }
}

export default AuthoredStylesApi;
