import { useApprovalExpired, editabilityReasonLabels, exportCompletionLabel, type WorkspaceOperationCardProps } from "./workspaceOperationCardHelpers";
import { useEffect, useState } from "react";
import { ConversationMessageFrame } from "../conversation/ConversationPrimitives";
import { WorkspaceGenerationProgress } from "./WorkspaceGenerationProgress";
import { activeGenerationStep, buildGenerationProgress, latestGenerationEventEvidence } from "./generationProgressSteps";
import { canResumeGenerationFailure, generationFailureKey } from "./generationFailure";
import { operationFailureMessage, operationProgressEvidence, type WorkspaceOperationObservation } from "./WorkspaceOperationPresentation";
import { useLocale, useTranslations } from "next-intl";
import SvgQualityStatusSummary from "./SvgQualityStatusSummary";
import { WorkspacePartialRecovery } from "./WorkspacePartialRecovery";

import { translateBackendMessage } from "@/lib/backend-messages-intl";
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
  displayResultValue,
  formatDuration,
  formatTimestamp,
  generationCompletionSummary,
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
import {
  templateApplicationEvidence,
  templateApplicationEvidenceComplete,
} from "./WorkspaceOperationTemplateEvidence";




export function WorkspaceOperationCard({
  operation,
  currentRevision,
  disabled = false,
  onAction,
  onReExport,
  isSvgDeck = false,
  finalPptxReviewStatus,
  onFinalPptxReview,
  onFocusSlide,
  onRemakePage,
  presentationRefreshState,
  observation,
  composerCancelOperationId,
}: WorkspaceOperationCardProps) {
  const t = useTranslations("qualityReview.workspace");
  const tOperation = useTranslations("presentationOperationCard");
  const tFailure = useTranslations("generationFailure");
  const tProgress = useTranslations("presentationGenerationProgress");
  const locale = useLocale();
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
  const historicalResolution = operation.historicalResolution;
  const resolvedByLaterSuccess = historicalResolution === "resolved";
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
  const latestEvent = [...operation.events]
    .reverse()
    .find((event) => event.message || event.messageKey);
  // The backend's queued event carries English text and no key; the event type is the stable identity.
  const latestMessage = latestEvent
    ? latestEvent.type === "queued" && !latestEvent.messageKey
      ? tOperation("queuedMessage")
      : translateBackendMessage(
          {
            key: latestEvent.messageKey,
            params: latestEvent.messageParams,
            text: latestEvent.message,
          },
          locale,
        )
    : undefined;
  const title =
    translateBackendMessage(
      { key: operation.titleKey, text: operation.title },
      locale,
    ) ?? operation.title;
  const isPresentationGeneration =
    operation.operationType === "presentation.generate";
  const progressEvidence = operationProgressEvidence(operation);
  const failed = ["failed", "dead_letter", "deadline_exceeded"].includes(operation.status);
  const failureClassKey = isPresentationGeneration ? generationFailureKey(operation.failureClass) : null;
  const actor = actorSummary(operation);
  const createdAt = formatTimestamp(operation.createdAt, locale);
  const updatedAt = formatTimestamp(operation.updatedAt, locale);
  const target = targetSummary(operation.target, operation.operationType);
  // The server's activity_summary is English for generation; show the translated step.
  const liveGenerationStep = isPresentationGeneration && !TERMINAL.has(operation.status)
    ? activeGenerationStep(buildGenerationProgress({
      activity: null, evidence: latestGenerationEventEvidence(operation.events), now: 0,
    }))
    : null;
  const activity = isPresentationGeneration && !TERMINAL.has(operation.status)
    ? tProgress(liveGenerationStep ? `step_${liveGenerationStep}` : "headerRunning")
    : activitySummary(operation);
  const revision =
    operation.expectedRevision !== undefined ||
    operation.resultRevision !== undefined
      ? `${operation.expectedRevision ?? "-"} → ${operation.resultRevision ?? "-"}`
      : undefined;
  const operationMetadata = [
    ["요청 ID", operation.operationId],
    observation?.lastConfirmedAt ? ["마지막 서버 확인", formatTimestamp(observation.lastConfirmedAt, locale) ?? "확인 정보 없음"] : undefined,
    actor ? ["실행 주체", actor] : undefined,
    createdAt ? ["시작", createdAt] : undefined,
    updatedAt ? ["업데이트", updatedAt] : undefined,
    target ? ["대상", target] : undefined,
    revision ? ["리비전", revision] : undefined,
    activity ? ["활동", activity] : undefined,
  ].filter((item): item is [string, string] => item !== undefined);
  const result = recordValue(operation.result);
  const publicExportWorkerEvidence = recordValue(
    result.public_export_worker_evidence ?? result.publicExportWorkerEvidence,
  );
  const exportEditability = recordValue(
    publicExportWorkerEvidence.editability,
  );
  const exportQuality = recordValue(publicExportWorkerEvidence.quality);
  const exportProducerTiming = recordValue(
    publicExportWorkerEvidence.producerTiming,
  );
  const exportEditabilityVerdict = stringValue(
    exportEditability.verdict,
    exportEditability.status,
  );
  const exportQualityStatus = stringValue(exportQuality.status);
  const exportEditabilityReasons = Array.isArray(exportEditability.reasonCodes)
    ? exportEditability.reasonCodes.filter(
        (reason): reason is string =>
          typeof reason === "string" && reason.length > 0,
      )
    : [];
  const exportEditabilityImageOnlyPages = Array.isArray(
    exportEditability.imageOnlyPages,
  )
    ? exportEditability.imageOnlyPages.filter(
        (page): page is number =>
          typeof page === "number" && Number.isSafeInteger(page) && page >= 1,
      )
    : [];
  const exportQualitySlideFallbacks = Array.isArray(exportQuality.slideFallbacks)
    ? exportQuality.slideFallbacks.flatMap((value) => {
        const fallback = recordValue(value);
        const slideNumber = numberValue(fallback.n, fallback.slideNumber);
        if (slideNumber === undefined || slideNumber < 1) return [];
        return [{
          slideNumber,
          code: stringValue(fallback.code, fallback.reason) ?? "unknown",
        }];
      })
    : [];
  const exportImageFallbackPages = [...new Set([
    ...exportEditabilityImageOnlyPages,
    ...exportQualitySlideFallbacks.map((fallback) => fallback.slideNumber),
  ])].sort((left, right) => left - right);
  const exportCleanupFailurePages = Array.isArray(exportProducerTiming.slides)
    ? exportProducerTiming.slides.flatMap((value) => {
        const slide = recordValue(value);
        const recovery = recordValue(slide.recovery);
        const slideNumber = numberValue(slide.n, slide.slideNumber);
        return recovery.cleanupFailure === true &&
          slideNumber !== undefined &&
          slideNumber >= 1
          ? [slideNumber]
          : [];
      })
    : [];
  const exportEditabilitySlides = Array.isArray(exportEditability.slides)
    ? exportEditability.slides.flatMap((value) => {
        const slide = recordValue(value);
        const slideNumber = numberValue(slide.slideNumber);
        if (slideNumber === undefined || slideNumber < 1) return [];
        const verdict = stringValue(slide.verdict, slide.status) ?? "unknown";
        const reasons = Array.isArray(slide.reasons)
          ? slide.reasons.filter(
              (reason): reason is string =>
                typeof reason === "string" && reason.length > 0,
            )
          : [];
        const imageOnly = slide.imageOnly === true;
        const photoOnlyExempt = slide.photoOnlyExempt === true;
        if (
          !imageOnly &&
          !photoOnlyExempt &&
          !["partially-editable", "unknown", "not-editable"].includes(verdict) &&
          reasons.length === 0
        )
          return [];
        return [{ slideNumber, verdict, imageOnly, photoOnlyExempt, reasons }];
      })
    : [];
  const editabilityReasonLabel = (reason: string) =>
    editabilityReasonLabels[reason] ?? "세부 확인 필요";
  const generationCompletion = isPresentationGeneration
    ? generationCompletionSummary(result)
    : undefined;
  const isQualityReview =
    operation.operationType === "quality.review" ||
    result.kind === "quality.review";
  const isSvgReview = isQualityReview && result.source_format === "svg" && result.mode === "analyze_only";
  const isExport =
    operation.operationType === "export.pptx" ||
    operation.operationType === "export.pdf";
  const isPptxExport = operation.operationType === "export.pptx";
  const exportEditabilityCompletion = !isPptxExport
    ? undefined
    : exportQualityStatus === "image-only" ||
        exportEditabilityVerdict === "not-editable"
      ? "image-only"
      : exportImageFallbackPages.length > 0 ||
          exportQualityStatus === "partially-editable" ||
          exportEditabilityVerdict === "partially-editable"
        ? "partially-editable"
        : exportEditabilityVerdict === "fully-editable" &&
            exportQualityStatus !== "partially-editable" &&
            exportQualityStatus !== "image-only"
          ? "fully-editable"
          : "unknown";
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

  const finalPptxReviewVisible =
    isSvgDeck && isPptxExport && operation.status === "succeeded" && Boolean(artifactId);
  const handleFinalPptxReview = async () => {
    if (!artifactId || !onFinalPptxReview || pendingAction ||
      finalPptxReviewStatus === "running" || finalPptxReviewStatus === "deferred") return;
    setPendingAction("final-pptx-review");
    setActionError(undefined);
    try {
      await onFinalPptxReview(artifactId);
    } catch (error) {
      setActionError(safeRequestError(error, tOperation("actionFailed")));
    } finally {
      setPendingAction(undefined);
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
          tOperation("actionFailed"),
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
        safeRequestError(error, tOperation("reexportFailed")),
      );
    } finally {
      setPendingAction(undefined);
    }
  };

  return (
    <article
      className="w-full text-[15px] leading-[1.6]"
      aria-label={tOperation("operationAria", { title })}
      data-workspace-anchor-id={operation.id}
      data-operation-id={operation.operationId}
      data-operation-status={operation.status}
    >
      <ConversationMessageFrame appearance="assistant">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-slate-900">
            {title}
          </h3>
          <p
            className="mt-0.5 text-xs text-slate-500"
            role="status"
            aria-live="polite"
          >
            {historicalResolution === "resolved"
              ? tOperation("resolved")
              : historicalResolution === "superseded"
                ? tOperation("superseded")
              : capacityWaiting
              ? tOperation("capacityWaiting")
              : operation.status === "succeeded" && isTemplateApply && !templateEvidenceComplete
              ? tOperation("visualEvidenceWaiting")
              : STATUS_LABELS[operation.status] ??
              tOperation("unsupportedStatus", { status: operation.status })}
          </p>
        </div>
        {!isPresentationGeneration && !TERMINAL.has(operation.status) &&
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
          {tOperation("unsupportedStatusNotice")}
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
            ? tOperation("approvalExpired", { title })
            : tOperation("approvalRequired", { title })}
        </p>
      )}
      {operationMetadata.length > 0 && (
        <details className="mt-2 text-xs text-slate-600">
          <summary className="cursor-pointer select-none font-medium">{tOperation("viewDetails")}</summary>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 rounded-lg bg-slate-50 px-2.5 py-2" data-testid="operation-metadata">
            {operationMetadata.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="font-medium text-slate-500">{label}</dt>
                <dd className="min-w-0 break-words text-slate-700">{value}</dd>
              </div>
            ))}
          </dl>
          {progressEvidence.messages.length > 0 && <ol className="mt-2 space-y-1 text-slate-600">
            {progressEvidence.messages.map((message, index) => <li key={`${index}-${message}`}>{message}</li>)}
          </ol>}
          {TERMINAL.has(operation.status) && progress !== undefined && <p className="mt-2">{tOperation("lastProgress", { percent: Math.round(progress) })}</p>}
        </details>
      )}
      {isPresentationGeneration && !TERMINAL.has(operation.status) && <WorkspaceGenerationProgress operation={operation} observation={observation} locale={locale} />}
      {!isPresentationGeneration && !TERMINAL.has(operation.status) && displayedProgress !== undefined && (
        <div className="mt-3">
          <div
            className="h-1.5 overflow-hidden rounded-full bg-slate-100"
            role="progressbar"
            aria-label={tOperation("progressAria")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(displayedProgress)}
            aria-valuetext={
              qualityResultSaving
                ? tOperation("savingResultRevision")
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
              ? tOperation("savingResult")
              : `${Math.round(displayedProgress)}%`}
          </p>
        </div>
      )}
      {qualityResultSaving && (
        <p
          className="mt-2 text-xs text-slate-600"
          role="status"
          aria-live="polite"
        >
          {tOperation("savingReviewResult")}
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
      {latestMessage && !isPresentationGeneration && !TERMINAL.has(operation.status) && !operation.error && !capacityWaiting && (
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
      {operation.status === "succeeded" && generationCompletion && (
        <section
          className={`mt-2 rounded-lg border px-2.5 py-2 text-xs ${
            generationCompletion.reviewRequired > 0
              ? "border-amber-200 bg-amber-50 text-amber-950"
              : "border-emerald-100 bg-emerald-50 text-emerald-950"
          }`}
          aria-label={tOperation("generationResultAria")}
          data-testid="generation-completion-summary"
        >
          <p className="font-semibold">
            {tOperation("generationSummary", { total: generationCompletion.total, normal: generationCompletion.normal, repaired: generationCompletion.autoRepaired, review: generationCompletion.reviewRequired })}
          </p>
          {generationCompletion.reviewPages.length > 0 && (
            <ul className="mt-2 space-y-1" data-testid="generation-review-pages">
              {generationCompletion.reviewPages.map((page) => (
                <li key={page.index}>
                  {tOperation("reviewPage", { page: page.index + 1, reasons: page.reasons.join(" · ") })}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {operation.status === "succeeded" && isVersionRestore && (
        <section
          className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-950"
          aria-label={tOperation("restoreResultAria")}
          data-testid="version-restore-result"
        >
          <p className="font-semibold">{tOperation("restoreComplete")}</p>
          <p className="mt-1 text-emerald-800">
            {sourceRevision !== undefined && publishedRevision !== undefined
              ? tOperation("revisionTransition", { source: sourceRevision, published: publishedRevision })
              : publishedRevision !== undefined
                ? tOperation("revision", { revision: publishedRevision })
                : tOperation("publishedNewRevision")}
            {restoredSlideCount !== undefined
              ? tOperation("slideCountSuffix", { count: restoredSlideCount })
              : ""}
          </p>
          {restoredVersionId && (
            <p
              className="mt-1 truncate text-emerald-700"
              title={restoredVersionId}
            >
              {tOperation("targetVersion", { id: restoredVersionId })}
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
          aria-label={tOperation("templateResultAria")}
          data-testid="template-apply-result"
        >
          <p className="font-semibold">
            {templateEvidenceComplete
              ? stringValue(result.message) ?? tOperation("visualVerified")
              : tOperation("visualEvidenceMissing")}
          </p>
          <p className="mt-1 text-violet-800">
            {tOperation("sourceUnchanged")}
          </p>
          <dl
            className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 break-words"
            data-testid="template-apply-evidence"
          >
            <dt>{tOperation("template")}</dt>
            <dd>{templateEvidence.templateId ?? tOperation("unknown")}</dd>
            <dt>{tOperation("templateDigest")}</dt>
            <dd>{templateEvidence.templateDigest ?? tOperation("unknown")}</dd>
            <dt>{tOperation("referenceLayoutDigest")}</dt>
            <dd>{templateEvidence.referenceLayoutDigest ?? tOperation("unknown")}</dd>
            <dt>{tOperation("compositionRuleDigest")}</dt>
            <dd>
              {templateEvidence.compositionDigest ?? tOperation("unknown")} / {templateEvidence.ruleDigest ?? tOperation("unknown")}
            </dd>
            <dt>{tOperation("visualFidelity")}</dt>
            <dd>{templateEvidence.visualFidelityPassed ? tOperation("passed") : tOperation("notPassedOrUnknown")}</dd>
            <dt>{tOperation("previewNativeSnapshot")}</dt>
            <dd>
              {templateEvidence.previewMatches && templateEvidence.nativeMatches
                ? tOperation("sameSnapshotVerified")
                : tOperation("sameSnapshotUnknown")}
            </dd>
            <dt>{tOperation("semanticRoles")}</dt>
            <dd>{templateEvidence.semanticRoles.join(", ") || tOperation("unknown")}</dd>
            <dt>{tOperation("layoutRoles")}</dt>
            <dd>{templateEvidence.layoutRoles.join(", ") || tOperation("unknown")}</dd>
            <dt>{tOperation("pageNumbers")}</dt>
            <dd>
              {templateEvidence.pageNumbers.length > 0
                ? tOperation("pageNumbersValue", { pages: templateEvidence.pageNumbers.join(", "), count: templateEvidence.slideCount ?? "?" })
                : tOperation("unknown")}
            </dd>
            <dt>{tOperation("nativePptxReadback")}</dt>
            <dd>{templateEvidence.readbackVerified ? tOperation("verified") : tOperation("unknown")}</dd>
          </dl>
        </section>
      )}
      {operation.status === "succeeded" && isQualityReview && (
        <section
          className="mt-2 rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-2 text-xs text-blue-950"
          aria-label={tOperation("qualityReviewResultAria")}
          data-testid="quality-review-result"
        >
          <p className="font-semibold">
            {stringValue(result.message) ??
              tOperation(isSvgReview ? "svgQualityReviewComplete" : "qualityReviewComplete")}
          </p>
          {isSvgReview && <SvgQualityStatusSummary status={result.qualityStatus} />}
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-blue-800">
            <dt>{tOperation("reviewedPages")}</dt>
            <dd>
              {reviewedCount !== undefined ? tOperation("slidesCount", { count: reviewedCount }) : tOperation("reviewComplete")}
            </dd>
            <dt>{t("issueSlides")}</dt>
            <dd>
              {issueSlideCount !== undefined ? tOperation("slidesCount", { count: issueSlideCount }) : "-"}
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
            <dt>{tOperation("inspectedRevision")}</dt>
            <dd>{inspectedRevision !== undefined ? inspectedRevision : "-"}</dd>
            <dt>{tOperation("savedRevision")}</dt>
            <dd>{qualityRevision !== undefined ? qualityRevision : "-"}</dd>
            <dt>{tOperation("powerPointRefresh")}</dt>
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
              aria-label={tOperation("goToEditedPages")}
            >
              {fixedSlideIndexes.map((slideIndex) => (
                <button
                  key={slideIndex}
                  type="button"
                  onClick={() => onFocusSlide?.(slideIndex)}
                  disabled={!onFocusSlide}
                  className="rounded-md border border-blue-200 bg-white px-2 py-1 font-semibold text-blue-700 hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-1"
                  aria-label={tOperation("goToSlide", { number: slideIndex + 1 })}
                  data-testid={`quality-review-focus-slide-${slideIndex + 1}`}
                >
                  {tOperation("goToNumber", { number: slideIndex + 1 })}
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
          aria-label={tOperation("exportResultAria")}
          data-testid="export-artifact-result"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate font-semibold">
                {artifactFileName(result, artifactFormat)}
              </p>
              <p className="mt-0.5 text-emerald-800">
                {artifactFormat?.toUpperCase() ?? tOperation("file")}
                {sourceRevision !== undefined
                  ? tOperation("sourceRevisionSuffix", { revision: sourceRevision })
                  : ""}
              </p>
              {exportEditabilityCompletion && (
                <p
                  className={
                    exportEditabilityCompletion === "fully-editable"
                      ? "mt-0.5 text-emerald-800"
                      : "mt-0.5 font-medium text-amber-800"
                  }
                  role="status"
                  data-testid="export-editability-verdict"
                >
                  {exportCompletionLabel[exportEditabilityCompletion]}
                </p>
              )}
            </div>
            <div className="flex shrink-0 flex-wrap justify-end gap-2">
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
                  className="rounded-lg bg-emerald-700 px-3 py-1.5 font-semibold text-white hover:bg-emerald-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2"
                  aria-label={tOperation("downloadFileAria", { format: artifactFormat?.toUpperCase() ?? tOperation("exported") })}
                >
                  {tOperation("download")}
                </button>
              )}
              {finalPptxReviewVisible && onFinalPptxReview && (
                <button
                  type="button"
                  data-testid="final-pptx-review"
                  disabled={disabled || pendingAction !== undefined || finalPptxReviewStatus === "running" || finalPptxReviewStatus === "deferred"}
                  onClick={() => void handleFinalPptxReview()}
                  className="rounded-lg border border-indigo-700 bg-white px-3 py-1.5 font-semibold text-indigo-800 hover:bg-indigo-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
                  aria-label={tOperation("finalPptxReviewAria")}
                >
                  {finalPptxReviewStatus === "running"
                    ? tOperation("finalPptxReviewRunning")
                    : finalPptxReviewStatus === "deferred"
                      ? tOperation("finalPptxReviewDeferred")
                      : finalPptxReviewStatus === "passed"
                        ? tOperation("finalPptxReviewPassed")
                        : tOperation("finalPptxReview")}
                </button>
              )}
            </div>
          </div>
          {finalPptxReviewVisible && (
            <p
              className={`mt-2 rounded-md px-2 py-1.5 ${finalPptxReviewStatus === "passed" ? "bg-emerald-100 text-emerald-900" : finalPptxReviewStatus === "failed" ? "bg-red-100 text-red-900" : "bg-indigo-50 text-indigo-900"}`}
              role="status"
              data-testid="final-pptx-review-status"
            >
              {finalPptxReviewStatus === "passed"
                ? tOperation("finalPptxReviewPassedNotice")
                : finalPptxReviewStatus === "failed"
                  ? tOperation("finalPptxReviewFailedNotice")
                  : finalPptxReviewStatus === "deferred"
                    ? tOperation("finalPptxReviewDeferredNotice")
                    : tOperation("finalPptxReviewRequiredNotice")}
            </p>
          )}
          {(exportEditabilitySlides.length > 0 ||
            exportImageFallbackPages.length > 0 ||
            exportEditabilityReasons.length > 0) && (
            <div
              className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-amber-950"
              data-testid="export-editability-evidence"
            >
              <p className="font-medium">{tOperation("editabilityCheck")}</p>
              {exportEditabilityReasons.length > 0 && (
                <p className="mt-1">
                  {tOperation("reasons", { reasons: exportEditabilityReasons.map(editabilityReasonLabel).join(" · ") })}
                </p>
              )}
              {exportEditabilitySlides.length > 0 && (
                <ul className="mt-1 space-y-0.5">
                  {exportEditabilitySlides.map((slide) => (
                    <li key={slide.slideNumber}>
                      {tOperation("pageLabel", { page: slide.slideNumber })} {slide.photoOnlyExempt
                        ? tOperation("sourcePhotoPage")
                        : slide.imageOnly
                          ? tOperation("imageOnly")
                          : slide.verdict === "partially-editable"
                            ? tOperation("partiallyEditable")
                            : tOperation("needsConfirmation")}
                      {slide.reasons.length > 0
                        ? ` · ${slide.reasons.map(editabilityReasonLabel).join(" · ")}`
                        : ""}
                    </li>
                  ))}
                </ul>
              )}
              {exportImageFallbackPages.length > 0 && (
                <p className="mt-1 font-medium" data-testid="export-image-fallback-pages">
                  {tOperation("imageFallbackPages", { pages: exportImageFallbackPages.map((page) => tOperation("pageName", { page })).join(", ") })}
                </p>
              )}
              {exportQualitySlideFallbacks.length > 0 && (
                <ul className="mt-1 space-y-0.5" data-testid="export-image-fallback-reasons">
                  {exportQualitySlideFallbacks.map((fallback) => (
                    <li key={`${fallback.slideNumber}:${fallback.code}`}>
                      {tOperation("pageLabel", { page: fallback.slideNumber })} {editabilityReasonLabel(fallback.code)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {exportCleanupFailurePages.length > 0 && (
            <p
              className="mt-2 rounded-md bg-slate-100 px-2 py-1.5 text-slate-700"
              data-testid="export-cleanup-status"
            >
              {tOperation("cleanupFailure", { pages: exportCleanupFailurePages.map((page) => tOperation("pageName", { page })).join(", ") })}
            </p>
          )}
          {!artifactId && (
            <p
              className="mt-2 rounded-md bg-red-100 px-2 py-1.5 text-red-800"
              role="alert"
            >
              {tOperation("downloadInfoMissing")}
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
              <p role="status">{tOperation("exportExpired")}</p>
              {exportOperationType &&
                onReExport &&
                retryAction?.enabled === true && (
                  <button
                    type="button"
                    data-testid="artifact-reexport"
                    disabled={disabled || pendingAction !== undefined}
                    onClick={() => void handleReExport(exportOperationType)}
                    className="mt-2 rounded-lg border border-amber-700 bg-white px-3 py-1.5 font-semibold text-amber-950 hover:bg-amber-50 disabled:opacity-50"
                    aria-label={tOperation("reexportLatestAria", { format: artifactFormat?.toUpperCase() ?? tOperation("file") })}
                  >
                    {tOperation("reexportLatest")}
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
              {tOperation("staleArtifact")}
            </p>
          )}
        </section>
      )}
      {historicalResolution && operation.supersededByOperationId && (
        <p
          className="mt-2 rounded-lg bg-emerald-50 px-2.5 py-2 text-xs text-emerald-800"
          role="status"
          data-testid="operation-superseded-resolution"
        >
          {tOperation("supersededHistory", { id: operation.supersededByOperationId })}
        </p>
      )}
      {(failed || operation.error) && operation.status !== "succeeded" && !resolvedByLaterSuccess && (
        <p
          className="mt-3 flex items-start gap-2 text-sm text-slate-800"
          role="alert"
        >
          <span aria-hidden="true" className="font-semibold text-red-700">!</span>
          {failureClassKey ? tFailure(`${failureClassKey}.body`) : operationFailureMessage(operation)}
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
          {tOperation("approvalDeadlineExpired")}
        </p>
      )}
      {retryUnavailable && !isPresentationGeneration && !failed && !operation.error && !resolvedByLaterSuccess && (
        <p
          className="mt-2 rounded-lg bg-amber-50 px-2.5 py-2 text-xs text-amber-800"
          role="status"
          data-testid="operation-retry-unavailable"
        >
          {tOperation("retryUnavailable")}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {actions
          .filter((action) =>
            ["retry", "approve", "reject", "cancel", "undo"].includes(
              action.id,
            ) && !(resolvedByLaterSuccess && action.id === "retry") &&
              !( !isExport && action.id === "cancel" && operation.operationId === composerCancelOperationId) &&
              !(action.id === "retry" && (!action.enabled || operation.retryable === false)),
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
      {isPresentationGeneration && failed && !resolvedByLaterSuccess && canResumeGenerationFailure(operation.failureClass) && <WorkspacePartialRecovery
        operationId={operation.operationId} operationVersion={operation.version} currentRevision={currentRevision}
        disabled={disabled || pendingAction !== undefined} onAction={onAction} />}
      </ConversationMessageFrame>
    </article>
  );
}
