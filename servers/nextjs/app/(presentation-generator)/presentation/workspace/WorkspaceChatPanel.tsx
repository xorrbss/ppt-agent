"use client";

import { LocateFixed } from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { WorkspaceComposer } from "./WorkspaceComposer";
import { WorkspaceMessage } from "./WorkspaceMessage";
import { WorkspaceSuggestions } from "./WorkspaceSuggestions";
import { WorkspaceOperationCard } from "./WorkspaceOperationCard";
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
  WorkspaceQualityReviewRequester,
} from "./types";
import {
  postWorkspaceAsset,
  type WorkspaceUploadedAsset,
} from "../../services/api/workspace";
import {
  DashboardApi,
  type PresentationGenerationInfo,
} from "../../services/api/dashboard";
import { withGenerationContext } from "./generationContext";
import { buildConversationContext } from "../conversation/context";
import { WorkspacePageRemakePrompt } from "../components/AuthoredPageRemake";
import {
  PageRemakeHistory,
  usePageRemakeHistory,
} from "../components/PageRemakeHistory";
import {
  CONNECTION_LABELS,
  parseQualityReviewSlideRange,
  type QualityReviewScope,
  type SlideRangeParseResult,
} from "./workspaceChatPanelUtils";

const SLIDESPEC_WORKSPACE_REPAIR_ENABLED =
  process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_CANARY_UI !== "false" &&
  process.env.NEXT_PUBLIC_SLIDESPEC_REPAIR_ALL_SLIDES !== "false";

interface WorkspaceChatPanelProps {
  presentationId: string;
  currentSlide?: number;
  commandsEnabled?: boolean;
  onPresentationChanged?: WorkspacePresentationChangedHandler;
  onSlideFocus?: (slideIndex: number) => void;
  /** True while the runner still owns the deck, for canvas activity affordances. */
  onAgentActivityChange?: (active: boolean) => void;
  /** Emitted only while "AI 따라가기" is on, so the canvas may move itself. */
  onAgentSlideFocus?: (focus: WorkspaceAgentSlideFocus) => void;
  onCommandDispatcherReady?: (
    dispatcher: WorkspaceCommandDispatcher | null,
  ) => void;
  onQualityReviewRequesterReady?: (
    requester: WorkspaceQualityReviewRequester | null,
  ) => void;
  onPageRemakeRequesterReady?: (
    requester: WorkspacePageRemakeRequester | null,
  ) => void;
}

export default function WorkspaceChatPanel({
  presentationId,
  currentSlide,
  commandsEnabled = true,
  onPresentationChanged,
  onSlideFocus,
  onAgentActivityChange,
  onAgentSlideFocus,
  onCommandDispatcherReady,
  onQualityReviewRequesterReady,
  onPageRemakeRequesterReady,
}: WorkspaceChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState("");
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [qualityReviewPrompt, setQualityReviewPrompt] = useState<{
    currentSlide?: number;
  } | null>(null);
  const [pageRemakePrompt, setPageRemakePrompt] = useState<{
    currentSlide: number;
  } | null>(null);
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
  const [generationInfoResult, setGenerationInfoResult] = useState<{
    presentationId: string;
    info: PresentationGenerationInfo | null;
  } | null>(null);
  const [pendingAssets, setPendingAssets] = useState<WorkspaceUploadedAsset[]>(
    [],
  );
  const hasInitialScrollRef = useRef(false);
  const shouldFollowLatestRef = useRef(true);
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

  useEffect(() => {
    let active = true;

    void DashboardApi.getPresentationGenerationInfo(presentationId)
      .then((info) => {
        if (active) setGenerationInfoResult({ presentationId, info });
      })
      .catch(() => {
        if (active) setGenerationInfoResult({ presentationId, info: null });
      });

    return () => {
      active = false;
    };
  }, [presentationId]);

  const generationInfo =
    generationInfoResult?.presentationId === presentationId
      ? generationInfoResult.info
      : null;
  const generationInfoLoading =
    generationInfoResult?.presentationId !== presentationId;

  const timelineItems = useMemo(
    () =>
      withGenerationContext(presentationId, generationInfo, projection.items),
    [generationInfo, presentationId, projection.items],
  );

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
    onCommandDispatcherReady?.(commandDispatcher);
    return () => onCommandDispatcherReady?.(null);
  }, [commandDispatcher, onCommandDispatcherReady]);

  const qualityReviewRequester = useCallback<WorkspaceQualityReviewRequester>(
    (requestedSlide) => {
      setPageRemakePrompt(null);
      setQualityReviewScope("all");
      setQualityReviewRangeInput("");
      setQualityReviewRange(null);
      setQualityReviewRangeError(null);
      setQualityReviewPrompt(
        requestedSlide === undefined ? {} : { currentSlide: requestedSlide },
      );
      shouldFollowLatestRef.current = true;
    },
    [],
  );

  useEffect(() => {
    onQualityReviewRequesterReady?.(qualityReviewRequester);
    return () => onQualityReviewRequesterReady?.(null);
  }, [onQualityReviewRequesterReady, qualityReviewRequester]);

  const pageRemakeRequester = useCallback<WorkspacePageRemakeRequester>(
    (requestedSlide) => {
      setQualityReviewPrompt(null);
      setPageRemakePrompt({ currentSlide: requestedSlide });
      shouldFollowLatestRef.current = true;
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
    previousItemCountRef.current = 0;
    previousEventHighWatermarkRef.current = undefined;
    prependRestoreRef.current = null;
    setLoadingOlder(false);
    setDraft("");
    setPendingAssets([]);
    setUploadError(null);
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
    const subsetScopeSelected = qualityReviewScope === "subset";
    if (subsetScopeSelected && !qualityReviewRange) {
      setQualityReviewRangeError(
        "페이지 범위를 확인한 뒤 검수를 시작해 주세요.",
      );
      return;
    }
    const slideIndices = subsetScopeSelected
      ? (qualityReviewRange?.indices ?? [])
      : currentScopeSelected && selectedSlide !== undefined
        ? [selectedSlide]
        : [];
    const subject = subsetScopeSelected
      ? `${qualityReviewRange?.normalized}번 슬라이드를`
      : currentScopeSelected && selectedSlide !== undefined
        ? `현재 ${selectedSlide + 1}번 슬라이드를`
        : "전체 슬라이드를";

    await commandDispatcher(
      `${subject} 고품질 검수하고 문제를 자동 수정해 줘`,
      {
        commandType: "quality.review",
        context: {
          scope: subsetScopeSelected
            ? "subset"
            : currentScopeSelected
              ? "current"
              : "all",
          slide_indices: slideIndices,
          mode: "analyze_and_fix",
          ...(SLIDESPEC_WORKSPACE_REPAIR_ENABLED
            ? { slidespec_opt_in: true }
            : {}),
          ...(selectedSlide === undefined
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

  const handleFilesSelected = useCallback(
    async (files: File[]) => {
      setUploading(true);
      setUploadError(null);
      try {
        for (const file of files) {
          const uploaded = await postWorkspaceAsset(file, presentationId);
          setPendingAssets((current) => [
            ...current.filter((asset) => asset.assetId !== uploaded.assetId),
            uploaded,
          ]);
        }
      } catch (error: unknown) {
        setUploadError(
          error instanceof Error
            ? error.message
            : "파일을 업로드하지 못했습니다.",
        );
      } finally {
        setUploading(false);
      }
    },
    [presentationId],
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
      } else if (!loadingOlder) {
        prependRestoreRef.current = null;
      }
      previousItemCountRef.current = timelineItems.length;
      previousEventHighWatermarkRef.current = projection.eventHighWatermark;
      return;
    }

    if (!hasInitialScrollRef.current) {
      scrollElement.scrollTop = scrollElement.scrollHeight;
      hasInitialScrollRef.current = true;
      shouldFollowLatestRef.current = true;
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
  ]);

  const handleTimelineScroll = useCallback(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) return;
    const distanceFromBottom =
      scrollElement.scrollHeight -
      scrollElement.scrollTop -
      scrollElement.clientHeight;
    shouldFollowLatestRef.current = distanceFromBottom <= 48;
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
      className="flex h-full w-full min-h-0 min-w-0 flex-col bg-slate-50"
      aria-label="프레젠테이션 작업 대화"
    >
      <header className="flex items-center justify-between gap-2 border-b border-slate-200 bg-white px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-slate-900">
            Slides Workbench
          </h2>
          <p className="text-xs text-slate-500">생성 및 수정 내역</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
        <span
          className="text-xs text-slate-500"
          role="status"
          aria-live="polite"
        >
          {CONNECTION_LABELS[connection]}
        </span>
        <button
          type="button"
          onClick={agentFocus.toggleFollow}
          aria-pressed={agentFocus.followEnabled}
          data-testid="workspace-follow-agent-toggle"
          title={
            agentFocus.followEnabled
              ? "AI 따라가기 켜짐: 요청한 페이지로 캔버스가 이동합니다"
              : "AI 따라가기 꺼짐"
          }
          className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors ${
            agentFocus.followEnabled
              ? "border-indigo-200 bg-indigo-50 text-indigo-700"
              : "border-slate-200 bg-white text-slate-500"
          }`}
        >
          <LocateFixed className="h-3 w-3" aria-hidden="true" />
          {agentFocus.followEnabled ? "따라가는 중" : "AI 따라가기"}
        </button>
        </div>
      </header>

      <div
        ref={scrollRef}
        data-testid="workspace-timeline-scroll"
        className="min-h-0 flex-1 overflow-y-auto px-3 py-4"
        onScroll={handleTimelineScroll}
      >
        {projection.hasMore && (
          <div className="mb-4 text-center">
            <button
              type="button"
              onClick={() => void handleLoadOlder()}
              disabled={loadingOlder}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
            >
              {loadingOlder ? "이전 기록 불러오는 중" : "이전 기록 불러오기"}
            </button>
          </div>
        )}
        {(loading || (generationInfoLoading && timelineItems.length === 0)) && (
          <p className="py-8 text-center text-sm text-slate-500">
            대화 기록을 불러오는 중입니다.
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
                아직 기록된 대화가 없습니다.
              </p>
              {commandsEnabled && (
                <WorkspaceSuggestions
                  disabled={submitting || !writesEnabled}
                  onSelect={(prompt) => {
                    setDraft(prompt);
                    composerRef.current?.focus();
                  }}
                />
              )}
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
            setPageRemakePrompt({ currentSlide: slideIndex })
          }
        />
        <div className="space-y-3">
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
                <WorkspaceOperationCard
                  key={item.id}
                  operation={item}
                  currentRevision={projection.revision}
                  disabled={submitting || !writesEnabled}
                  onAction={(operationId, action) =>
                    performAction(operationId, action)
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
              );
            }
            return (
              <article
                key={item.id}
                className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm"
                role="alert"
              >
                지원되지 않는 타임라인 항목({item.rawType})입니다. 기록은
                보존되어 있습니다.
              </article>
            );
          })}
          {qualityReviewPrompt && (
            <article
              className="rounded-2xl border border-indigo-200 bg-white p-4 shadow-sm"
              data-testid="quality-review-options"
              aria-label="고품질 검수 범위"
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
                    고품질 검수 범위
                  </h3>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    검토 후 문제를 자동으로 수정합니다. 검수할 페이지 범위를
                    선택해 주세요.
                  </p>
                </div>
              </div>

              <fieldset className="mt-4">
                <legend className="text-xs font-semibold text-slate-700">
                  검수 범위
                </legend>
                <div
                  className="mt-2 grid grid-cols-3 gap-2"
                  role="radiogroup"
                  aria-label="검수 범위"
                >
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
                    전체 슬라이드
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
                    현재 슬라이드
                    {qualityReviewPrompt.currentSlide === undefined
                      ? ""
                      : ` (${qualityReviewPrompt.currentSlide + 1}번)`}
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
                    페이지 지정
                  </button>
                </div>
              </fieldset>

              {qualityReviewScope === "subset" && (
                <div className="mt-4">
                  <label
                    htmlFor="quality-review-page-range"
                    className="text-xs font-semibold text-slate-700"
                  >
                    페이지 번호
                  </label>
                  <p
                    id="quality-review-range-help"
                    className="mt-1 text-[11px] text-slate-500"
                  >
                    쉼표와 범위를 사용해 입력하세요. 예: 3, 5-8, 12
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
                    placeholder="예: 3, 5-8, 12"
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
                      검수 대상 확인: {qualityReviewRange.normalized}번 페이지
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
                  취소
                </button>
                <button
                  type="button"
                  onClick={() =>
                    void handleQualityReviewSubmit().catch(() => undefined)
                  }
                  disabled={
                    submitting ||
                    !writesEnabled ||
                    (qualityReviewScope === "subset" && !qualityReviewRange)
                  }
                  data-testid="quality-review-options-submit"
                  className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  검수 및 자동 수정 시작
                </button>
              </div>
            </article>
          )}
          {pageRemakePrompt && (
            <WorkspacePageRemakePrompt
              presentationId={presentationId}
              currentSlide={pageRemakePrompt.currentSlide}
              disabled={submitting || !writesEnabled}
              onWorkspaceCommand={commandDispatcher}
              onDismiss={() => {
                setPageRemakePrompt(null);
                setPageRemakeHistoryToken((token) => token + 1);
              }}
            />
          )}
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
              기록 다시 불러오기
            </button>
          </div>
        )}
        {!loading && !historyError && projection.writesSafe === false && (
          <p
            className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
            role="status"
            data-testid="workspace-schema-read-only"
          >
            이 기록의 계약 버전을 확인할 수 없어 새 요청과 작업 변경을 잠시
            사용할 수 없습니다. 기존 기록과 다운로드는 계속 확인할 수 있습니다.
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
                  ? "최신 버전 기준으로 다시 시도"
                  : "같은 요청으로 다시 확인"}
              </button>
            )}
          </div>
        )}
      </div>

      {commandsEnabled ? (
        <WorkspaceComposer
          disabled={
            submitting || loading || historyError !== null || !writesEnabled
          }
          uploading={uploading}
          uploadError={uploadError}
          assets={pendingAssets}
          fileInputRef={fileInputRef}
          value={draft}
          onValueChange={setDraft}
          textareaRef={composerRef}
          onFilesSelected={handleFilesSelected}
          onRemoveAsset={(assetId) => {
            setPendingAssets((current) =>
              current.filter((asset) => asset.assetId !== assetId),
            );
          }}
          onSend={async (command) => {
            // Follow-latest switches off as soon as the reader scrolls up, but
            // a turn the user just sent must always come into view - otherwise
            // their message and its progress card land below the fold and the
            // send looks like it did nothing.
            shouldFollowLatestRef.current = true;
            await commandDispatcher(command, {
              // Keep the legacy adapter compatible while making context rich
              // enough for the canonical Conversation API. The server derives
              // mode/capabilities and admits only ready/scanned assets.
              context: { ...buildConversationContext({
                currentSlideIndex: currentSlide,
                assets: pendingAssets.map((asset) => ({
                  id: asset.assetId,
                  name: asset.fileName,
                  status: asset.status,
                })),
              }) },
              assetIds: pendingAssets.map((asset) => asset.assetId),
            });
            setPendingAssets([]);
          }}
        />
      ) : null}
    </section>
  );
}
