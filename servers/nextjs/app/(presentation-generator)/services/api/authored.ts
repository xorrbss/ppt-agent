import { getApiUrl } from "@/utils/api";

import { ApiResponseHandler } from "./api-error-handler";
import {
  isRecord,
  normalizeStringList,
} from "./authored-quality-normalize-base";
import { getHeader } from "./header";

export interface AuthoredStylePreview {
  bg: string;
  accent: string;
  palette: string[];
  variant: string;
  image?: string;
}

/**
 * The internal maximum-repair pipeline may choose a path, but the UI never
 * exposes that choice to users. `legacy_*` values are normalized to their
 * current semantic equivalent so historic operation results remain readable.
 */
export type AuthoredReviewRepairPath =
  | "pattern"
  | "deterministic"
  | "llm_repair_plan"
  /** Historic result only; it never authorizes automatic page regeneration. */
  | "local_patch"
  /** Historic result only; the current quality-review path never emits it. */
  | "full_fallback"
  | "none";

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

/** A single measurement fact. Unknown facts never negate a proven issue. */
export type AuthoredQualityEvidenceState =
  | "proven_clean"
  | "proven_issue"
  | "unknown"
  | "not_applicable";

export interface AuthoredQualityEvidenceAtom {
  qid?: string;
  constraint_id?: string;
  metric?: string;
  stage?: string;
  state: AuthoredQualityEvidenceState;
  reason?: string;
}

/** A scoped sensor failure shown independently from detected issues. */
export interface AuthoredQualityIncompleteMeasurement {
  qid?: string;
  constraint_id?: string;
  metric?: string;
  stage?: string;
  reason: string;
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
  /** Scoped candidate-generation blockers with element/constraint/sensor detail. */
  repair_blockers: string[];
  /** Human review corrected a detector hit; source content was intentionally unchanged. */
  corrected_false_positive: boolean;
  correction_reason?: string | null;
  /** Issues reported by the detector before proposal generation. */
  detected_issues: string[];
  /** Per-element/constraint evidence; compatibility telemetry fields are derived server-side. */
  evidence_atoms: AuthoredQualityEvidenceAtom[];
  /** Scoped measurement gaps. These do not erase proven issue evidence. */
  incomplete_measurements: AuthoredQualityIncompleteMeasurement[];
  /** Unknown evidence in the mutation target's dependency cone that blocked publication. */
  publish_blockers: AuthoredQualityIncompleteMeasurement[];
  confirmed_issue_count: number;
  unresolved_confirmed_issue_count: number;
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
  /** End-to-end elapsed time. This is not the sum of parallel stage timings. */
  wall_time_ms?: number;
  /** Sum of stage timings for capacity analysis; parallel work may make it exceed wall time. */
  aggregate_stage_time_ms?: number;
  /** Legacy end-to-end elapsed time alias retained for historic operation results. */
  total_ms?: number;
  cache_hits?: number;
  cache_misses?: number;
  /** Repair groups eligible to enter the bounded LLM proposal path. */
  llm_eligible?: number;
  llm_calls?: number;
  /** Responses received from the provider, before schema validation. */
  llm_responses?: number;
  /** Closed, schema-valid RepairPlans that passed proposal validation. */
  llm_valid_plans?: number;
  /** Provider responses that passed the closed RepairPlan schema. */
  llm_schema_passes?: number;
  /** Provider/network/model failures before a usable response was received. */
  llm_provider_failures?: number;
  /** Empty but otherwise successful provider responses. */
  llm_empty?: number;
  /** Responses rejected by the closed RepairPlan schema. */
  llm_schema_rejected?: number;
  /** Plans rejected before rendering because source projection is unavailable. */
  proposal_preflight_unprojectable?: number;
  /** Plans that passed proposal preflight before render admission. */
  llm_preflight_passes?: number;
  /** LLM-originated candidates that reached a render attempt. */
  llm_rendered?: number;
  /** Bounded provider/model/schema negative-cache hits. */
  llm_negative_cache_hits?: number;
  /** LLM proposal attempts suppressed because the provider circuit was open. */
  llm_circuit_open?: number;
  /** Distinct bounded reason codes for why the LLM produced no candidate. */
  llm_no_candidate_reasons?: string[];
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
  pattern_cluster_count?: number;
  pattern_representative_count?: number;
  pattern_recipe_count?: number;
  pattern_reuse_attempts?: number;
  pattern_reuse_hits?: number;
  pattern_reuse_applied?: number;
  composite_plan_count?: number;
  composite_operations_merged?: number;
  render_candidates_rejected_early?: number;
  full_render_count?: number;
  llm_pattern_call_count?: number;
  individual_fallback_count?: number;
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
  detailed_issue_count: number;
  fixed_count: number;
  resolved_issue_count: number;
  fixed_slide_indices: number[];
  remaining_slide_count: number;
  remaining_issue_count: number;
  remaining_slide_indices: number[];
  pattern_cluster_count?: number;
  pattern_representative_count?: number;
  pattern_recipe_count?: number;
  pattern_reuse_attempts?: number;
  pattern_reuse_hits?: number;
  pattern_reuse_applied?: number;
  version_saved: boolean;
  confirmed_issue_count: number;
  incomplete_measurement_count: number;
  unresolved_confirmed_issue_count: number;
  clean_slide_count: number;
  /** Detailed detector issues that could not be safely associated with a slide. */
  unattributed_issues: string[];
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

export { normalizeAuthoredQualityReviewResult } from "./authored-quality-normalization";

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
