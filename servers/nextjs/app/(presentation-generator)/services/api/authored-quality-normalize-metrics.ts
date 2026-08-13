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

export function normalizeStageTimings(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([stage, duration]) => {
      const normalized = normalizeNumber(duration);
      return normalized === undefined ? [] : [[stage, normalized]];
    }),
  );
}

export function normalizePercentiles(
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

export function normalizeLatencyPercentiles(
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

export function normalizeStageUsage(
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

export function normalizeSlideOcr(
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
