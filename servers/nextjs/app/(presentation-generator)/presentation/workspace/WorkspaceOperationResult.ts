import { WorkspaceApiError } from "../../services/api/workspace";
import type { WorkspaceOperationAnchor } from "./types";
import {
  OPERATION_ACTIVITY_LABELS,
  OPERATION_TARGET_LABELS,
  RESULT_LABELS,
  STATUS_LABELS,
} from "./WorkspaceOperationLabels";

export function resultLabel(key: string): string | undefined {
  return RESULT_LABELS[key];
}

export function displayResultValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "예" : "아니요";
  return String(value);
}

export function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function numberValue(...values: unknown[]): number | undefined {
  return values.find(
    (value): value is number =>
      typeof value === "number" && Number.isFinite(value),
  );
}

export function stringValue(...values: unknown[]): string | undefined {
  return values.find(
    (value): value is string =>
      typeof value === "string" && value.trim().length > 0,
  );
}

export function stringList(...values: unknown[]): string[] {
  const value = values.find(Array.isArray);
  return value
    ? value.filter(
        (item): item is string => typeof item === "string" && item.length > 0,
      )
    : [];
}

export function booleanValue(...values: unknown[]): boolean | undefined {
  return values.find((value): value is boolean => typeof value === "boolean");
}

export function slideIndexes(
  zeroBasedValues: unknown,
  oneBasedValues: unknown,
): number[] {
  const zeroBased = Array.isArray(zeroBasedValues) ? zeroBasedValues : [];
  const oneBased = Array.isArray(oneBasedValues) ? oneBasedValues : [];
  return Array.from(
    new Set([
      ...zeroBased
        .filter(
          (value): value is number =>
            typeof value === "number" && Number.isInteger(value) && value >= 0,
        )
        .map((value) => value),
      ...oneBased
        .filter(
          (value): value is number =>
            typeof value === "number" && Number.isInteger(value) && value >= 1,
        )
        .map((value) => value - 1),
    ]),
  ).sort((left, right) => left - right);
}

export interface QualityReviewHistoryItem {
  slideIndex?: number;
  issueType?: string;
  severity?: string;
  description?: string;
  action?: string;
  applied?: boolean;
}

export function qualityReviewHistory(
  result: Record<string, unknown>,
): QualityReviewHistoryItem[] {
  const values = [
    result.page_history,
    result.pageHistory,
    result.slide_history,
    result.slideHistory,
    result.review_details,
    result.reviewDetails,
    result.issues,
    result.slide_results,
    result.slideResults,
  ].flatMap((value) => (Array.isArray(value) ? value : []));
  const items = values
    .map((value) => {
      const item = recordValue(value);
      const zeroBasedIndex = numberValue(item.slide_index, item.slideIndex);
      const oneBasedNumber = numberValue(
        item.slide_number,
        item.slideNumber,
        item.page,
        item.page_number,
        item.pageNumber,
      );
      return {
        slideIndex:
          zeroBasedIndex !== undefined
            ? zeroBasedIndex
            : oneBasedNumber !== undefined
              ? oneBasedNumber - 1
              : undefined,
        issueType: stringValue(
          item.type,
          item.category,
          item.issue_type,
          item.issueType,
        ),
        severity: stringValue(item.severity),
        description: stringValue(item.description, item.message, item.detail),
        action: stringValue(
          item.action,
          item.fix_action,
          item.fixAction,
          item.repair_action,
          item.repairAction,
        ),
        applied: booleanValue(item.applied, item.fixed, item.resolved),
      };
    })
    .filter(
      (item) =>
        item.slideIndex !== undefined ||
        item.issueType !== undefined ||
        item.description !== undefined,
    );
  const merged: QualityReviewHistoryItem[] = [];
  for (const item of items) {
    const isSlideOutcome =
      item.issueType === undefined && item.description === undefined;
    const compatible = merged.find(
      (existing) =>
        existing.slideIndex === item.slideIndex &&
        (isSlideOutcome ||
          (existing.issueType === undefined &&
            existing.description === undefined)),
    );
    if (!compatible) {
      merged.push(item);
      continue;
    }
    compatible.issueType ??= item.issueType;
    compatible.severity ??= item.severity;
    compatible.description ??= item.description;
    compatible.action ??= item.action;
    compatible.applied ??= item.applied;
  }
  return merged;
}

export function formatTimestamp(value: string | undefined): string | undefined {
  if (!value) return undefined;
  // Backend timestamps are UTC but older rows were serialized without an
  // offset. Parse those values as UTC instead of shifting them as local time.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value.trim())
    ? value
    : `${value}Z`;
  const timestamp = new Date(normalized);
  if (!Number.isFinite(timestamp.getTime())) return undefined;
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(timestamp);
}

export function actorSummary(operation: WorkspaceOperationAnchor): string | undefined {
  const actor = operation.actor;
  if (!actor) return undefined;
  const type = actor.type
    ? ({ user: "사용자", worker: "작업자", system: "시스템" }[actor.type] ??
      "실행 주체")
    : undefined;
  const label = actor.label && actor.label !== "You" ? actor.label : type;
  return [label, actor.id].filter(Boolean).join(" · ") || undefined;
}

export function targetSummary(
  value: Record<string, unknown> | undefined,
  operationType: string,
): string | undefined {
  const target = value ?? {};
  const label =
    OPERATION_TARGET_LABELS[operationType] ??
    stringValue(
      target.label,
      target.name,
      target.displayLabel,
      target.display_label,
    );
  const presentationId = stringValue(
    target.presentationId,
    target.presentation_id,
  );
  const slideIds = stringList(target.slideIds, target.slide_ids);
  const elementIds = stringList(target.elementIds, target.element_ids);
  const displayIndices = (
    Array.isArray(target.displaySlideIndices)
      ? target.displaySlideIndices
      : Array.isArray(target.display_slide_indices)
        ? target.display_slide_indices
        : []
  )
    .filter(
      (item): item is number =>
        typeof item === "number" && Number.isFinite(item),
    )
    .slice(0, 3);
  const parts = [
    label,
    presentationId ? `자료 ID ${presentationId}` : undefined,
    displayIndices.length > 0
      ? `슬라이드 ${displayIndices.join(", ")}`
      : undefined,
    slideIds.length > 0
      ? `슬라이드 ID ${slideIds.slice(0, 3).join(", ")}`
      : undefined,
    elementIds.length > 0
      ? `요소 ID ${elementIds.slice(0, 3).join(", ")}`
      : undefined,
  ].filter((part): part is string => Boolean(part));
  return (
    parts.join(" · ") ||
    (Object.keys(target).length > 0 ? "지정된 대상" : undefined)
  );
}

export function activitySummary(
  operation: WorkspaceOperationAnchor,
): string | undefined {
  const explicitSummary = stringValue(operation.activitySummary);
  if (explicitSummary) return explicitSummary;
  const activity = OPERATION_ACTIVITY_LABELS[operation.operationType];
  const status = STATUS_LABELS[operation.status];
  return activity && status ? `${activity} · ${status}` : undefined;
}

export function safeRequestError(error: unknown, fallback: string): string {
  return error instanceof WorkspaceApiError && error.message.trim().length > 0
    ? error.message
    : fallback;
}

export function artifactFileName(
  result: Record<string, unknown>,
  format: string | undefined,
): string {
  const candidate = stringValue(
    result.file_name,
    result.filename,
    result.artifact_file_name,
    result.artifactFileName,
  );
  if (candidate) return candidate.replace(/[\\/\u0000-\u001f\u007f]/g, "_");
  return `presentation.${format ?? "bin"}`;
}

export function artifactExpired(result: Record<string, unknown>): boolean {
  const status = stringValue(
    result.artifact_status,
    result.artifactStatus,
    result.status,
  );
  if (status === "expired") return true;
  const expiresAt = stringValue(result.expires_at, result.expiresAt);
  return (
    expiresAt !== undefined &&
    Number.isFinite(Date.parse(expiresAt)) &&
    Date.parse(expiresAt) <= Date.now()
  );
}

export function triggerBrowserDownload(blob: Blob, fileName: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = fileName;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

export function issueLabel(value: unknown): string | undefined {
  const issue = recordValue(value);
  const message = stringValue(
    issue.message,
    issue.title,
    issue.code,
    issue.reason,
  );
  if (!message) return undefined;
  const slide = numberValue(
    issue.slide_number,
    issue.slideNumber,
    issue.slide_index,
    issue.slideIndex,
  );
  return slide === undefined
    ? message
    : `${slide + (issue.slide_index !== undefined ? 1 : 0)}번 슬라이드 · ${message}`;
}

export interface QualityReviewIssueDetail {
  slideIndex?: number;
  description: string;
  reason?: string;
}

export function qualityReviewIssueDetails(
  result: Record<string, unknown>,
  field: "detected_issues" | "resolved_issues" | "remaining_issues",
): QualityReviewIssueDetail[] {
  const slideResults = Array.isArray(result.slide_results)
    ? result.slide_results
    : Array.isArray(result.slideResults)
      ? result.slideResults
      : [];
  const values: Array<{ value: unknown; slideIndex?: number }> = [
    ...slideResults.flatMap((rawSlide) => {
      const slide = recordValue(rawSlide);
      const slideIndex = numberValue(slide.slide_index, slide.slideIndex);
      const issues = Array.isArray(slide[field]) ? slide[field] : [];
      return issues.map((value) => ({ value, slideIndex }));
    }),
    ...(Array.isArray(result[field])
      ? result[field].map((value) => ({ value }))
      : []),
  ];
  const seen = new Set<string>();
  return values.flatMap(({ value, slideIndex }) => {
    const issue = recordValue(value);
    const description =
      typeof value === "string"
        ? value.trim()
        : stringValue(issue.description, issue.message, issue.title, issue.code);
    if (!description) return [];
    const issueSlideIndex = numberValue(issue.slide_index, issue.slideIndex);
    const reason = stringValue(
      issue.reason,
      issue.failure_reason,
      issue.rejection_reason,
      issue.detail,
    );
    const resolvedSlideIndex = issueSlideIndex ?? slideIndex;
    const item: QualityReviewIssueDetail = {
      ...(resolvedSlideIndex === undefined ? {} : { slideIndex: resolvedSlideIndex }),
      description,
      ...(reason && reason !== description ? { reason } : {}),
    };
    const identity = `${item.slideIndex ?? ""}\u0000${description}\u0000${item.reason ?? ""}`;
    if (seen.has(identity)) return [];
    seen.add(identity);
    return [item];
  });
}

export function qualityMetric(
  result: Record<string, unknown>,
  key: string,
  ...aliases: string[]
): number | undefined {
  const metrics = recordValue(result.metrics);
  const telemetry = recordValue(result.telemetry);
  const llm = recordValue(metrics.llm ?? metrics.llm_funnel ?? telemetry.llm);
  const llmKeyAliases: Record<string, readonly string[]> = {
    llm_eligible: ["eligible", "eligible_count", "llm_eligible_count"],
    llm_calls: ["calls", "call_count"],
    llm_responses: ["responses", "response_count"],
    llm_valid_plans: ["valid_plans", "valid_plan_count"],
    llm_schema_passes: ["schema_passes", "schema_pass_count"],
    llm_provider_failures: ["provider_failures", "provider_failure_count"],
    llm_empty: ["empty", "empty_count"],
    llm_schema_rejected: ["schema_rejected", "schema_rejects", "schema_reject_count"],
    proposal_preflight_unprojectable: ["preflight_unprojectable", "proposal_preflight_unprojectable"],
    llm_preflight_passes: ["preflight_passes", "preflight_pass_count"],
    llm_rendered: ["rendered", "render_count"],
    llm_negative_cache_hits: ["negative_cache_hits", "negative_cache_hit_count"],
    llm_circuit_open: ["circuit_open", "circuit_open_count", "llm_circuit_open_count"],
  };
  return numberValue(
    ...[key, ...aliases].flatMap((candidate) => [
      metrics[candidate],
      telemetry[candidate],
      llm[candidate],
      ...(llmKeyAliases[candidate] ?? []).map((nested) => llm[nested]),
      result[candidate],
    ]),
  );
}

export function formatDuration(milliseconds: number): string {
  return milliseconds >= 1_000
    ? `${(milliseconds / 1_000).toFixed(milliseconds >= 10_000 ? 0 : 1)}초`
    : `${Math.round(milliseconds)}ms`;
}
