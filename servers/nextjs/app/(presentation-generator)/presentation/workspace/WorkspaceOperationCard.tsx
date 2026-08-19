import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  downloadWorkspaceArtifact,
  WorkspaceApiError,
} from "../../services/api/workspace";
import {
  KNOWN_OPERATION_STATUSES,
  isKnownWorkspaceOperationType,
  type WorkspaceOperationActionId,
  type WorkspaceOperationAnchor,
  type WorkspacePageRemakeRequester,
  type WorkspacePresentationRefreshState,
} from "./types";
import {
  APPROVABLE,
  STATUS_LABELS,
  TERMINAL,
} from "./WorkspaceOperationLabels";
import {
  activitySummary,
  actorSummary,
  artifactExpired,
  artifactFileName,
  booleanValue,
  displayResultValue,
  formatDuration,
  formatTimestamp,
  numberValue,
  qualityMetric,
  qualityReviewHistory,
  qualityReviewIssueDetails,
  recordValue,
  resultLabel,
  safeRequestError,
  slideIndexes,
  stringList,
  stringValue,
  targetSummary,
  triggerBrowserDownload,
} from "./WorkspaceOperationResult";
import { QualityReviewHistoryList } from "./QualityReviewHistoryList";
import {
  QualityReviewRemainingIssues,
  remainingIssueList,
} from "./QualityReviewRemainingIssues";


interface WorkspaceOperationCardProps {
  operation: WorkspaceOperationAnchor;
  currentRevision?: number;
  disabled?: boolean;
  onAction: (operationId: string, action: "retry" | "cancel" | "approve" | "reject" | "undo") => void | Promise<void>;
  onReExport?: (operationType: "export.pptx" | "export.pdf") => void | Promise<void>;
  onFocusSlide?: (slideIndex: number) => void;
  onRemakePage?: WorkspacePageRemakeRequester;
  presentationRefreshState?: WorkspacePresentationRefreshState;
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

type TemplateApplicationEvidence = {
  templateId?: string;
  templateDigest?: string;
  referenceLayoutDigest?: string;
  compositionDigest?: string;
  ruleDigest?: string;
  semanticRoles: string[];
  layoutRoles: string[];
  pageNumbers: number[];
  slideCount?: number;
  visualFidelityPassed: boolean;
  previewMatches: boolean;
  nativeMatches: boolean;
  readbackVerified: boolean;
};

function matchesResolvedSnapshot(
  snapshot: Record<string, unknown>,
  evidence: Pick<
    TemplateApplicationEvidence,
    "referenceLayoutDigest" | "compositionDigest" | "ruleDigest" | "slideCount"
  >,
): boolean {
  const referenceLayoutDigest = stringValue(
    snapshot.reference_layout_digest,
    snapshot.referenceLayoutDigest,
  );
  const compositionDigest = stringValue(
    snapshot.composition_digest,
    snapshot.compositionDigest,
  );
  const ruleDigest = stringValue(snapshot.rule_digest, snapshot.ruleDigest);
  const slideCount = numberValue(snapshot.slide_count, snapshot.slideCount);
  return Boolean(
    evidence.referenceLayoutDigest &&
      evidence.compositionDigest &&
      evidence.ruleDigest &&
      evidence.slideCount !== undefined &&
      referenceLayoutDigest === evidence.referenceLayoutDigest &&
      compositionDigest === evidence.compositionDigest &&
      ruleDigest === evidence.ruleDigest &&
      slideCount === evidence.slideCount,
  );
}

function templateApplicationEvidence(
  result: Record<string, unknown>,
): TemplateApplicationEvidence {
  // The durable design.apply receipt is snake_case.  Keep the older resolved
  // aliases for historical rows, but prefer neither spelling: both describe the
  // same immutable composition and must yield the same completed UI state.
  const templateSnapshot = recordValue(
    result.template_snapshot ?? result.templateSnapshot,
  );
  const referenceLayoutDigest = stringValue(
    result.resolved_reference_layout_digest,
    result.resolvedReferenceLayoutDigest,
    result.reference_layout_digest,
    result.referenceLayoutDigest,
    templateSnapshot.reference_layout_digest,
    templateSnapshot.referenceLayoutDigest,
  );
  const compositionDigest = stringValue(
    result.resolved_composition_digest,
    result.resolvedCompositionDigest,
    result.composition_digest,
    result.compositionDigest,
    recordValue(result.resolved_composition_pin).digest,
    recordValue(result.resolvedCompositionPin).digest,
  );
  const ruleDigest = stringValue(
    result.resolved_rule_digest,
    result.resolvedRuleDigest,
    result.rule_digest,
    result.ruleDigest,
  );
  const slideCount = numberValue(result.slide_count, result.slideCount);
  const pageNumbers = (Array.isArray(result.page_numbers)
    ? result.page_numbers
    : Array.isArray(result.pageNumbers)
      ? result.pageNumbers
      : []
  ).filter(
    (value): value is number => typeof value === "number" && Number.isInteger(value),
  );
  const visualFidelity = recordValue(
    result.visual_fidelity ?? result.visualFidelity,
  );
  const visualFidelityPassed =
    stringValue(visualFidelity.status, result.visual_fidelity_status, result.visualFidelityStatus) ===
      "passed" ||
    booleanValue(visualFidelity.passed, result.visual_fidelity_passed, result.visualFidelityPassed) === true;
  const evidence = {
    templateId: stringValue(
      result.resolved_template_id,
      result.resolvedTemplateId,
      result.template_id,
      result.templateId,
      templateSnapshot.resolved_template_id,
      templateSnapshot.resolvedTemplateId,
      templateSnapshot.template_id,
      templateSnapshot.templateId,
    ),
    templateDigest: stringValue(
      result.resolved_template_digest,
      result.resolvedTemplateDigest,
      result.template_digest,
      result.templateDigest,
      templateSnapshot.resolved_template_digest,
      templateSnapshot.resolvedTemplateDigest,
      templateSnapshot.template_digest,
      templateSnapshot.templateDigest,
    ),
    referenceLayoutDigest,
    compositionDigest,
    ruleDigest,
    semanticRoles: stringList(result.semantic_roles_used, result.semanticRolesUsed),
    layoutRoles: stringList(result.layout_roles_used, result.layoutRolesUsed),
    pageNumbers,
    slideCount,
    visualFidelityPassed,
    previewMatches: false,
    nativeMatches: false,
    readbackVerified:
      booleanValue(result.readback_verified, result.readbackVerified) === true,
  } satisfies TemplateApplicationEvidence;
  return {
    ...evidence,
    previewMatches: matchesResolvedSnapshot(
      recordValue(result.preview_snapshot ?? result.previewSnapshot),
      evidence,
    ),
    nativeMatches: matchesResolvedSnapshot(
      recordValue(result.native_pptx_snapshot ?? result.nativePptxSnapshot),
      evidence,
    ),
  };
}

function templateApplicationEvidenceComplete(
  evidence: TemplateApplicationEvidence,
): boolean {
  return Boolean(
    evidence.templateId &&
    evidence.templateDigest &&
      evidence.referenceLayoutDigest &&
      evidence.compositionDigest &&
      evidence.ruleDigest &&
      evidence.visualFidelityPassed &&
      evidence.previewMatches &&
      evidence.nativeMatches &&
      evidence.readbackVerified &&
      evidence.slideCount !== undefined &&
      evidence.pageNumbers.length === evidence.slideCount &&
      evidence.pageNumbers.every((pageNumber, index) => pageNumber === index + 1),
  );
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
  const t = useTranslations("qualityReview.workspace");
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
  const capacityWaiting =
    operation.status === "queued" && operation.capacityWaiting === true;
  const capacityWaitMessage =
    operation.queuePosition !== undefined
      ? `생성 용량 대기 중 · 대기 순번 ${operation.queuePosition}번째`
      : "생성 용량 대기 중 · 슬롯이 확보되면 자동으로 시작합니다.";
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
    operation.operationType === "design.apply" ||
    result.kind === "template.apply" ||
    result.kind === "design.apply";
  const templateEvidence = isTemplateApply
    ? templateApplicationEvidence(result)
    : undefined;
  const templateEvidenceComplete =
    templateEvidence !== undefined &&
    templateApplicationEvidenceComplete(templateEvidence);
  const templateProgressMessage =
    capacityWaiting
      ? capacityWaitMessage
      : progress === undefined
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
  const remainingIssues = remainingIssueList(result, remainingIssueDetails);
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
            {capacityWaiting
              ? "생성 용량 대기 중"
              : operation.status === "succeeded" && isTemplateApply && !templateEvidenceComplete
              ? "시각 검증 증거 대기"
              : STATUS_LABELS[operation.status] ??
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
              {t("lastProgress", { time: latestProgressAt })}
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
      {capacityWaiting && !isTemplateApply && (
        <p
          className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-800"
          role="status"
          aria-live="polite"
          data-testid="generation-capacity-waiting"
        >
          {capacityWaitMessage}
        </p>
      )}
      {latestMessage && operation.status !== "succeeded" && !capacityWaiting && (
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
      {operation.status === "succeeded" && isTemplateApply && templateEvidence && (
        <section
          className={`mt-2 rounded-lg border px-2.5 py-2 text-xs ${
            templateEvidenceComplete
              ? "border-violet-100 bg-violet-50 text-violet-950"
              : "border-amber-200 bg-amber-50 text-amber-950"
          }`}
          aria-label="AI 저작 템플릿 변경 결과"
          data-testid="template-apply-result"
        >
          <p className="font-semibold">
            {templateEvidenceComplete
              ? stringValue(result.message) ?? "AI 저작 자료의 시각 검증이 완료되었습니다."
              : "자료는 생성되었지만 시각 검증 증거가 부족하여 완료로 표시하지 않습니다."}
          </p>
          <p className="mt-1 text-violet-800">
            원본 자료는 변경되지 않았습니다.
          </p>
          <dl
            className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 break-words"
            data-testid="template-apply-evidence"
          >
            <dt>템플릿</dt>
            <dd>{templateEvidence.templateId ?? "미확인"}</dd>
            <dt>템플릿 digest</dt>
            <dd>{templateEvidence.templateDigest ?? "미확인"}</dd>
            <dt>Reference-layout digest</dt>
            <dd>{templateEvidence.referenceLayoutDigest ?? "미확인"}</dd>
            <dt>Composition / rule digest</dt>
            <dd>
              {templateEvidence.compositionDigest ?? "미확인"} / {templateEvidence.ruleDigest ?? "미확인"}
            </dd>
            <dt>시각 충실도</dt>
            <dd>{templateEvidence.visualFidelityPassed ? "통과" : "미통과 또는 미확인"}</dd>
            <dt>Preview / native snapshot</dt>
            <dd>
              {templateEvidence.previewMatches && templateEvidence.nativeMatches
                ? "동일한 resolved snapshot 확인"
                : "동일 snapshot 미확인"}
            </dd>
            <dt>사용 semantic roles</dt>
            <dd>{templateEvidence.semanticRoles.join(", ") || "미확인"}</dd>
            <dt>사용 layout roles</dt>
            <dd>{templateEvidence.layoutRoles.join(", ") || "미확인"}</dd>
            <dt>페이지 번호</dt>
            <dd>
              {templateEvidence.pageNumbers.length > 0
                ? `${templateEvidence.pageNumbers.join(", ")} / ${templateEvidence.slideCount ?? "?"}장`
                : "미확인"}
            </dd>
            <dt>Native PPTX readback</dt>
            <dd>{templateEvidence.readbackVerified ? "검증됨" : "미확인"}</dd>
          </dl>
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
            <dt>{t("issueSlides")}</dt>
            <dd>
              {issueSlideCount !== undefined ? `${issueSlideCount}장` : "-"}
            </dd>
            <dt>{t("detailedIssues")}</dt>
            <dd>{t("issuesCount", { count: detailedIssueCount })}</dd>
            <dt>{t("fixedSlides")}</dt>
            <dd>{t("slidesCount", { count: fixedCount ?? 0 })}</dd>
            <dt>{t("resolvedIssues")}</dt>
            <dd>{t("issuesCount", { count: resolvedIssueCount })}</dd>
            <dt>{t("remainingIssues")}</dt>
            <dd>
              {remainingIssueCount !== undefined
                ? t("issuesCount", { count: remainingIssueCount })
                : "-"}
            </dd>
            {patternClusterCount !== undefined && (
              <>
                <dt>{t("repairPatterns")}</dt>
                <dd>
                  {t("patternSummary", { clusters: patternClusterCount, recipes: patternRecipeCount ?? 0 })}
                  {patternReuseApplied !== undefined
                    ? t("patternReuse", { count: patternReuseApplied })
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
              aria-label={t("pipelineMetricsAria")}
              data-testid="quality-review-pipeline-metrics"
            >
              <h4 className="font-semibold">{t("pipelineMetricsTitle")}</h4>
              {(qualityWallTime !== undefined || qualityStageAggregateTime !== undefined) && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-blue-900">
                  {qualityWallTime !== undefined && (
                    <span data-testid="quality-review-wall-time">{t("wallTime", { duration: formatDuration(qualityWallTime) })}</span>
                  )}
                  {qualityStageAggregateTime !== undefined && (
                    <span data-testid="quality-review-stage-aggregate-time">
                      {t("aggregateStageTime", { duration: formatDuration(qualityStageAggregateTime) })}
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
                  {t("llmNoCandidateReasons", { reasons: qualityLlmNoCandidateReasons.join(", ") })}
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
          <QualityReviewRemainingIssues
            issues={remainingIssues}
            totalIssueCount={remainingIssues.length}
            remainingSlideIndexes={slideIndexes(
              result.remaining_slide_indices,
              result.remaining_pages,
            )}
            disabled={disabled}
            onFocusSlide={onFocusSlide}
            onRemakePage={onRemakePage}
          />
          <QualityReviewHistoryList items={qualityHistory} />
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
      {operation.error && operation.status !== "succeeded" && (
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
