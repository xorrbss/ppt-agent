import { useEffect, useState } from "react";
import {
  downloadWorkspaceArtifact,
  WorkspaceApiError,
} from "../../services/api/workspace";
import {
  KNOWN_OPERATION_STATUSES,
  isKnownWorkspaceOperationType,
  type WorkspaceOperationActionId,
  type WorkspaceOperationAnchor,
  type WorkspacePresentationRefreshState,
} from "./types";

const STATUS_LABELS: Record<string, string> = {
  queued: "대기 중",
  waiting_for_assets: "파일 준비 대기 중",
  blocked: "사용자 조치 필요",
  running: "진행 중",
  approval_required: "승인 필요",
  awaiting_approval: "승인 대기 중",
  cancelling: "취소 중",
  succeeded: "완료",
  failed: "실패",
  cancelled: "취소됨",
  dead_letter: "재시도 필요",
};

const TERMINAL = new Set(["succeeded", "failed", "cancelled", "dead_letter"]);
const APPROVABLE = new Set(["approval_required", "awaiting_approval"]);
interface WorkspaceOperationCardProps {
  operation: WorkspaceOperationAnchor;
  currentRevision?: number;
  disabled?: boolean;
  onAction: (
    operationId: string,
    action: "retry" | "cancel" | "approve" | "reject" | "undo",
  ) => void | Promise<void>;
  onReExport?: (
    operationType: "export.pptx" | "export.pdf",
  ) => void | Promise<void>;
  onFocusSlide?: (slideIndex: number) => void;
  onRemakePage?: (slideIndex: number) => void;
  presentationRefreshState?: WorkspacePresentationRefreshState;
}

const RESULT_LABELS: Record<string, string> = {
  summary: "요약",
  message: "메시지",
  revision: "리비전",
  slide_count: "슬라이드 수",
  slideCount: "슬라이드 수",
  deleted: "삭제 완료",
  cleanup_status: "정리 상태",
  cleanupStatus: "정리 상태",
  restored_version_id: "복원 버전",
  restoredVersionId: "복원 버전",
};

const OPERATION_TARGET_LABELS: Record<string, string> = {
  "presentation.generate": "프레젠테이션",
  "presentation.edit": "프레젠테이션",
  "presentation.delete": "프레젠테이션",
  "slide.edit": "선택한 슬라이드",
  "slide.regenerate": "선택한 슬라이드",
  "slide.delete": "선택한 슬라이드",
  "version.restore": "저장된 버전",
  "template.apply": "AI 저작 템플릿",
  "quality.review": "프레젠테이션 품질",
  "export.pptx": "PowerPoint 파일",
  "export.pdf": "PDF 파일",
};

const OPERATION_ACTIVITY_LABELS: Record<string, string> = {
  "presentation.generate": "프레젠테이션 생성",
  "presentation.edit": "프레젠테이션 편집",
  "presentation.delete": "프레젠테이션 삭제",
  "slide.edit": "슬라이드 편집",
  "slide.regenerate": "페이지 다시 만들기",
  "slide.delete": "슬라이드 삭제",
  "version.restore": "버전 복원",
  "template.apply": "AI 저작 템플릿 생성",
  "quality.review": "품질 검토",
  "export.pptx": "PowerPoint 내보내기",
  "export.pdf": "PDF 내보내기",
};

function resultLabel(key: string): string | undefined {
  return RESULT_LABELS[key];
}

function displayResultValue(value: string | number | boolean): string {
  if (typeof value === "boolean") return value ? "예" : "아니요";
  return String(value);
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function numberValue(...values: unknown[]): number | undefined {
  return values.find(
    (value): value is number =>
      typeof value === "number" && Number.isFinite(value),
  );
}

function stringValue(...values: unknown[]): string | undefined {
  return values.find(
    (value): value is string =>
      typeof value === "string" && value.trim().length > 0,
  );
}

function stringList(...values: unknown[]): string[] {
  const value = values.find(Array.isArray);
  return value
    ? value.filter(
        (item): item is string => typeof item === "string" && item.length > 0,
      )
    : [];
}

function booleanValue(...values: unknown[]): boolean | undefined {
  return values.find((value): value is boolean => typeof value === "boolean");
}

function slideIndexes(
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

interface QualityReviewHistoryItem {
  slideIndex?: number;
  issueType?: string;
  severity?: string;
  description?: string;
  action?: string;
  applied?: boolean;
}

function qualityReviewHistory(
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

function formatTimestamp(value: string | undefined): string | undefined {
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

function actorSummary(operation: WorkspaceOperationAnchor): string | undefined {
  const actor = operation.actor;
  if (!actor) return undefined;
  const type = actor.type
    ? ({ user: "사용자", worker: "작업자", system: "시스템" }[actor.type] ??
      "실행 주체")
    : undefined;
  const label = actor.label && actor.label !== "You" ? actor.label : type;
  return [label, actor.id].filter(Boolean).join(" · ") || undefined;
}

function targetSummary(
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

function activitySummary(
  operation: WorkspaceOperationAnchor,
): string | undefined {
  const explicitSummary = stringValue(operation.activitySummary);
  if (explicitSummary) return explicitSummary;
  const activity = OPERATION_ACTIVITY_LABELS[operation.operationType];
  const status = STATUS_LABELS[operation.status];
  return activity && status ? `${activity} · ${status}` : undefined;
}

function safeRequestError(error: unknown, fallback: string): string {
  return error instanceof WorkspaceApiError && error.message.trim().length > 0
    ? error.message
    : fallback;
}

function artifactFileName(
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

function artifactExpired(result: Record<string, unknown>): boolean {
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

function triggerBrowserDownload(blob: Blob, fileName: string): void {
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

function issueLabel(value: unknown): string | undefined {
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

interface QualityReviewIssueDetail {
  slideIndex?: number;
  description: string;
  reason?: string;
}

function qualityReviewIssueDetails(
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

function qualityMetric(
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

function formatDuration(milliseconds: number): string {
  return milliseconds >= 1_000
    ? `${(milliseconds / 1_000).toFixed(milliseconds >= 10_000 ? 0 : 1)}초`
    : `${Math.round(milliseconds)}ms`;
}

function useApprovalExpired(
  expiresAt: string | undefined,
  approvable: boolean,
): boolean {
  const expiresAtMs = expiresAt ? Date.parse(expiresAt) : Number.NaN;
  const [expired, setExpired] = useState(
    approvable && Number.isFinite(expiresAtMs) && expiresAtMs <= Date.now(),
  );

  useEffect(() => {
    if (!approvable || !Number.isFinite(expiresAtMs)) {
      setExpired(false);
      return;
    }
    let timeout: number | undefined;
    const scheduleExpiry = () => {
      const remaining = expiresAtMs - Date.now();
      if (remaining <= 0) {
        setExpired(true);
        return;
      }
      setExpired(false);
      // Browsers clamp larger delays; reschedule so a far-future approval is
      // never marked expired immediately because of timer overflow.
      timeout = window.setTimeout(
        scheduleExpiry,
        Math.min(remaining, 2_147_483_647),
      );
    };
    scheduleExpiry();
    return () => {
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [approvable, expiresAtMs]);

  return expired;
}

export function WorkspaceOperationCard({
  operation,
  currentRevision,
  disabled = false,
  onAction,
  onReExport,
  onFocusSlide,
  onRemakePage,
  presentationRefreshState,
}: WorkspaceOperationCardProps) {
  const operationKnown = isKnownWorkspaceOperationType(operation.operationType);
  const statusKnown = (KNOWN_OPERATION_STATUSES as readonly string[]).includes(
    operation.status,
  );
  const known = operationKnown && statusKnown;
  const approvable = operationKnown && APPROVABLE.has(operation.status);
  const approvalExpired = useApprovalExpired(
    operation.approvalExpiresAt,
    approvable,
  );
  const actions = operationKnown ? (operation.allowedActions ?? []) : [];
  const allowedAction = (id: WorkspaceOperationActionId) =>
    actions.find((action) => action.id === id);
  const retryAction = allowedAction("retry");
  const retryUnavailable =
    operation.retryable === false || retryAction?.enabled === false;
  const progress =
    operation.progress === undefined
      ? undefined
      : Math.max(0, Math.min(100, operation.progress));
  const latestMessage = [...operation.events]
    .reverse()
    .find((event) => event.message)?.message;
  const latestProgressEvent = [...operation.events]
    .reverse()
    .find((event) => event.type === "operation.progress");
  const latestProgressAt = formatTimestamp(
    latestProgressEvent?.createdAt ?? operation.updatedAt,
  );
  const isPresentationGeneration =
    operation.operationType === "presentation.generate";
  const actor = actorSummary(operation);
  const createdAt = formatTimestamp(operation.createdAt);
  const updatedAt = formatTimestamp(operation.updatedAt);
  const target = targetSummary(operation.target, operation.operationType);
  const activity = activitySummary(operation);
  const revision =
    operation.expectedRevision !== undefined ||
    operation.resultRevision !== undefined
      ? `${operation.expectedRevision ?? "-"} → ${operation.resultRevision ?? "-"}`
      : undefined;
  const operationMetadata = [
    actor ? ["실행 주체", actor] : undefined,
    createdAt ? ["시작", createdAt] : undefined,
    updatedAt ? ["업데이트", updatedAt] : undefined,
    target ? ["대상", target] : undefined,
    revision ? ["리비전", revision] : undefined,
    activity ? ["활동", activity] : undefined,
  ].filter((item): item is [string, string] => item !== undefined);
  const result = recordValue(operation.result);
  const isQualityReview =
    operation.operationType === "quality.review" ||
    result.kind === "quality.review";
  const isExport =
    operation.operationType === "export.pptx" ||
    operation.operationType === "export.pdf";
  const isVersionRestore =
    operation.operationType === "version.restore" ||
    result.kind === "version.restore";
  const isTemplateApply =
    operation.operationType === "template.apply" ||
    result.kind === "template.apply";
  const templateProgressMessage =
    progress === undefined
      ? "템플릿 변경 작업을 준비하고 있습니다."
      : progress < 10
        ? "기존 원고와 선택한 템플릿을 확인하고 있습니다."
        : progress < 100
          ? "새 자료를 생성하고 있습니다. 원본 자료는 그대로 유지됩니다."
          : "새 AI 저작 자료가 준비되었습니다.";
  const exportOperationType = isExport
    ? (operation.operationType as "export.pptx" | "export.pdf")
    : undefined;
  const resultSummary =
    operationKnown &&
    operation.result &&
    !isQualityReview &&
    !isExport &&
    !isVersionRestore &&
    !isTemplateApply
      ? Object.entries(operation.result)
          .flatMap(([key, value]) => {
            const label = resultLabel(key);
            return label &&
              (typeof value === "string" ||
                typeof value === "number" ||
                typeof value === "boolean")
              ? [`${label}: ${displayResultValue(value)}`]
              : [];
          })
          .slice(0, 5)
          .join(" · ")
      : "";
  // The turn answered but the page is model-authored HTML the chat tools
  // cannot rebuild, so the reply can only name the remake flow. The backend
  // names the page too, which turns that description into an offer. Read only
  // under a known operation schema: an unrecognised envelope's fields are not
  // ours to interpret, and this one starts a write.
  const remakeOfferSlideIndex = operationKnown
    ? numberValue(result.page_remake_slide_index, result.pageRemakeSlideIndex)
    : undefined;
  const inspectedRevision = numberValue(
    result.inspected_revision,
    result.inspectedRevision,
  );
  const reviewedCount = numberValue(
    result.reviewed_count,
    result.reviewedCount,
  );
  const issueSlideCount = numberValue(
    result.issue_slide_count,
    result.issueSlideCount,
  );
  const remainingIssueCount = numberValue(
    result.remaining_issue_count,
    result.remainingIssueCount,
  );
  const confirmedIssueCount = numberValue(
    result.confirmed_issue_count,
    result.confirmedIssueCount,
  );
  const resolvedIssueDetails = qualityReviewIssueDetails(result, "resolved_issues");
  const remainingIssueDetails = qualityReviewIssueDetails(result, "remaining_issues");
  const detectedIssueDetails = qualityReviewIssueDetails(result, "detected_issues");
  const detailedIssueCount =
    confirmedIssueCount ??
    Math.max(
      detectedIssueDetails.length,
      resolvedIssueDetails.length + remainingIssueDetails.length,
    );
  const resolvedIssueCount = numberValue(
    result.resolved_issue_count,
    result.resolvedIssueCount,
  ) ?? resolvedIssueDetails.length;
  const fixedCount = numberValue(result.fixed_count, result.fixedCount);
  const patternClusterCount = qualityMetric(
    result,
    "pattern_cluster_count",
    "patternClusterCount",
  );
  const patternRecipeCount = qualityMetric(
    result,
    "pattern_recipe_count",
    "patternRecipeCount",
  );
  const patternReuseApplied = qualityMetric(
    result,
    "pattern_reuse_applied",
    "patternReuseApplied",
  );
  const qualityPipelineMetrics: Array<[string, number | undefined]> = [
    ["대표 표본", qualityMetric(result, "pattern_representative_count", "pattern_representatives")],
    ["레시피", qualityMetric(result, "pattern_recipe_count")],
    ["재사용 시도", qualityMetric(result, "pattern_reuse_attempts")],
    ["재사용 적중", qualityMetric(result, "pattern_reuse_hits")],
    ["재사용 적용", qualityMetric(result, "pattern_reuse_applied")],
    ["복합 계획", qualityMetric(result, "composite_plan_count")],
    ["병합 연산", qualityMetric(result, "composite_operations_merged")],
    ["조기 렌더 거부", qualityMetric(result, "render_candidates_rejected_early", "early_render_rejections")],
    ["전체 렌더", qualityMetric(result, "full_render_count", "full_renders")],
    ["LLM 대상", qualityMetric(result, "llm_eligible", "llm_eligible_count", "eligible")],
    ["LLM 호출", qualityMetric(result, "llm_calls")],
    ["LLM 응답", qualityMetric(result, "llm_responses", "llm_response_count")],
    ["유효 RepairPlan", qualityMetric(result, "llm_valid_plans", "llm_valid_plan_count", "valid_plans")],
    ["LLM schema 통과", qualityMetric(result, "llm_schema_passes", "llm_schema_pass_count", "schema_passes")],
    ["LLM provider 실패", qualityMetric(result, "llm_provider_failures", "llm_provider_failure_count", "provider_failures")],
    ["LLM 빈 응답", qualityMetric(result, "llm_empty", "llm_empty_count", "empty_responses")],
    ["LLM schema 거부", qualityMetric(result, "llm_schema_rejected", "llm_schema_rejects", "llm_schema_reject_count", "schema_rejected")],
    ["투영 preflight 차단", qualityMetric(result, "proposal_preflight_unprojectable", "proposal_preflight_unprojectable_count", "proposal_preflight_rejected", "preflight_unprojectable")],
    ["투영 preflight 통과", qualityMetric(result, "llm_preflight_passes", "llm_preflight_pass_count", "preflight_passes")],
    ["LLM 후보 렌더", qualityMetric(result, "llm_rendered", "llm_render_count", "llm_rendered_count")],
    ["LLM negative cache", qualityMetric(result, "llm_negative_cache_hits", "negative_cache_hits", "negative_cache_hit_count")],
    ["LLM circuit open", qualityMetric(result, "llm_circuit_open", "llm_circuit_open_count", "circuit_open")],
    ["LLM RepairPlan", qualityMetric(result, "llm_pattern_call_count", "llm_pattern_calls")],
    ["개별 전환", qualityMetric(result, "individual_fallback_count", "individual_fallbacks")],
  ].filter((item): item is [string, number] => item[1] !== undefined);
  const qualityStageTimings = recordValue(
    recordValue(result.metrics).stage_timings_ms ??
      recordValue(result.metrics).stage_timings ??
      result.stage_timings_ms,
  );
  const qualityLlmNoCandidateReasons = stringList(
    result.no_candidate_reasons,
    recordValue(result.metrics).no_candidate_reasons,
    recordValue(result.metrics).llm &&
      recordValue(recordValue(result.metrics).llm).no_candidate_reasons,
  );
  const qualityWallTime = qualityMetric(result, "wall_time_ms", "wall_ms", "elapsed_ms", "total_ms");
  const reportedStageAggregateTime = qualityMetric(
    result,
    "aggregate_stage_time_ms",
    "stage_aggregate_ms",
    "aggregate_ms",
  );
  const derivedStageAggregateTime = Object.values(qualityStageTimings).reduce<number>(
    (total, duration) =>
      typeof duration === "number" && Number.isFinite(duration)
        ? total + duration
        : total,
    0,
  );
  const qualityStageAggregateTime = reportedStageAggregateTime ??
    (Object.keys(qualityStageTimings).length > 0 ? derivedStageAggregateTime : undefined);
  const remainingSlideIndexes = Array.from(
    new Set([
      ...slideIndexes(result.remaining_slide_indices, result.remaining_pages),
      ...remainingIssueDetails.flatMap((issue) =>
        issue.slideIndex === undefined ? [] : [issue.slideIndex],
      ),
    ]),
  ).sort((left, right) => left - right);
  const fixedSlideIndexes = slideIndexes(
    result.fixed_slide_indices ??
      result.fixedSlideIndices ??
      result.modified_slide_indices ??
      result.modifiedSlideIndices,
    result.fixed_pages ??
      result.fixedPages ??
      result.modified_pages ??
      result.modifiedPages ??
      result.fixed_page_numbers ??
      result.fixedPageNumbers,
  );
  const fixedSlideNumbers = fixedSlideIndexes.map((value) => value + 1);
  const qualityChangeSummary =
    fixedSlideNumbers.length > 0
      ? `수정된 슬라이드: ${fixedSlideNumbers.join(", ")}번`
      : fixedCount !== undefined && fixedCount > 0
        ? `${fixedCount}개 슬라이드를 수정했습니다.`
        : "수정된 슬라이드가 없습니다.";
  const allQualityIssues = (
    Array.isArray(result.remaining_issues)
      ? result.remaining_issues
      : Array.isArray(result.issues)
        ? result.issues
        : []
  )
    .map(issueLabel)
    .filter((issue): issue is string => Boolean(issue));
  const qualityIssues = allQualityIssues.slice(0, 8);
  const shownRemainingIssues: QualityReviewIssueDetail[] =
    remainingIssueDetails.length > 0
      ? remainingIssueDetails.slice(0, 8)
      : qualityIssues.map((description) => ({ description }));
  const qualityHistory = qualityReviewHistory(result);
  const qualityRevision = numberValue(
    result.revision,
    result.result_revision,
    result.resultRevision,
    result.saved_revision,
    result.savedRevision,
    operation.resultRevision,
  );
  const qualityRefreshMessage =
    fixedCount === 0 && fixedSlideIndexes.length === 0
      ? "수정이 없어 PowerPoint 화면 갱신이 필요하지 않습니다."
      : presentationRefreshState === "refreshed"
        ? "수정본을 PowerPoint 화면에 자동으로 반영했습니다."
        : presentationRefreshState === "failed"
          ? "수정본은 저장되었지만 PowerPoint 화면을 자동 갱신하지 못했습니다."
          : presentationRefreshState === "refreshing"
            ? "수정본 저장 완료 · PowerPoint 화면을 자동 갱신하고 있습니다."
            : "수정본 저장 완료 · PowerPoint 화면 자동 갱신을 요청했습니다.";
  const qualityResultSaving =
    isQualityReview &&
    !TERMINAL.has(operation.status) &&
    progress !== undefined &&
    progress >= 96;
  const displayedProgress =
    operation.status === "succeeded" && progress !== undefined
      ? 100
      : qualityResultSaving
        ? 99
        : progress;
  const artifactId = isExport
    ? stringValue(result.artifact_id, result.artifactId)
    : undefined;
  const artifactFormat = stringValue(
    result.format,
    result.artifact_kind,
  )?.toLowerCase();
  const sourceRevision = numberValue(
    result.source_revision,
    result.sourceRevision,
  );
  const publishedRevision = numberValue(
    result.revision,
    result.result_revision,
    result.resultRevision,
  );
  const restoredSlideCount = numberValue(result.slide_count, result.slideCount);
  const restoredVersionId = stringValue(
    result.restored_version_id,
    result.restoredVersionId,
  );
  const artifactStale =
    sourceRevision !== undefined &&
    currentRevision !== undefined &&
    currentRevision !== sourceRevision;
  const [downloadExpired, setDownloadExpired] = useState(() =>
    artifactExpired(result),
  );
  const [downloadError, setDownloadError] = useState<string>();
  const [downloading, setDownloading] = useState(false);
  const [pendingAction, setPendingAction] = useState<string>();
  const [actionError, setActionError] = useState<string>();

  useEffect(() => {
    setDownloadExpired(artifactExpired(result));
    setDownloadError(undefined);
    const expiresAt = stringValue(result.expires_at, result.expiresAt);
    const expiresAtMs = expiresAt ? Date.parse(expiresAt) : Number.NaN;
    if (!Number.isFinite(expiresAtMs)) return;
    let timeout: number | undefined;
    const scheduleExpiry = () => {
      const remaining = expiresAtMs - Date.now();
      if (remaining <= 0) {
        setDownloadExpired(true);
        return;
      }
      timeout = window.setTimeout(
        scheduleExpiry,
        Math.min(remaining, 2_147_483_647),
      );
    };
    scheduleExpiry();
    return () => {
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [operation.operationId, operation.updatedAt, result]);

  const handleArtifactDownload = async () => {
    if (!artifactId || downloading || downloadExpired) return;
    setDownloading(true);
    setDownloadError(undefined);
    try {
      const download = await downloadWorkspaceArtifact(artifactId);
      triggerBrowserDownload(
        download.blob,
        download.fileName ?? artifactFileName(result, artifactFormat),
      );
    } catch (error) {
      const expired =
        error instanceof WorkspaceApiError &&
        (error.status === 410 ||
          error.code === "workspace_export_expired" ||
          error.code === "workspace_export_missing");
      if (expired) {
        setDownloadExpired(true);
      } else {
        setDownloadError(
          safeRequestError(error, "다운로드를 시작할 수 없습니다."),
        );
      }
    } finally {
      setDownloading(false);
    }
  };

  const handleOperationAction = async (
    action: "retry" | "cancel" | "approve" | "reject" | "undo",
  ) => {
    if (pendingAction) return;
    setPendingAction(action);
    setActionError(undefined);
    try {
      await onAction(operation.operationId, action);
    } catch (error) {
      setActionError(
        safeRequestError(
          error,
          "작업을 완료할 수 없습니다. 다시 시도해 주세요.",
        ),
      );
    } finally {
      setPendingAction(undefined);
    }
  };

  const handleReExport = async (
    operationType: "export.pptx" | "export.pdf",
  ) => {
    if (!onReExport || pendingAction) return;
    setPendingAction("reexport");
    setActionError(undefined);
    try {
      await onReExport(operationType);
    } catch (error) {
      setActionError(
        safeRequestError(error, "다시 내보내기를 시작할 수 없습니다."),
      );
    } finally {
      setPendingAction(undefined);
    }
  };

  return (
    <article
      className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm"
      aria-label={`${operation.title} 작업`}
      data-operation-id={operation.operationId}
      data-operation-status={operation.status}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-slate-900">
            {operation.title}
          </h3>
          <p
            className="mt-0.5 text-xs text-slate-500"
            role="status"
            aria-live="polite"
          >
            {STATUS_LABELS[operation.status] ??
              `지원되지 않는 상태 · ${operation.status}`}
          </p>
        </div>
        {!TERMINAL.has(operation.status) &&
          !APPROVABLE.has(operation.status) && (
            <span
              aria-hidden="true"
              className="mt-1 h-2 w-2 shrink-0 animate-pulse rounded-full bg-blue-500 motion-reduce:animate-none"
            />
          )}
      </div>
      {!known && (
        <p
          className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-900"
          role="alert"
        >
          지원되지 않는 작업 상태입니다. 기록은 보존되어 있습니다.
        </p>
      )}
      {approvable && (
        <p
          className="sr-only"
          role="alert"
          aria-live="assertive"
          data-testid="operation-approval-alert"
        >
          {approvalExpired
            ? `${operation.title} 작업의 승인 기한이 만료되었습니다.`
            : `${operation.title} 작업에 승인이 필요합니다.`}
        </p>
      )}
      {operationMetadata.length > 0 && (
        <dl
          className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 rounded-lg bg-slate-50 px-2.5 py-2 text-xs"
          data-testid="operation-metadata"
        >
          {operationMetadata.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="font-medium text-slate-500">{label}</dt>
              <dd className="min-w-0 break-words text-slate-700">{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {displayedProgress !== undefined && (
        <div className="mt-3">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-slate-100"
            role="progressbar"
            aria-label="작업 진행률"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(displayedProgress)}
            aria-valuetext={
              qualityResultSaving
                ? "결과와 수정본 리비전을 저장 중"
                : `${Math.round(displayedProgress)}%`
            }
          >
            <div
              className="h-full rounded-full bg-blue-500 transition-[width] motion-reduce:transition-none"
              style={{ width: `${displayedProgress}%` }}
            />
          </div>
          <p className="mt-1 text-right text-[11px] text-slate-500">
            {qualityResultSaving
              ? "결과 저장 중"
              : `${Math.round(displayedProgress)}%`}
          </p>
          {isPresentationGeneration && latestProgressAt && (
            <p
              className="mt-1 text-xs text-slate-500"
              data-testid="generation-last-progress-at"
            >
              서버 작업 확인됨 · 마지막 진행 업데이트 {latestProgressAt}
            </p>
          )}
        </div>
      )}
      {qualityResultSaving && (
        <p
          className="mt-2 text-xs text-slate-600"
          role="status"
          aria-live="polite"
        >
          검수 결과와 수정본 리비전을 저장하고 있습니다.
        </p>
      )}
      {latestMessage && (
        <p className="mt-2 text-xs text-slate-600">{latestMessage}</p>
      )}
      {isTemplateApply && !TERMINAL.has(operation.status) && (
        <p
          className="mt-2 rounded-lg bg-violet-50 px-2.5 py-2 text-xs text-violet-800"
          role="status"
          data-testid="template-apply-progress"
        >
          {templateProgressMessage}
        </p>
      )}
      {operation.status === "succeeded" && resultSummary && (
        <p className="mt-2 rounded-lg bg-emerald-50 px-2.5 py-2 text-xs text-emerald-800">
          {resultSummary}
        </p>
      )}
      {operation.status === "succeeded" && remakeOfferSlideIndex !== undefined && (
        <section
          className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-xs text-amber-950"
          aria-label="페이지 다시 만들기 제안"
          data-testid="page-remake-offer"
        >
          <p>
            {remakeOfferSlideIndex + 1}페이지는 AI가 직접 작성한 자료라 대화로는
            고칠 수 없습니다. 페이지를 다시 만들면 지금 요청한 내용을 반영할 수
            있습니다.
          </p>
          <button
            type="button"
            onClick={() => onRemakePage?.(remakeOfferSlideIndex)}
            disabled={!onRemakePage || disabled}
            className="mt-2 rounded-md border border-amber-300 bg-white px-2.5 py-1.5 font-semibold text-amber-900 hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-600 focus-visible:ring-offset-1"
            data-testid="page-remake-offer-start"
          >
            {remakeOfferSlideIndex + 1}페이지 다시 만들기
          </button>
        </section>
      )}
      {operation.status === "succeeded" && isVersionRestore && (
        <section
          className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-950"
          aria-label="버전 복원 결과"
          data-testid="version-restore-result"
        >
          <p className="font-semibold">버전 복원이 완료되었습니다.</p>
          <p className="mt-1 text-emerald-800">
            {sourceRevision !== undefined && publishedRevision !== undefined
              ? `리비전 ${sourceRevision} → ${publishedRevision}`
              : publishedRevision !== undefined
                ? `리비전 ${publishedRevision}`
                : "새 리비전으로 게시됨"}
            {restoredSlideCount !== undefined
              ? ` · ${restoredSlideCount}장`
              : ""}
          </p>
          {restoredVersionId && (
            <p
              className="mt-1 truncate text-emerald-700"
              title={restoredVersionId}
            >
              대상 버전 {restoredVersionId}
            </p>
          )}
        </section>
      )}
      {operation.status === "succeeded" && isTemplateApply && (
        <section
          className="mt-2 rounded-lg border border-violet-100 bg-violet-50 px-2.5 py-2 text-xs text-violet-950"
          aria-label="AI 저작 템플릿 변경 결과"
          data-testid="template-apply-result"
        >
          <p className="font-semibold">
            {stringValue(result.message) ?? "새 AI 저작 자료가 준비되었습니다."}
          </p>
          <p className="mt-1 text-violet-800">
            원본 자료는 변경되지 않았습니다.
          </p>
        </section>
      )}
      {operation.status === "succeeded" && isQualityReview && (
        <section
          className="mt-2 rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-2 text-xs text-blue-950"
          aria-label="품질 검토 결과"
          data-testid="quality-review-result"
        >
          <p className="font-semibold">
            {stringValue(result.message) ??
              "고품질 검수와 자동 수정이 완료되었습니다."}
          </p>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-blue-800">
            <dt>검토 페이지</dt>
            <dd>
              {reviewedCount !== undefined ? `${reviewedCount}장` : "검토 완료"}
            </dd>
            <dt>문제 슬라이드</dt>
            <dd>
              {issueSlideCount !== undefined ? `${issueSlideCount}장` : "-"}
            </dd>
            <dt>상세 이슈</dt>
            <dd>{detailedIssueCount > 0 ? `${detailedIssueCount}개` : "0개"}</dd>
            <dt>수정 슬라이드</dt>
            <dd>{fixedCount !== undefined ? `${fixedCount}장` : "0장"}</dd>
            <dt>해결 이슈</dt>
            <dd>{resolvedIssueCount > 0 ? `${resolvedIssueCount}개` : "0개"}</dd>
            <dt>잔여 이슈</dt>
            <dd>
              {remainingIssueCount !== undefined
                ? `${remainingIssueCount}개`
                : "-"}
            </dd>
            {patternClusterCount !== undefined && (
              <>
                <dt>수정 패턴</dt>
                <dd>
                  {patternClusterCount}개 분류 · {patternRecipeCount ?? 0}개 학습
                  {patternReuseApplied !== undefined
                    ? ` · ${patternReuseApplied}장 재사용`
                    : ""}
                </dd>
              </>
            )}
            <dt>검토 리비전</dt>
            <dd>{inspectedRevision !== undefined ? inspectedRevision : "-"}</dd>
            <dt>저장 리비전</dt>
            <dd>{qualityRevision !== undefined ? qualityRevision : "-"}</dd>
            <dt>PowerPoint 갱신</dt>
            <dd>{qualityRefreshMessage}</dd>
          </dl>
          {(qualityWallTime !== undefined || qualityStageAggregateTime !== undefined || qualityPipelineMetrics.length > 0 || Object.keys(qualityStageTimings).length > 0) && (
            <section
              className="mt-3 border-t border-blue-100 pt-2"
              aria-label="최대 수정 파이프라인 지표"
              data-testid="quality-review-pipeline-metrics"
            >
              <h4 className="font-semibold">최대 수정 처리 내역</h4>
              {(qualityWallTime !== undefined || qualityStageAggregateTime !== undefined) && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-blue-900">
                  {qualityWallTime !== undefined && (
                    <span data-testid="quality-review-wall-time">실제 경과 {formatDuration(qualityWallTime)}</span>
                  )}
                  {qualityStageAggregateTime !== undefined && (
                    <span data-testid="quality-review-stage-aggregate-time">
                      단계 누적 {formatDuration(qualityStageAggregateTime)} (병렬 합산)
                    </span>
                  )}
                </div>
              )}
              {qualityPipelineMetrics.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-blue-900">
                  {qualityPipelineMetrics.map(([label, value]) => (
                    <span key={label}>{label} {value}</span>
                  ))}
                </div>
              )}
              {qualityLlmNoCandidateReasons.length > 0 && (
                <p className="mt-1 text-[11px] text-amber-800" data-testid="quality-review-llm-no-candidate-reasons">
                  LLM 후보 미생성 사유: {qualityLlmNoCandidateReasons.join(", ")}
                </p>
              )}
              {Object.keys(qualityStageTimings).length > 0 && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-blue-800">
                  {Object.entries(qualityStageTimings).flatMap(([stage, duration]) =>
                    typeof duration === "number" && Number.isFinite(duration)
                      ? [<span key={stage}>{stage} {formatDuration(duration)}</span>]
                      : [],
                  )}
                </div>
              )}
            </section>
          )}
          <p className="mt-1 font-medium text-blue-900">
            {qualityChangeSummary}
          </p>
          {fixedSlideIndexes.length > 0 && (
            <div
              className="mt-2 flex flex-wrap gap-1.5"
              aria-label="수정된 페이지로 이동"
            >
              {fixedSlideIndexes.map((slideIndex) => (
                <button
                  key={slideIndex}
                  type="button"
                  onClick={() => onFocusSlide?.(slideIndex)}
                  disabled={!onFocusSlide}
                  className="rounded-md border border-blue-200 bg-white px-2 py-1 font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                  aria-label={`${slideIndex + 1}번 슬라이드로 이동`}
                  data-testid={`quality-review-focus-slide-${slideIndex + 1}`}
                >
                  {slideIndex + 1}번으로 이동
                </button>
              ))}
            </div>
          )}
          {(remainingIssueDetails.length > 0 || qualityIssues.length > 0) && (
            <section
              className="mt-3 border-t border-blue-100 pt-2"
              aria-label="잔여 이슈"
              data-testid="quality-review-remaining-issues"
            >
              <h4 className="font-semibold">잔여 이슈</h4>
              <ol className="mt-1.5 space-y-1.5">
                {shownRemainingIssues.map((issue, index) => (
                  <li
                    key={`${issue.slideIndex ?? "unknown"}-${issue.description}-${index}`}
                    className="rounded-md bg-white/70 px-2 py-1.5 text-[11px] text-blue-900"
                  >
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span>
                        {issue.slideIndex === undefined
                          ? `슬라이드 미지정 · ${issue.description}`
                          : `${issue.slideIndex + 1}번 슬라이드 · ${issue.description}`}
                      </span>
                      {issue.slideIndex !== undefined && (
                        <button
                          type="button"
                          onClick={() => onFocusSlide?.(issue.slideIndex!)}
                          disabled={!onFocusSlide}
                          className="rounded border border-blue-200 bg-white px-1.5 py-0.5 font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                          data-testid={`quality-review-focus-remaining-slide-${issue.slideIndex + 1}`}
                        >
                          이동
                        </button>
                      )}
                    </div>
                    {issue.reason && (
                      <p className="mt-0.5 text-blue-700">사유: {issue.reason}</p>
                    )}
                  </li>
                ))}
              </ol>
              {remainingIssueDetails.length === 0 && allQualityIssues.length > qualityIssues.length && (
                <p className="mt-1 text-blue-700">
                  외 {allQualityIssues.length - qualityIssues.length}개 이슈가 더 있습니다.
                </p>
              )}
              {remainingSlideIndexes.length > 0 && remainingIssueDetails.length === 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5" aria-label="잔여 이슈 슬라이드로 이동">
                  {remainingSlideIndexes.map((slideIndex) => (
                    <button
                      key={slideIndex}
                      type="button"
                      onClick={() => onFocusSlide?.(slideIndex)}
                      disabled={!onFocusSlide}
                      className="rounded-md border border-blue-200 bg-white px-2 py-1 font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                    >
                      {slideIndex + 1}번으로 이동
                    </button>
                  ))}
                </div>
              )}
            </section>
          )}
          {qualityHistory.length > 0 && (
            <section
              className="mt-3 border-t border-blue-100 pt-2"
              aria-label="페이지별 검수 이력"
            >
              <h4 className="font-semibold">페이지별 검수 이력</h4>
              <ol className="mt-1.5 space-y-2">
                {qualityHistory.map((item, index) => (
                  <li
                    key={`${item.slideIndex ?? "unknown"}-${item.issueType ?? index}-${index}`}
                    className="rounded-md bg-white/70 px-2 py-1.5"
                  >
                    <p className="font-medium">
                      {item.slideIndex === undefined
                        ? "페이지 정보 없음"
                        : `${item.slideIndex + 1}번 슬라이드`}
                    </p>
                    <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[11px] text-blue-900">
                      {item.issueType && (
                        <>
                          <dt>이슈 유형</dt>
                          <dd>{item.issueType}</dd>
                        </>
                      )}
                      {item.severity && (
                        <>
                          <dt>심각도</dt>
                          <dd>{item.severity}</dd>
                        </>
                      )}
                      {item.description && (
                        <>
                          <dt>설명</dt>
                          <dd>{item.description}</dd>
                        </>
                      )}
                      {item.action && (
                        <>
                          <dt>수정 조치</dt>
                          <dd>{item.action}</dd>
                        </>
                      )}
                      {item.applied !== undefined && (
                        <>
                          <dt>적용</dt>
                          <dd>{item.applied ? "적용됨" : "미적용"}</dd>
                        </>
                      )}
                    </dl>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </section>
      )}
      {operation.status === "succeeded" && isExport && (
        <section
          className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-950"
          aria-label="내보내기 결과"
          data-testid="export-artifact-result"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate font-semibold">
                {artifactFileName(result, artifactFormat)}
              </p>
              <p className="mt-0.5 text-emerald-800">
                {artifactFormat?.toUpperCase() ?? "파일"}
                {sourceRevision !== undefined
                  ? ` · 소스 리비전 ${sourceRevision}`
                  : ""}
              </p>
            </div>
            {allowedAction("download") && (
              <button
                type="button"
                data-testid="artifact-download"
                disabled={
                  downloading ||
                  downloadExpired ||
                  !artifactId ||
                  allowedAction("download")?.enabled !== true
                }
                onClick={() => void handleArtifactDownload()}
                className="shrink-0 rounded-lg bg-emerald-700 px-3 py-1.5 font-semibold text-white hover:bg-emerald-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                aria-label={`${artifactFormat?.toUpperCase() ?? "내보낸"} 파일 다운로드`}
              >
                다운로드
              </button>
            )}
          </div>
          {!artifactId && (
            <p
              className="mt-2 rounded-md bg-red-100 px-2 py-1.5 text-red-800"
              role="alert"
            >
              다운로드 정보를 확인할 수 없습니다. 다시 내보내 주세요.
            </p>
          )}
          {downloadError && (
            <p
              className="mt-2 rounded-md bg-red-100 px-2 py-1.5 text-red-800"
              role="alert"
            >
              {downloadError}
            </p>
          )}
          {downloadExpired && (
            <div
              className="mt-2 rounded-md bg-amber-100 px-2 py-2 text-amber-950"
              data-testid="artifact-expired"
            >
              <p role="status">내보내기 파일이 만료되었습니다.</p>
              {exportOperationType &&
                onReExport &&
                retryAction?.enabled === true && (
                  <button
                    type="button"
                    data-testid="artifact-reexport"
                    disabled={disabled || pendingAction !== undefined}
                    onClick={() => void handleReExport(exportOperationType)}
                    className="mt-2 rounded-lg border border-amber-700 bg-white px-3 py-1.5 font-semibold text-amber-950 hover:bg-amber-50 disabled:opacity-50"
                    aria-label={`최신 리비전을 ${artifactFormat?.toUpperCase() ?? "파일"}로 다시 내보내기`}
                  >
                    최신 리비전으로 다시 내보내기
                  </button>
                )}
            </div>
          )}
          {artifactStale && (
            <p
              className="mt-2 rounded-md bg-amber-100 px-2 py-1.5 text-amber-900"
              role="status"
              data-testid="artifact-stale"
            >
              이 파일은 이전 리비전에서 생성되었습니다. 최신 내용이 필요하면
              다시 내보내세요.
            </p>
          )}
        </section>
      )}
      {operation.error && (
        <p
          className="mt-2 rounded-lg bg-red-50 px-2.5 py-2 text-xs text-red-700"
          role="alert"
        >
          {operation.error}
        </p>
      )}
      {actionError && (
        <p
          className="mt-2 rounded-lg bg-red-50 px-2.5 py-2 text-xs text-red-700"
          role="alert"
          data-testid="operation-action-error"
        >
          {actionError}
        </p>
      )}
      {approvalExpired && (
        <p
          className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-800"
          role="status"
        >
          승인 기한이 만료되었습니다. 작업을 다시 시도해 주세요.
        </p>
      )}
      {retryUnavailable && (
        <p
          className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-800"
          role="status"
          data-testid="operation-retry-unavailable"
        >
          이 오류는 재시도할 수 없습니다. 내용을 확인한 뒤 새 명령을 보내
          주세요.
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {actions
          .filter((action) =>
            ["retry", "approve", "reject", "cancel", "undo"].includes(
              action.id,
            ),
          )
          .map((action) => {
            const unavailable =
              disabled ||
              pendingAction !== undefined ||
              !action.enabled ||
              (action.id === "approve" && approvalExpired) ||
              (action.id === "retry" && operation.retryable === false);
            const reasonId =
              !action.enabled && action.reason
                ? `operation-${operation.operationId}-${action.id}-reason`.replace(
                    /[^a-zA-Z0-9_-]/g,
                    "-",
                  )
                : undefined;
            return (
              <div
                key={action.id}
                className="flex max-w-full flex-col items-start gap-1"
              >
                <button
                  type="button"
                  disabled={unavailable}
                  title={unavailable ? action.reason : undefined}
                  aria-describedby={reasonId}
                  data-testid={`operation-${action.id}`}
                  onClick={() =>
                    void handleOperationAction(
                      action.id as
                        "retry" | "cancel" | "approve" | "reject" | "undo",
                    )
                  }
                  className={
                    action.variant === "primary"
                      ? "rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                      : action.variant === "danger"
                        ? "rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
                        : "rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  }
                >
                  {action.label}
                </button>
                {reasonId && (
                  <p
                    id={reasonId}
                    className="max-w-56 text-[11px] leading-snug text-slate-600"
                    data-testid={`operation-${action.id}-reason`}
                  >
                    {action.reason}
                  </p>
                )}
              </div>
            );
          })}
      </div>
    </article>
  );
}
