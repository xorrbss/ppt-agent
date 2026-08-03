"use client";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import "../../utils/prism-languages";
import { Skeleton } from "@/components/ui/skeleton";
import PresentationMode from "./PresentationMode";
import SidePanel from "./SidePanel";
import SlideContent from "./SlideContent";
import { Button } from "@/components/ui/button";
import { usePathname, useRouter } from "next/navigation";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { AlertCircle, Layers } from "lucide-react";
import {
  usePresentationStreaming,
  usePresentationData,
  usePresentationNavigation,
  useAutoSave,
  useHybridExportWarmup,
} from "../hooks";
import { PresentationPageProps } from "../types";
import LoadingState from "./LoadingState";
import { applyPresentationThemeToElement } from "../utils/applyPresentationThemeDom";
import { isAuthoredPresentation } from "../utils/isAuthoredPresentation";

import PresentationHeader from "./PresentationHeader";
import Chat from "./Chat";
import {
  WorkspaceChatPanel,
  type WorkspaceCommandDispatcher,
  type WorkspacePresentationChange,
  type WorkspaceQualityReviewRequester,
} from "../workspace";
import { useChatFirstWorkbenchRollout } from "@/lib/use-chat-first-workbench-rollout";
import { canReadChatFirstWorkspace } from "@/lib/chat-first-workbench";

const PresentationPage: React.FC<PresentationPageProps> = ({
  presentation_id,
}) => {
  const pathname = usePathname();
  // State management
  const [loading, setLoading] = useState(true);
  const [selectedSlide, setSelectedSlide] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isChatSending, setIsChatSending] = useState(false);
  const [isFollowModeEnabled, setIsFollowModeEnabled] = useState(true);
  const [agentFocusedSlide, setAgentFocusedSlide] = useState<number | null>(null);
  const [agentFocusEventId, setAgentFocusEventId] = useState<string | null>(null);
  const [glowingSlideIndex, setGlowingSlideIndex] = useState<number | null>(null);
  const [chatTargetedSlides, setChatTargetedSlides] = useState<number[]>([]);
  const [error, setError] = useState(false);
  const [compactWorkspacePane, setCompactWorkspacePane] = useState<
    "slides" | "chat"
  >("chat");
  const [workspaceDrawerOpen, setWorkspaceDrawerOpen] = useState(true);
  const [legacyWorkspacePane, setLegacyWorkspacePane] = useState<
    "chat" | "history"
  >("chat");
  const slidesScrollContainerRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();
  const chatFirstRollout = useChatFirstWorkbenchRollout(undefined, presentation_id);
  const chatFirstEnabled = chatFirstRollout.mode === "chat";
  const workspaceShellEnabled = chatFirstRollout.mode !== "legacy";
  const legacyMutationsEnabled = chatFirstRollout.mode === "legacy";
  const workspaceCapability = chatFirstRollout.capability;
  const legacyWorkspaceHistoryEnabled =
    chatFirstRollout.mode === "legacy" &&
    canReadChatFirstWorkspace(workspaceCapability);
  const [workspaceCommandDispatcher, setWorkspaceCommandDispatcher] =
    useState<WorkspaceCommandDispatcher | null>(null);
  const [workspaceQualityReviewRequester, setWorkspaceQualityReviewRequester] =
    useState<WorkspaceQualityReviewRequester | null>(null);
  const handleWorkspaceCommandDispatcherReady = useCallback(
    (dispatcher: WorkspaceCommandDispatcher | null) => {
      setWorkspaceCommandDispatcher(() => dispatcher);
    },
    [],
  );

  useEffect(() => {
    if (chatFirstRollout.mode !== "legacy") setLegacyWorkspacePane("chat");
  }, [chatFirstRollout.mode]);



  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  // Current decks use mode/theme; legacy saved decks are detected from their
  // authored slide sentinels.
  const isAuthoredDeck = isAuthoredPresentation(presentationData as any);
  const slidesLength = presentationData?.slides?.length ?? 0;
  const lastStreamingSlideIndex =
    slidesLength > 0
      ? presentationData?.slides?.[slidesLength - 1]?.index
      : undefined;

  const scheduleHybridExportWarmup = useHybridExportWarmup({
    presentationId: presentation_id,
    title: presentationData?.title,
    presentationData,
    isAuthoredDeck,
    isStreaming,
  });

  // Auto-save functionality
  const { isSaving, saveFailed } = useAutoSave({
    debounceMs: 2000,
    enabled: legacyMutationsEnabled && !!presentationData && !isStreaming,
    onSaveSuccess: scheduleHybridExportWarmup,
  });

  // Custom hooks
  const { fetchUserSlides } = usePresentationData(
    presentation_id,
    setLoading,
    setError
  );

  const {
    isPresentMode,
    stream,
    currentSlide: presentSlideFromUrl,
    handleSlideClick,
    toggleFullscreen,
    handlePresentExit,
    handleSlideChange,
  } = usePresentationNavigation(
    presentation_id,
    selectedSlide,
    setSelectedSlide,
    setIsFullscreen
  );

  // Initialize streaming
  usePresentationStreaming(
    presentation_id,
    stream,
    setLoading,
    setError,
    fetchUserSlides
  );

  useEffect(() => {
    if (!isStreaming) return;

    const scrollContainer = slidesScrollContainerRef.current;
    if (!scrollContainer) return;

    const frame = window.requestAnimationFrame(() => {
      if (slidesLength <= 1) {
        scrollContainer.scrollTo({ top: 0, behavior: "auto" });
        return;
      }

      if (lastStreamingSlideIndex === undefined) return;

      const slideElement = document.getElementById(
        `slide-${lastStreamingSlideIndex}`
      );
      if (!slideElement) return;

      const containerRect = scrollContainer.getBoundingClientRect();
      const slideRect = slideElement.getBoundingClientRect();
      const slideTop =
        slideRect.top - containerRect.top + scrollContainer.scrollTop;

      scrollContainer.scrollTo({
        top: Math.max(slideTop, 0),
        behavior: "smooth",
      });
    });

    return () => window.cancelAnimationFrame(frame);
  }, [isStreaming, lastStreamingSlideIndex, slidesLength]);

  useEffect(() => {
    trackEvent(MixpanelEvent.Presentation_Editor_Viewed, {
      pathname,
      presentation_id,
      stream_mode: !!stream,
      presentation_mode: isPresentMode ? "present" : "edit",
    });
  }, [pathname, presentation_id, stream, isPresentMode]);

  /** Editor tree unmounts in present mode; remount loses inline theme CSS — re-apply from Redux. */
  useLayoutEffect(() => {
    if (isPresentMode) return;
    const theme = presentationData?.theme;
    if (!theme) return;
    const el = document.getElementById("presentation-slides-wrapper");
    applyPresentationThemeToElement(el, theme);
  }, [isPresentMode, presentationData?.theme]);

  const onSlideChange = (newSlide: number) => {
    handleSlideChange(newSlide, presentationData);
  };

  const handlePresentationChanged = useCallback(async (
    change?: WorkspacePresentationChange,
  ) => {
    const refreshed = await fetchUserSlides({ clearHistory: false });
    if (!refreshed) {
      throw new Error("The committed presentation revision could not be loaded.");
    }
    const slideCount = refreshed?.slides?.length ?? 0;
    if (
      slideCount === 0 ||
      (selectedSlide >= 0 && selectedSlide < slideCount)
    ) {
      return;
    }

    // A revision can remove/reorder slides. Keep the user's selection whenever
    // it remains valid; only then fall back to the first slide that was changed.
    const fallbackIndex = change?.fixedSlideIndexes?.find(
      (index) => index >= 0 && index < slideCount,
    ) ?? 0;
    window.requestAnimationFrame(() => handleSlideClick(fallbackIndex));
  }, [fetchUserSlides, handleSlideClick, selectedSlide]);

  const handleChatSendingStateChange = useCallback((sending: boolean) => {
    setIsChatSending(sending);
    if (sending) {
      setChatTargetedSlides((previous) => (previous.length === 0 ? previous : []));
      return;
    }
    setAgentFocusedSlide(null);
    setAgentFocusEventId(null);
  }, []);

  const handleAgentSlideFocus = useCallback(
    ({ slideIndex, eventId }: { slideIndex: number; eventId: string }) => {
      if (slideIndex < 0) {
        return;
      }
      setAgentFocusedSlide(slideIndex);
      setAgentFocusEventId(eventId);
      setChatTargetedSlides((previous) =>
        previous.includes(slideIndex) ? previous : [...previous, slideIndex]
      );
    },
    []
  );

  const totalSlides = presentationData?.slides?.length ?? 0;
  const highlightedSlideIndex = glowingSlideIndex;
  const targetedSlidesSet = useMemo(
    () => new Set(chatTargetedSlides),
    [chatTargetedSlides]
  );

  useEffect(() => {
    if (!isFollowModeEnabled || !isChatSending || totalSlides <= 0) {
      return;
    }
    if (agentFocusedSlide === null) {
      return;
    }

    const clampedIndex = Math.min(Math.max(agentFocusedSlide, 0), totalSlides - 1);
    if (clampedIndex !== selectedSlide) {
      handleSlideClick(clampedIndex);
    }
  }, [
    isFollowModeEnabled,
    isChatSending,
    totalSlides,
    agentFocusedSlide,
    agentFocusEventId,
    selectedSlide,
    handleSlideClick,
  ]);

  useEffect(() => {
    if (totalSlides <= 0) {
      setGlowingSlideIndex(null);
      setChatTargetedSlides([]);
      return;
    }

    if (!isChatSending) {
      if (glowingSlideIndex === null && chatTargetedSlides.length === 0) {
        return;
      }
      const clearTimer = window.setTimeout(() => {
        setGlowingSlideIndex(null);
        setChatTargetedSlides([]);
      }, 900);
      return () => window.clearTimeout(clearTimer);
    }

    // Do not show glow/scanner until chat traces identify an actual target slide.
    // This avoids the "instant scanner on send" effect before tools start editing.
    if (agentFocusedSlide === null) {
      if (glowingSlideIndex !== null) {
        setGlowingSlideIndex(null);
      }
      return;
    }

    const targetIndex = Math.min(Math.max(agentFocusedSlide, 0), totalSlides - 1);
    setGlowingSlideIndex(targetIndex);
  }, [
    isChatSending,
    totalSlides,
    selectedSlide,
    isFollowModeEnabled,
    agentFocusedSlide,
    chatTargetedSlides.length,
    glowingSlideIndex,
  ]);


  // Presentation Mode View
  if (isPresentMode) {
    return (
      <PresentationMode
        slides={presentationData?.slides!}
        currentSlide={presentSlideFromUrl}
        theme={presentationData?.theme ?? undefined}
        isFullscreen={isFullscreen}
        onFullscreenToggle={toggleFullscreen}
        onExit={handlePresentExit}
        onSlideChange={onSlideChange}
      />
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-100 font-syne">
        <div
          className="bg-white border border-red-300 text-red-700 px-6 py-8 rounded-lg shadow-lg flex flex-col items-center"
          role="alert"
        >
          <AlertCircle className="w-16 h-16 mb-4 text-red-500" />
          <h2 className="text-xl font-semibold mb-2">문제가 발생했습니다</h2>
          <p className="text-center mb-4">
            발표자료를 불러오지 못했습니다. 다시 시도해 주세요.
          </p>
          <div className="flex gap-2 justify-center items-center">

            <Button onClick={() => { trackEvent(MixpanelEvent.PresentationPage_Refresh_Page_Button_Clicked, { pathname }); window.location.reload(); }}>페이지 새로고침</Button>
            <Button onClick={() => { trackEvent(MixpanelEvent.Navigation, { from: pathname, to: "/upload" }); router.push("/upload"); }}>업로드로 이동</Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <main
      className="h-screen w-full max-w-full overflow-clip font-syne"
      aria-labelledby="presentation-editor-title"
    >
      <h1 id="presentation-editor-title" className="sr-only">
        {presentationData?.title ?? "프레젠테이션 편집기"}
      </h1>
      <div
        style={{
          background: "#EDEEEF",
        }}
        id="presentation-slides-wrapper"
        className="relative flex h-full w-full min-w-0 max-w-full flex-col overflow-clip"
      >
        <PresentationHeader
          presentation_id={presentation_id}
          isPresentationSaving={isSaving}
          isPresentationSaveFailed={saveFailed}
          currentSlide={selectedSlide}
          isAuthoredDeck={isAuthoredDeck}
          onReload={() => fetchUserSlides({ clearHistory: true })}
          mutationsEnabled={legacyMutationsEnabled}
          chatFirstEnabled={chatFirstEnabled}
          workspaceCommandsEnabled={
            chatFirstEnabled &&
            workspaceCapability.commands &&
            workspaceCommandDispatcher !== null
          }
          onWorkspaceCommand={workspaceCommandDispatcher ?? undefined}
          onWorkspaceQualityReviewRequest={
            workspaceQualityReviewRequester ?? undefined
          }
        />
        {isAuthoredDeck && (
          <div className="shrink-0 bg-[#EFF4FF] border-y border-[#C7D7FE] px-6 py-2 text-center text-xs text-[#2D4E9A] font-medium">
            이 발표자료는 <span className="font-bold">AI 저작(고품질)</span> 모드입니다 — 인앱은 보기 전용입니다. 내보낸 PPTX를 PowerPoint에서 편집하세요.
          </div>
        )}
        {workspaceShellEnabled && (
          <div className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-white px-3 py-2 xl:hidden">
            <div
              className="flex min-w-0 flex-1 gap-1 lg:hidden"
              role="tablist"
              aria-label="작업 영역"
            >
              <button
                type="button"
                id="compact-slides-tab"
                role="tab"
                aria-selected={compactWorkspacePane === "slides"}
                aria-controls="presentation-slides-pane"
                data-testid="responsive-slides-tab"
                onClick={() => setCompactWorkspacePane("slides")}
                className={`min-w-0 flex-1 rounded-md px-3 py-2 text-sm font-medium ${
                  compactWorkspacePane === "slides"
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                슬라이드
              </button>
              <button
                type="button"
                id="compact-chat-tab"
                role="tab"
                aria-selected={compactWorkspacePane === "chat"}
                aria-controls="workspace-chat-pane"
                data-testid="responsive-chat-tab"
                onClick={() => setCompactWorkspacePane("chat")}
                className={`min-w-0 flex-1 rounded-md px-3 py-2 text-sm font-medium ${
                  compactWorkspacePane === "chat"
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                작업 대화
              </button>
            </div>
            <button
              type="button"
              aria-expanded={workspaceDrawerOpen}
              aria-controls="workspace-chat-pane"
              data-testid="workspace-drawer-toggle"
              onClick={() => setWorkspaceDrawerOpen((isOpen) => !isOpen)}
              className="ml-auto hidden rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 lg:inline-flex xl:hidden"
            >
              {workspaceDrawerOpen ? "작업 대화 닫기" : "작업 대화 열기"}
            </button>
          </div>
        )}
        <div className="relative flex flex-1 min-h-0 gap-6 overflow-hidden">
          <div
            id="presentation-slides-pane"
            role={workspaceShellEnabled ? "tabpanel" : undefined}
            aria-labelledby={workspaceShellEnabled ? "compact-slides-tab" : undefined}
            className={`min-w-0 flex-1 gap-6 ${
              workspaceShellEnabled
                ? compactWorkspacePane === "slides"
                  ? "flex"
                  : "hidden lg:flex"
                : "flex"
            }`}
          >
          <div className="hidden md:block w-[120px] h-full shrink-0 self-start sticky top-0 pt-[18px]">
            <SidePanel
              selectedSlide={selectedSlide}
              onSlideClick={handleSlideClick}
              presentationId={presentation_id}
              loading={loading}
              mutationsEnabled={legacyMutationsEnabled}
            />
          </div>
          <div className="w-full min-w-0 h-full flex-1 pt-[18px]">
            <div
              ref={slidesScrollContainerRef}
              className="font-inter h-full overflow-y-auto hide-scrollbar scroll-pt-[18px]"
            >
              <div className="w-full max-w-[1280px] min-h-full mx-auto flex flex-col items-center pb-8">
                {!presentationData ||
                  loading ||
                  !presentationData?.slides ||
                  (presentationData.slides.length === 0 && stream) ? (
                  <div className="relative w-full h-[calc(100vh-120px)] mx-auto hide-scrollbar">
                    <div className="">
                      {Array.from({ length: 2 }).map((_, index) => (
                        <Skeleton
                          key={index}
                          className="aspect-video bg-gray-400 my-4 w-full mx-auto "
                        />
                      ))}
                    </div>
                    {stream && <LoadingState />}
                  </div>
                ) : presentationData.slides.length === 0 ? (
                  // Loaded, not streaming, but the deck has 0 slides. Previously
                  // this fell into the skeleton branch and showed permanent gray
                  // bars with no message — a dead-end. Show a recoverable empty
                  // state instead (the header's 재생성/regenerate button stays
                  // available above this area).
                  <div className="flex w-full h-[calc(100vh-160px)] flex-col items-center justify-center text-center">
                    <div className="flex max-w-md flex-col items-center rounded-lg border border-gray-200 bg-white px-6 py-8 shadow-sm">
                      <Layers className="mb-4 h-14 w-14 text-gray-400" />
                      <h2 className="mb-2 text-lg font-semibold text-gray-800">
                        슬라이드가 없습니다
                      </h2>
                      <p className="mb-5 text-sm text-gray-500">
                        이 발표자료에는 아직 생성된 슬라이드가 없습니다. 다시
                        불러오거나 상단의 재생성 버튼으로 슬라이드를 만들 수
                        있습니다.
                      </p>
                      <div className="flex items-center justify-center gap-2">
                        <Button
                          onClick={() =>
                            fetchUserSlides({ clearHistory: false })
                          }
                        >
                          다시 불러오기
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => {
                            trackEvent(MixpanelEvent.Navigation, {
                              from: pathname,
                              to: "/dashboard",
                            });
                            router.push("/dashboard");
                          }}
                        >
                          대시보드로 이동
                        </Button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    {presentationData &&
                      presentationData.slides &&
                      presentationData.slides.length > 0 &&
                      presentationData.slides.map((slide: any, index: number) => (
                        <SlideContent
                          key={`${slide.type}-${index}-${slide.index}`}
                          slide={slide}
                          index={index}
                          presentationId={presentation_id}
                          mutationsEnabled={legacyMutationsEnabled}
                          isChatEditing={
                            highlightedSlideIndex !== null &&
                            index === highlightedSlideIndex
                          }
                          isChatTargeted={
                            isChatSending &&
                            highlightedSlideIndex !== index &&
                            targetedSlidesSet.has(index)
                          }
                        />
                      ))}
                  </>
                )}
              </div>
            </div>
          </div>
          </div>
          <div
            id="workspace-chat-pane"
            role={workspaceShellEnabled ? "tabpanel" : undefined}
            aria-labelledby={workspaceShellEnabled ? "compact-chat-tab" : undefined}
            data-testid="workspace-chat-pane"
            className={`h-full min-w-0 overflow-hidden ${
              workspaceShellEnabled
                ? `${
                    compactWorkspacePane === "chat" ? "flex" : "hidden"
                  } ${workspaceDrawerOpen ? "lg:flex" : "lg:hidden"} xl:flex flex-1 lg:absolute lg:inset-y-0 lg:right-0 lg:z-40 lg:w-[min(440px,100%)] lg:max-w-[440px] lg:flex-none lg:shadow-2xl xl:sticky xl:top-0 xl:z-auto xl:w-full xl:max-w-[clamp(360px,30vw,560px)] xl:shrink-0 xl:self-start xl:shadow-none`
                : "w-full max-w-[370px] shrink-0 self-start sticky top-0"
            }`}
          >
            {chatFirstEnabled ? (
              <WorkspaceChatPanel
                presentationId={presentation_id}
                currentSlide={selectedSlide}
                commandsEnabled={workspaceCapability.commands}
                onPresentationChanged={handlePresentationChanged}
                onSlideFocus={handleSlideClick}
                onCommandDispatcherReady={handleWorkspaceCommandDispatcherReady}
                onQualityReviewRequesterReady={(requester) =>
                  setWorkspaceQualityReviewRequester(() => requester)
                }
              />
            ) : chatFirstRollout.mode === "checking" ? (
              <div
                className="flex h-full items-center justify-center border-l border-slate-200 bg-white text-sm text-slate-500"
                role="status"
                aria-live="polite"
              >
                작업 대화를 연결하는 중입니다.
              </div>
            ) : chatFirstRollout.mode === "blocked" ? (
              <div
                className="flex h-full flex-col items-center justify-center gap-3 border-l border-slate-200 bg-white px-6 text-center"
                role="alert"
              >
                <AlertCircle className="h-5 w-5 text-amber-600" aria-hidden="true" />
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    작업 대화를 연결할 수 없습니다.
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    안전을 위해 이전 채팅의 수정 기능으로 전환하지 않았습니다.
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void chatFirstRollout.refresh()}
                >
                  다시 연결
                </Button>
              </div>
            ) : (
              <div className="flex h-full min-h-0 w-full flex-col bg-white">
                {legacyWorkspaceHistoryEnabled ? (
                  <div
                    className="flex shrink-0 gap-1 border-b border-slate-200 p-2"
                    role="tablist"
                    aria-label="프레젠테이션 대화 및 작업 기록"
                  >
                    <button
                      id="legacy-chat-tab"
                      type="button"
                      role="tab"
                      aria-selected={legacyWorkspacePane === "chat"}
                      aria-controls="legacy-chat-panel"
                      data-testid="legacy-chat-tab"
                      className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                        legacyWorkspacePane === "chat"
                          ? "bg-slate-900 text-white"
                          : "text-slate-600 hover:bg-slate-100"
                      }`}
                      onClick={() => setLegacyWorkspacePane("chat")}
                    >
                      이전 채팅
                    </button>
                    <button
                      id="legacy-history-tab"
                      type="button"
                      role="tab"
                      aria-selected={legacyWorkspacePane === "history"}
                      aria-controls="legacy-history-panel"
                      data-testid="legacy-history-tab"
                      className={`rounded-md px-3 py-1.5 text-sm font-medium ${
                        legacyWorkspacePane === "history"
                          ? "bg-slate-900 text-white"
                          : "text-slate-600 hover:bg-slate-100"
                      }`}
                      onClick={() => setLegacyWorkspacePane("history")}
                    >
                      작업 기록
                    </button>
                  </div>
                ) : null}
                <div
                  id="legacy-chat-panel"
                  role={legacyWorkspaceHistoryEnabled ? "tabpanel" : undefined}
                  aria-labelledby={legacyWorkspaceHistoryEnabled ? "legacy-chat-tab" : undefined}
                  hidden={legacyWorkspaceHistoryEnabled && legacyWorkspacePane !== "chat"}
                  className="min-h-0 flex-1"
                >
                  <Chat
                    presentationId={presentation_id}
                    currentSlide={selectedSlide}
                    onPresentationChanged={handlePresentationChanged}
                    onChatSendingStateChange={handleChatSendingStateChange}
                    onFollowModeChange={setIsFollowModeEnabled}
                    onAgentSlideFocus={handleAgentSlideFocus}
                  />
                </div>
                {legacyWorkspaceHistoryEnabled ? (
                  <div
                    id="legacy-history-panel"
                    role="tabpanel"
                    aria-labelledby="legacy-history-tab"
                    hidden={legacyWorkspacePane !== "history"}
                    className="min-h-0 flex-1"
                  >
                    <WorkspaceChatPanel
                      presentationId={presentation_id}
                      currentSlide={selectedSlide}
                      commandsEnabled={false}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>
    </main>
  );
};

export default PresentationPage;
