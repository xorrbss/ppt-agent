"use client";

import { activeWorkspaceMutationState, reviewRequiredIndicesFor, useWorkspaceGenerationInfoResult, type WorkspaceChatPanelProps, type GenerationInfoResult } from "./workspaceChatPanelHelpers";
import { LocateFixed } from "lucide-react";
import { useTranslations } from "next-intl";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { deriveFlaggedSlideMap, flaggedSlideKey } from "../utils/flaggedSlides";
import { WorkspaceConversationComposer } from "./WorkspaceConversationComposer";
import { WorkspaceMessage } from "./WorkspaceMessage";
import { WorkspaceTimelineOperation } from "./WorkspaceTimelineOperation";
import { WorkspaceAttemptNavigation } from "./WorkspaceAttemptNavigation";
import { useWorkspaceTimeline } from "./useWorkspace";
import {
  useWorkspaceAgentFocus,
  type WorkspaceAgentFocusController,
  type WorkspaceAgentSlideFocus,
} from "./useWorkspaceAgentFocus";
import type {
  WorkspaceCommandDispatcher,
  WorkspaceCommandOptions,
  WorkspacePageRemakeRequester,
  WorkspacePresentationChange,
  WorkspacePresentationChangedHandler,
  WorkspaceOperationAnchor,
  WorkspaceQualityReviewRequester,
} from "./types";
import {
  DashboardApi,
  type PresentationGenerationInfo,
} from "../../services/api/dashboard";
import { withGenerationContext } from "./generationContext";
import { WorkspaceRemakePrompts, type WorkspaceRemakeRequest } from "./WorkspaceRemakePrompts";
import {
  PageRemakeHistory,
  usePageRemakeHistory,
} from "../components/PageRemakeHistory";
import {
  CONNECTION_LABELS,
  parseQualityReviewSlideRange,
  resolveHistoricalExportOperations,
  useActiveExportOperationNotifier,
  type ExportOperationType,
  type QualityReviewScope,
  type SlideRangeParseResult,
} from "./workspaceChatPanelUtils";
import { useWorkspaceScrollContinuity } from "./useWorkspaceScrollContinuity";
import { useWorkspaceGenerationState } from "./useWorkspaceGenerationState";
import { TERMINAL } from "./WorkspaceOperationLabels";
import { isSvgPresentation } from "../utils/presentationSource";

export { parseQualityReviewSlideRange } from "./workspaceChatPanelUtils";
const SLIDESPEC_WORKSPACE_REPAIR_ENABLED =
  process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI !== "false" &&
  process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES !== "false";


export default function WorkspaceChatPanel({
  presentationId,
  presentation,
  currentSlide, currentSlideId, generationPending = false,
  commandsEnabled = true,
  onPresentationChanged,
  onSlideFocus,
  onAgentActivityChange,
  onAgentSlideFocus,
  onCommandDispatcherReady,
  onQualityReviewRequesterReady,
  onPageRemakeRequesterReady,
  onExportOperationStateChange,
  onGenerationStateChange, onEditCurrentSlideReady,
}: WorkspaceChatPanelProps) {
  const t = useTranslations("presentationChatPanel");
  const svgReviewOnly = isSvgPresentation(presentation);
  const reviewRequiredIndices = useMemo(() => reviewRequiredIndicesFor(presentation), [presentation]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const { restore: restoreScroll, remember: rememberScroll } = useWorkspaceScrollContinuity(presentationId);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [qualityReviewPrompt, setQualityReviewPrompt] = useState<{
    currentSlide?: number;
  } | null>(null);
  const [pageRemakePrompt, setPageRemakePrompt] =
    useState<WorkspaceRemakeRequest | null>(null);
  // Remake previews live outside the conversation, so the timeline reloads them
  // explicitly; bumping the token re-reads them after one is applied.
  const [pageRemakeHistoryToken, setPageRemakeHistoryToken] = useState(0);
  const pageRemakeHistory = usePageRemakeHistory(
    presentationId,
    true,
    pageRemakeHistoryToken,
  );
  const [qualityReviewScope, setQualityReviewScope] =
    useState<QualityReviewScope>("all");
  const [qualityReviewRangeInput, setQualityReviewRangeInput] = useState("");
  const [qualityReviewRange, setQualityReviewRange] =
    useState<SlideRangeParseResult | null>(null);
  const [qualityReviewRangeError, setQualityReviewRangeError] = useState<
    string | null
  >(null);
  const [generationInfoResult, setGenerationInfoResult] = useState<GenerationInfoResult | null>(null);
  const hasInitialScrollRef = useRef(false);
  const shouldFollowLatestRef = useRef(true);
  const [followingLatest, setFollowingLatest] = useState(true);
  const previousItemCountRef = useRef(0);
  const previousEventHighWatermarkRef = useRef<string | undefined>(undefined);
  const prependRestoreRef = useRef<{
    scrollHeight: number;
    scrollTop: number;
    itemCount: number;
  } | null>(null);
  // The focus controller is created below from timeline state, so a change that
  // arrives from the hook reaches it through this ref rather than a cycle.
  const agentFocusRef = useRef<WorkspaceAgentFocusController | null>(null);
  const handlePresentationChanged =
    useCallback<WorkspacePresentationChangedHandler>(
      async (change: WorkspacePresentationChange) => {
        agentFocusRef.current?.notePresentationChange(change);
        await onPresentationChanged?.(change);
      },
      [onPresentationChanged],
    );
  const {
    projection,
    loading,
    submitting,
    historyError,
    requestError,
    retryIntent,
    connection,
    operationObservations,
    presentationRefreshStates,
    loadOlder,
    sendCommand,
    performAction,
    retryPendingIntent,
    retryLoad,
    writesEnabled,
  } = useWorkspaceTimeline({
    presentationId,
    enabled: true,
    commandsEnabled,
    onPresentationChanged: handlePresentationChanged,
  });

  const { generationInfo, generationInfoLoading } = useWorkspaceGenerationInfoResult(presentationId, generationInfoResult, setGenerationInfoResult);

  const timelineItems = useMemo(
    () =>
      resolveHistoricalExportOperations(
        withGenerationContext(presentationId, generationInfo, projection.items),
      ),
    [generationInfo, presentationId, projection.items],
  );

  const { state: generationState, mutationActive } = useWorkspaceGenerationState({
    items: projection.items,
    loading,
    onChange: onGenerationStateChange,
  });

  const { activeMutation, cancelOperation, showFollowControl, restartBlockedReason } =
    activeWorkspaceMutationState(projection.items, writesEnabled, submitting, performAction, generationPending, loading, mutationActive, retryIntent?.reason);

  useActiveExportOperationNotifier(timelineItems, onExportOperationStateChange);
  const agentFocus = useWorkspaceAgentFocus({
    presentationId,
    submitting,
    items: projection.items,
    onActivityChange: onAgentActivityChange,
    onSlideFocus: onAgentSlideFocus,
  });
  useEffect(() => {
    agentFocusRef.current = agentFocus;
  }, [agentFocus]);

  const sendCommandRef = useRef(sendCommand);
  useEffect(() => {
    sendCommandRef.current = sendCommand;
  }, [sendCommand]);
  // Single funnel for every command the panel can start, so the pages a request
  // names are recorded once instead of at each call site.
  const commandDispatcher = useCallback<WorkspaceCommandDispatcher>(
    (command, options?: WorkspaceCommandOptions) => {
      agentFocusRef.current?.noteDispatch(options);
      return sendCommandRef.current(command, options);
    },
    [],
  );

  useEffect(() => {
    onCommandDispatcherReady?.(writesEnabled ? commandDispatcher : null);
    return () => onCommandDispatcherReady?.(null);
  }, [commandDispatcher, onCommandDispatcherReady, writesEnabled]);

  const qualityReviewRequester = useCallback<WorkspaceQualityReviewRequester>(
    (requestedSlide, options) => {
      setPageRemakePrompt(null);
      setQualityReviewScope(options?.scope ?? "all");
      setQualityReviewRangeInput("");
      setQualityReviewRange(null);
      setQualityReviewRangeError(null);
      setQualityReviewPrompt(
        requestedSlide === undefined ? {} : { currentSlide: requestedSlide },
      );
      shouldFollowLatestRef.current = true;
      setFollowingLatest(true);
    },
    [],
  );

  useEffect(() => {
    onQualityReviewRequesterReady?.(qualityReviewRequester);
    return () => onQualityReviewRequesterReady?.(null);
  }, [onQualityReviewRequesterReady, qualityReviewRequester]);

  const pageRemakeRequester = useCallback<WorkspacePageRemakeRequester>(
    (requested) => {
      const slideIndexes = Array.isArray(requested) ? requested : [requested];
      setQualityReviewPrompt(null);
      setPageRemakePrompt({ slideIndexes, batch: Array.isArray(requested) });
      shouldFollowLatestRef.current = true;
      setFollowingLatest(true);
    },
    [],
  );

  useEffect(() => {
    onPageRemakeRequesterReady?.(pageRemakeRequester);
    return () => onPageRemakeRequesterReady?.(null);
  }, [onPageRemakeRequesterReady, pageRemakeRequester]);

  useLayoutEffect(() => {
    hasInitialScrollRef.current = false;
    shouldFollowLatestRef.current = true;
    setFollowingLatest(true);
    previousItemCountRef.current = 0;
    previousEventHighWatermarkRef.current = undefined;
    prependRestoreRef.current = null;
    setLoadingOlder(false);
    setQualityReviewPrompt(null);
    setPageRemakePrompt(null);
    setQualityReviewRangeInput("");
    setQualityReviewRange(null);
    setQualityReviewRangeError(null);
  }, [presentationId]);

  useLayoutEffect(() => {
    if ((!qualityReviewPrompt && !pageRemakePrompt) || !scrollRef.current) return;
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [pageRemakePrompt, qualityReviewPrompt]);

  const handleQualityReviewSubmit = useCallback(async () => {
    if (!qualityReviewPrompt) return;
    const selectedSlide = qualityReviewPrompt.currentSlide;
    const currentScopeSelected = qualityReviewScope === "current";
    const reviewRequiredSelected = qualityReviewScope === "review_required";
    if (reviewRequiredSelected && reviewRequiredIndices.length === 0) return;
    const subsetScopeSelected = qualityReviewScope === "subset";
    if (subsetScopeSelected && !qualityReviewRange) {
      setQualityReviewRangeError(
        "페이지 범위를 확인한 뒤 검수를 시작해 주세요.",
      );
      return;
    }
    const slideIndices = reviewRequiredSelected
      ? reviewRequiredIndices
      : subsetScopeSelected
      ? (qualityReviewRange?.indices ?? [])
      : currentScopeSelected && selectedSlide !== undefined
        ? [selectedSlide]
        : [];
    const subject = reviewRequiredSelected
      ? `검수 필요 ${reviewRequiredIndices.map((index) => index + 1).join(", ")}번 슬라이드를`
      : subsetScopeSelected
      ? `${qualityReviewRange?.normalized}번 슬라이드를`
      : currentScopeSelected && selectedSlide !== undefined
        ? `현재 ${selectedSlide + 1}번 슬라이드를`
        : "전체 슬라이드를";

    await commandDispatcher(
      svgReviewOnly ? `${subject} 현재 화면의 품질을 검토해 줘` : `${subject} 고품질 검수하고 문제를 자동 수정해 줘`,
      {
        commandType: "quality.review",
        context: {
          scope: subsetScopeSelected || reviewRequiredSelected
            ? "subset"
            : currentScopeSelected
              ? "current"
              : "all",
          slide_indices: slideIndices,
          mode: svgReviewOnly ? "analyze_only" : "analyze_and_fix",
          ...(!svgReviewOnly && SLIDESPEC_WORKSPACE_REPAIR_ENABLED
            ? { slidespec_opt_in: true }
            : {}),
          ...(svgReviewOnly || selectedSlide === undefined
            ? {}
            : { current_slide: selectedSlide }),
        },
      },
    );
    setQualityReviewPrompt(null);
  }, [
    commandDispatcher,
    qualityReviewPrompt,
    qualityReviewRange,
    qualityReviewScope,
    reviewRequiredIndices,
    svgReviewOnly,
  ]);

  const handleQualityReviewRangeInput = useCallback(
    (value: string) => {
      setQualityReviewRangeInput(value);
      if (!value.trim()) {
        setQualityReviewRange(null);
        setQualityReviewRangeError(null);
        return;
      }
      try {
        const parsed = parseQualityReviewSlideRange(
          value,
          generationInfo?.settings?.n_slides,
        );
        setQualityReviewRange(parsed);
        setQualityReviewRangeError(null);
      } catch (error) {
        setQualityReviewRange(null);
        setQualityReviewRangeError(
          error instanceof Error
            ? error.message
            : "페이지 범위를 확인해 주세요.",
        );
      }
    },
    [generationInfo?.settings?.n_slides],
  );

  useLayoutEffect(() => {
    if (loading) return;

    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    const eventHighWatermarkAdvanced =
      projection.eventHighWatermark !== previousEventHighWatermarkRef.current;

    const prependRestore = prependRestoreRef.current;
    if (prependRestore) {
      if (timelineItems.length > prependRestore.itemCount) {
        const addedHeight =
          scrollElement.scrollHeight - prependRestore.scrollHeight;
        scrollElement.scrollTop = prependRestore.scrollTop + addedHeight;
        prependRestoreRef.current = null;
        shouldFollowLatestRef.current = false;
        setFollowingLatest(false);
      } else if (!loadingOlder) {
        prependRestoreRef.current = null;
      }
      previousItemCountRef.current = timelineItems.length;
      previousEventHighWatermarkRef.current = projection.eventHighWatermark;
      return;
    }

    if (!hasInitialScrollRef.current) {
      const restoredScrollTop = restoreScroll(scrollElement);
      scrollElement.scrollTop = restoredScrollTop === undefined ? scrollElement.scrollHeight
        : Math.min(restoredScrollTop, Math.max(0, scrollElement.scrollHeight - scrollElement.clientHeight));
      hasInitialScrollRef.current = true;
      shouldFollowLatestRef.current = restoredScrollTop === undefined;
      setFollowingLatest(shouldFollowLatestRef.current);
    } else if (
      (timelineItems.length > previousItemCountRef.current ||
        eventHighWatermarkAdvanced) &&
      shouldFollowLatestRef.current
    ) {
      scrollElement.scrollTop = scrollElement.scrollHeight;
    }

    previousItemCountRef.current = timelineItems.length;
    previousEventHighWatermarkRef.current = projection.eventHighWatermark;
  }, [
    loading,
    loadingOlder,
    projection.eventHighWatermark,
    timelineItems.length,
    // Remake history resolves after the first paint and renders above the
    // transcript, so its arrival moves the bottom out from under a reader who
    // was pinned to it.
    pageRemakeHistory.length,
    restoreScroll,
  ]);

  const handleTimelineScroll = useCallback(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    const distanceFromBottom =
      scrollElement.scrollHeight -
      scrollElement.scrollTop -
      scrollElement.clientHeight;
    shouldFollowLatestRef.current = distanceFromBottom <= 48;
    rememberScroll(scrollElement);
    setFollowingLatest(shouldFollowLatestRef.current);
  }, [rememberScroll]);

  const handleTimelineManualScrollIntent = useCallback(() => {
    // A wheel/touch gesture is observable before the browser commits the new
    // scrollTop. Pause follow-latest immediately so an event arriving in that
    // small window cannot snap the reader back to the live operation card.
    shouldFollowLatestRef.current = false;
    setFollowingLatest(false);
  }, []);

  const handleLoadOlder = useCallback(async () => {
    const scrollElement = scrollRef.current;
    if (!scrollElement || loadingOlder) return;

    prependRestoreRef.current = {
      scrollHeight: scrollElement.scrollHeight,
      scrollTop: scrollElement.scrollTop,
      itemCount: timelineItems.length,
    };
    setLoadingOlder(true);
    try {
      await loadOlder();
    } catch {
      prependRestoreRef.current = null;
    } finally {
      setLoadingOlder(false);
    }
  }, [loadOlder, loadingOlder, timelineItems.length]);

  return (
    <section
      className="relative flex h-full w-full min-h-0 min-w-0 flex-col overflow-hidden bg-white"
      aria-label={t("conversation")}
    >
      {(connection !== "live" || showFollowControl) && <header className="mx-auto flex w-full max-w-[800px] items-center justify-end gap-2 px-4 py-2">
        <div className="flex shrink-0 items-center gap-2">
        {connection !== "live" && <span
          className="text-xs text-slate-500"
          role="status"
          aria-live="polite"
        >
          {CONNECTION_LABELS[connection]}
        </span>}
        {showFollowControl && <button
          type="button"
          onClick={agentFocus.toggleFollow}
          aria-pressed={agentFocus.followEnabled}
          data-testid="workspace-follow-agent-toggle"
          title={
            agentFocus.followEnabled
              ? t("followOnTitle")
              : t("followOffTitle")
          }
          className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
            agentFocus.followEnabled
              ? "border-indigo-200 bg-indigo-50 text-indigo-700"
              : "border-slate-200 bg-white text-slate-500"
          }`}
        >
          <LocateFixed className="h-3 w-3" aria-hidden="true" />
          {agentFocus.followEnabled ? t("following") : t("follow")}
        </button>}
        </div>
      </header>}
      <div className="mx-auto w-full max-w-[800px] px-4">
      <WorkspaceAttemptNavigation
        presentationId={presentationId}
        previousPresentationId={generationInfo?.previous_presentation_id}
        nextPresentationIds={generationInfo?.next_presentation_ids}
        generationStatus={generationState.status}
        showRestart={false}
        restartBlockedReason={
          loading || submitting || mutationActive
            ? t("retryAfterSettled")
            : retryIntent?.reason === "ambiguous"
              ? t("checkPriorAdmission")
              : !writesEnabled
                ? t("noWritePermission")
                : null
        }
      />
      </div>

      <div
        ref={scrollRef}
        data-testid="workspace-timeline-scroll"
        data-follow-latest={followingLatest ? "true" : "false"}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-6"
        onTouchStartCapture={handleTimelineManualScrollIntent}
        onWheelCapture={handleTimelineManualScrollIntent}
        onScroll={handleTimelineScroll}
      >
        <div className="mx-auto w-full max-w-[800px]">
        {projection.hasMore && (
          <div className="mb-4 text-center">
            <button
              type="button"
              onClick={() => void handleLoadOlder()}
              disabled={loadingOlder}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            >
              {loadingOlder ? t("loadingOlder") : t("loadOlder")}
            </button>
          </div>
        )}
        {(loading || (generationInfoLoading && timelineItems.length === 0)) && (
          <p className="py-8 text-center text-sm text-slate-500">
            {t("loadingHistory")}
          </p>
        )}
        {!loading &&
          !generationInfoLoading &&
          timelineItems.length === 0 &&
          !qualityReviewPrompt &&
          !pageRemakePrompt &&
          !historyError && (
            <div className="py-8">
              <p className="text-center text-sm text-slate-500">
                {t("noHistory")}
              </p>
            </div>
          )}
        {/* Remake previews carry no conversation position, so they sit above
            the transcript. Rendering them after it put them between the newest
            turn and the composer, which pushed a just-sent message out of
            view. */}
        <PageRemakeHistory
          presentationId={presentationId}
          entries={pageRemakeHistory}
          disabled={submitting || !writesEnabled}
          onWorkspaceCommand={commandDispatcher}
          onApplied={(slideIndex) => {
            setPageRemakeHistoryToken((token) => token + 1);
            void handlePresentationChanged({
              operationId: `page-remake-history-${slideIndex}`,
              operationType: "slide.regenerate",
              fixedSlideIndexes: [slideIndex],
            });
          }}
          onRegenerate={(slideIndex) =>
            setPageRemakePrompt({ slideIndexes: [slideIndex], batch: false })
          }
        />
        <div className="space-y-8">
          {timelineItems.map((item) => {
            if (item.anchorType === "message") {
              return (
                <WorkspaceMessage
                  key={item.id}
                  message={item}
                  onReattach={
                    writesEnabled
                      ? () => fileInputRef.current?.click()
                      : undefined
                  }
                />
              );
            }
            if (item.anchorType === "operation") {
              return (
                <div key={item.id}>
                <WorkspaceTimelineOperation
                  operation={item}
                  composerCancelOperationId={cancelOperation?.operationId}
                  observation={{ ...operationObservations[item.operationId],
                    connection: connection === "live" || connection === "polling" ? "confirmed" : "checking" }}
                  currentRevision={projection.revision}
                  disabled={submitting || !writesEnabled}
                  onAction={(operationId, action, options) =>
                    performAction(operationId, action, options)
                  }
                  onReExport={(operationType) => {
                    const format =
                      operationType === "export.pdf" ? "pdf" : "pptx";
                    return commandDispatcher(
                      `${format.toUpperCase()}로 다시 내보내기`,
                      {
                        commandType: operationType,
                        context: { format },
                      },
                    );
                  }}
                  onFocusSlide={onSlideFocus}
                  // The same prompt the header button opens. A chat request the
                  // tools had to refuse now ends at the flow that can serve it
                  // instead of at prose describing where to find it.
                  onRemakePage={pageRemakeRequester}
                  presentationRefreshState={
                    presentationRefreshStates[item.operationId]
                  }
                />
                {item.operationId === generationState.operationId &&
                  TERMINAL.has(item.status) && item.status !== "succeeded" &&
                  <WorkspaceAttemptNavigation presentationId={presentationId}
                    generationStatus={item.status} restartBlockedReason={restartBlockedReason}
                    showHistory={false} />}
                </div>
              );
            }
            return (
              <article
                key={item.id}
                data-workspace-anchor-id={item.id}
                className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm"
                role="alert"
              >
                {t("unsupportedTimelineItem", { type: item.rawType })}
              </article>
            );
          })}
          {qualityReviewPrompt && (
            <article
              className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm"
              data-testid="quality-review-options"
              aria-label={t("qualityReviewScope")}
              aria-live="polite"
            >
              <div className="flex items-start gap-3">
                <span
                  className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-indigo-50 text-sm text-indigo-600"
                  aria-hidden="true"
                >
                  ✦
                </span>
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold text-slate-900">
                    {t("qualityReviewScope")}
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    {t(svgReviewOnly ? "svgQualityReviewDescription" : "qualityReviewDescription")}
                  </p>
                </div>
              </div>

              <fieldset className="mt-4">
                <legend className="text-xs font-semibold text-slate-700">
                  {t("reviewScope")}
                </legend>
                <div
                  className="mt-2 grid grid-cols-2 gap-2"
                  role="radiogroup"
                  aria-label={t("reviewScope")}
                >
                  <button
                    type="button"
                    role="radio"
                    aria-checked={qualityReviewScope === "review_required"}
                    disabled={reviewRequiredIndices.length === 0}
                    onClick={() => setQualityReviewScope("review_required")}
                    data-testid="quality-review-scope-review-required"
                    className={`rounded-xl border px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      qualityReviewScope === "review_required"
                        ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {t("reviewRequiredOnly", { count: reviewRequiredIndices.length })}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={qualityReviewScope === "all"}
                    onClick={() => setQualityReviewScope("all")}
                    data-testid="quality-review-scope-all"
                    className={`rounded-xl border px-3 py-2 text-left text-xs transition-colors ${
                      qualityReviewScope === "all"
                        ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {t("allSlides")}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={qualityReviewScope === "current"}
                    disabled={qualityReviewPrompt.currentSlide === undefined}
                    onClick={() => setQualityReviewScope("current")}
                    data-testid="quality-review-scope-current"
                    className={`rounded-xl border px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                      qualityReviewScope === "current"
                        ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {t("currentSlide")}
                    {qualityReviewPrompt.currentSlide === undefined
                      ? ""
                      : t("slideNumberSuffix", { number: qualityReviewPrompt.currentSlide + 1 })}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={qualityReviewScope === "subset"}
                    onClick={() => setQualityReviewScope("subset")}
                    data-testid="quality-review-scope-subset"
                    className={`rounded-xl border px-3 py-2 text-left text-xs transition-colors ${
                      qualityReviewScope === "subset"
                        ? "border-indigo-500 bg-indigo-50 font-semibold text-indigo-700"
                        : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {t("specificPages")}
                  </button>
                </div>
              </fieldset>

              {qualityReviewScope === "review_required" && (
                <p
                  className="mt-4 text-xs font-medium text-emerald-700"
                  aria-live="polite"
                  data-testid="quality-review-required-confirmation"
                >
                  {reviewRequiredIndices.length > 0
                    ? t("reviewTargets", { pages: reviewRequiredIndices.map((index) => index + 1).join(", ") })
                    : t("noReviewRequired")}
                </p>
              )}

              {qualityReviewScope === "subset" && (
                <div className="mt-4">
                  <label
                    htmlFor="quality-review-page-range"
                    className="text-xs font-semibold text-slate-700"
                  >
                    {t("pageNumbers")}
                  </label>
                  <p
                    id="quality-review-range-help"
                    className="mt-1 text-[11px] text-slate-500"
                  >
                    {t("rangeHelp")}
                  </p>
                  <input
                    id="quality-review-page-range"
                    value={qualityReviewRangeInput}
                    onChange={(event) =>
                      handleQualityReviewRangeInput(event.target.value)
                    }
                    aria-invalid={Boolean(qualityReviewRangeError)}
                    aria-describedby={
                      qualityReviewRangeError
                        ? "quality-review-range-error"
                        : "quality-review-range-help"
                    }
                    inputMode="text"
                    placeholder={t("rangePlaceholder")}
                    className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  />
                  {qualityReviewRangeError && (
                    <p
                      id="quality-review-range-error"
                      role="alert"
                      className="mt-2 text-xs text-red-600"
                    >
                      {qualityReviewRangeError}
                    </p>
                  )}
                  {qualityReviewRange && (
                    <p
                      role="status"
                      aria-live="polite"
                      data-testid="quality-review-range-confirmation"
                      className="mt-2 text-xs font-medium text-emerald-700"
                    >
                      {t("confirmedTargets", { pages: qualityReviewRange.normalized })}
                    </p>
                  )}
                </div>
              )}

              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setQualityReviewPrompt(null)}
                  data-testid="quality-review-options-cancel"
                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50"
                >
                  {t("cancel")}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    void handleQualityReviewSubmit().catch(() => undefined)
                  }
                  disabled={
                    submitting ||
                    !writesEnabled ||
                    (qualityReviewScope === "subset" && !qualityReviewRange) ||
                    (qualityReviewScope === "review_required" && reviewRequiredIndices.length === 0)
                  }
                  data-testid="quality-review-options-submit"
                  className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t(svgReviewOnly ? "startSvgReview" : "startReview")}
                </button>
              </div>
            </article>
          )}
          <WorkspaceRemakePrompts
            request={pageRemakePrompt}
            presentationId={presentationId}
            disabled={submitting || !writesEnabled}
            onWorkspaceCommand={commandDispatcher}
            onDismiss={() => {
              setPageRemakePrompt(null);
              setPageRemakeHistoryToken((token) => token + 1);
            }}
          />

        </div>
        {historyError && (
          <div
            className="mt-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-700"
            role="alert"
            aria-live="assertive"
            data-testid="workspace-history-error"
          >
            <p>{historyError}</p>
            <button
              type="button"
              onClick={() => void retryLoad()}
              className="mt-2 font-semibold underline"
            >
              {t("reloadHistory")}
            </button>
          </div>
        )}
        {!loading && !historyError && projection.writesSafe === false && (
          <p
            className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
            role="status"
            data-testid="workspace-schema-read-only"
          >
            {t("unknownContractVersion")}
          </p>
        )}
        {requestError && (
          <div
            className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
            role="status"
            aria-live="polite"
            data-testid="workspace-request-error"
          >
            <p>{requestError}</p>
            {retryIntent?.command && (
              <blockquote className="mt-2 rounded-lg border border-red-200 bg-white/70 px-3 py-2 text-xs text-slate-700">
                {retryIntent.command}
              </blockquote>
            )}
            {retryIntent && (
              <button
                type="button"
                onClick={() => void retryPendingIntent().catch(() => undefined)}
                disabled={submitting || !writesEnabled}
                data-testid="workspace-retry-intent"
                className="mt-2 font-semibold underline disabled:cursor-not-allowed disabled:opacity-50"
              >
                {retryIntent.reason === "revision_conflict"
                  ? t("retryLatest")
                  : t("retrySame")}
              </button>
            )}
          </div>
        )}
      </div>
      </div>

      {!followingLatest && <div className="relative h-0 shrink-0"><button type="button"
        onClick={() => {
          shouldFollowLatestRef.current = true;
          setFollowingLatest(true);
          if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }}
        className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-full border border-slate-200 bg-white px-4 py-2 text-sm shadow-sm"
        data-testid="workspace-new-messages">{t("newMessages")}</button></div>}
      {commandsEnabled ? (
      <div className="mx-auto w-full max-w-[824px] shrink-0">
        <WorkspaceConversationComposer
          presentationId={presentationId}
          revision={projection.revision}
          cancelOperation={cancelOperation}
          conversationId={generationInfo?.conversation_id}
          presentation={presentation}
          currentSlide={currentSlide}
          currentSlideId={currentSlideId}
          generationPending={generationPending}
          mutationActive={mutationActive}
          loading={loading || generationInfoLoading}
          submitting={submitting}
          historyError={historyError}
          writesEnabled={writesEnabled && retryIntent?.reason !== "ambiguous"}
          fileInputRef={fileInputRef}
          commandDispatcher={commandDispatcher}
          onWillSend={() => {
            shouldFollowLatestRef.current = true;
            setFollowingLatest(true);
          }}
          onEditCurrentSlideReady={onEditCurrentSlideReady}
          showSuggestions={
            !loading &&
            !generationInfoLoading &&
            timelineItems.length === 0 &&
            !qualityReviewPrompt &&
            !pageRemakePrompt &&
            !historyError
          }
        />
        </div>
      ) : null}
    </section>
  );
}
