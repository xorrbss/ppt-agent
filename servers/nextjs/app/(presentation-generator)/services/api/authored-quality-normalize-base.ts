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

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function normalizeStringList(value: unknown): string[] {
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

export function normalizeNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : undefined;
}

export function normalizeIndexList(value: unknown): number[] {
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

export function normalizeIssueList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string") return item.trim() ? [item.trim()] : [];
    if (!isRecord(item)) return [];
    const detail = item.detail ?? item.message ?? item.code;
    return typeof detail === "string" && detail.trim() ? [detail.trim()] : [];
  });
}

export function normalizeRepairPath(value: unknown): AuthoredReviewRepairPath {
  switch (value) {
    case "pattern":
    case "pattern_reuse":
      return "pattern";
    case "deterministic":
    case "individual":
      return "deterministic";
    case "llm_repair_plan":
    case "local_patch":
    case "full_fallback":
      return value;
    case "llm":
      return "llm_repair_plan";
    default:
      return "none";
  }
}

export function normalizeUnattributedIssues(
  payload: Record<string, unknown>,
  slideResults: readonly AuthoredQualityReviewSlideResult[],
): string[] {
  const attributed = new Set(
    slideResults.flatMap((slide) => [
      ...slide.detected_issues,
      ...slide.resolved_issues,
      ...slide.remaining_issues,
    ]),
  );
  const candidates = [
    payload.unattributed_issues,
    payload.unscoped_issues,
    payload.issues_without_slide_index,
    payload.issues,
    payload.remaining_issues,
    payload.detected_issues,
  ];
  const seen = new Set<string>();
  return candidates.flatMap((candidate) => {
    if (!Array.isArray(candidate)) return [];
    return candidate.flatMap((rawIssue) => {
      const issue = isRecord(rawIssue) ? rawIssue : {};
      const index = firstDefined(issue.slide_index, issue.slideIndex);
      if (
        typeof index === "number" &&
        Number.isInteger(index) &&
        index >= 0
      ) {
        return [];
      }
      const detail = normalizeIssueList([rawIssue])[0];
      if (!detail || attributed.has(detail) || seen.has(detail)) return [];
      seen.add(detail);
      return [detail];
    });
  });
}

export function normalizeOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function normalizeApplied(value: unknown, legacyValue: boolean): boolean {
  return typeof value === "boolean" ? value : legacyValue;
}

export function normalizeBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

export function firstDefined(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined);
}

export function normalizeOcrStatus(value: unknown): AuthoredOcrStatus | undefined {
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

export function normalizeOcrOmissions(
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

export function deriveResolvedOmissions(
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

export function normalizeOcrCheck(
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
