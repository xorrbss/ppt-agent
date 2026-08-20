"use client";
import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import "../../utils/prism-languages";
import { Skeleton } from "@/components/ui/skeleton";
import SidePanel from "./SidePanel";
import SlideContent from "./SlideContent";
import { Button } from "@/components/ui/button";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { AlertCircle, Layers, PanelLeft } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  usePresentationStreaming,
  usePresentationData,
  usePresentationNavigation,
  useAutoSave,
} from "../hooks";
import { PresentationPageProps } from "../types";
import LoadingState from "./LoadingState";
import { applyPresentationThemeToElement } from "../utils/applyPresentationThemeDom";
import {
  tryResolveServerIssuedPresentationProfile,
} from "@/lib/presentation-profile";
import PresentationHeader from "./PresentationHeader";
import PresentationShell, {
  PresentationFeatureSlot,
  PresentationRuntimeDependencies,
} from "./PresentationShell";
import { usePresentationPageController } from "../hooks/usePresentationPageController";
import {
  WorkspaceChatPanel,
  useWorkspaceCapability,
  type WorkspaceAgentSlideFocus,
  type WorkspaceCommandDispatcher,
  type WorkspacePageRemakeRequester,
  type WorkspacePresentationChange,
  type WorkspaceQualityReviewRequester,
} from "../workspace";
import { findMostVisibleSlideIndex } from "../utils/slideScrollSync";
import { PresentationLoadError, PresentationModeView } from "./PresentationTerminalStates";
import { renderArtifactSlides } from "@/lib/render-service";
import { usePresentationPreviewArtifact } from "../hooks/usePresentationPreviewArtifact";
import { getWorkspaceTimeline } from "../../services/api/workspace";
/**
 * Every deck is served by the Slides Workbench, so the editor never takes a
 * direct write: the backend answers those with `chat_first_command_required`.
 * Inline editing stays rendered but read-only.
 */
const INLINE_MUTATIONS_ENABLED = false;
const WORKSPACE_STAGING_POLL_INTERVAL_MS = 2_000;
const WORKSPACE_STAGING_FAILURE_STATES = new Set([
  "failed",
  "dead_letter",
  "cancelled",
  "rejected",
  "expired",
]);

export function useAgentSlideFocusOnce({
  presentationId,
  totalSlides,
  focusedSlide,
  focusEventId,
  selectedSlide,
  onSlideFocus,
}: {
  presentationId: string;
  totalSlides: number;
  focusedSlide: number | null;
  focusEventId: string | null;
  selectedSlide: number;
  onSlideFocus: (slideIndex: number) => void;
}) {
  const appliedFocusRef = useRef<{
    eventKey: string;
    slideIndex: number;
  } | null>(null);

  useEffect(() => {
    if (totalSlides <= 0 || focusedSlide === null || focusEventId === null) {
      return;
    }
    const clampedIndex = Math.min(Math.max(focusedSlide, 0), totalSlides - 1);
    const eventKey = `${presentationId}:${focusEventId}`;
    const appliedFocus = appliedFocusRef.current;
    if (
      appliedFocus?.eventKey === eventKey &&
      appliedFocus.slideIndex === clampedIndex
    ) {
      return;
    }
    appliedFocusRef.current = { eventKey, slideIndex: clampedIndex };
    if (clampedIndex !== selectedSlide) {
      onSlideFocus(clampedIndex);
    }
  }, [
    focusEventId,
    focusedSlide,
    onSlideFocus,
    presentationId,
    selectedSlide,
    totalSlides,
  ]);
}

const PresentationPage: React.FC<PresentationPageProps> = ({
  presentation_id,
  workspaceStaging = false,
}) => {
  const t = useTranslations("community");
  const pathname = usePathname();
  const dispatch = useDispatch();
  const [loading, setLoading] = useState(true);
  const [selectedSlide, setSelectedSlide] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isAgentWorking, setIsAgentWorking] = useState(false);
  const [agentFocusedSlide, setAgentFocusedSlide] = useState<number | null>(null);
  const [agentFocusEventId, setAgentFocusEventId] = useState<string | null>(null);
  const [glowingSlideIndex, setGlowingSlideIndex] = useState<number | null>(null);
  const [chatTargetedSlides, setChatTargetedSlides] = useState<number[]>([]);
  const [error, setError] = useState(false);
  const [compactWorkspacePane, setCompactWorkspacePane] = useState<
    "slides" | "chat"
  >("chat");
  const [workspaceDrawerOpen, setWorkspaceDrawerOpen] = useState(true);
  const [workspaceStagingFailure, setWorkspaceStagingFailure] = useState<
    string | null
  >(null);
  const [mobileThumbnailsOpen, setMobileThumbnailsOpen] = useState(false);
  const slidesScrollContainerRef = useRef<HTMLDivElement | null>(null);
  const slideScrollSyncFrameRef = useRef<number | null>(null);
  const router = useRouter();
  const {
    state: workspaceCapabilityState,
    capability: workspaceCapability,
    refresh: refreshWorkspaceCapability,
  } = useWorkspaceCapability(true, presentation_id);
  const workspaceReady = workspaceCapabilityState === "supported";
  const [workspaceCommandDispatcher, setWorkspaceCommandDispatcher] =
    useState<WorkspaceCommandDispatcher | null>(null);
  const [workspaceQualityReviewRequester, setWorkspaceQualityReviewRequester] =
    useState<WorkspaceQualityReviewRequester | null>(null);
  const [workspacePageRemakeRequester, setWorkspacePageRemakeRequester] =
    useState<WorkspacePageRemakeRequester | null>(null);
  const handleWorkspaceCommandDispatcherReady = useCallback(
    (dispatcher: WorkspaceCommandDispatcher | null) => {
      setWorkspaceCommandDispatcher(() => dispatcher);
    },
    [],
  );
  const handleWorkspacePageRemakeRequest = useCallback<WorkspacePageRemakeRequester>(
    (requested) => {
      setCompactWorkspacePane("chat");
      setWorkspaceDrawerOpen(true);
      workspacePageRemakeRequester?.(requested);
    },
    [workspacePageRemakeRequester],
  );
  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const presentationRuntime = useMemo(
    () => tryResolveServerIssuedPresentationProfile(presentationData),
    [presentationData],
  );
  const presentationFeatureView = usePresentationPageController({
    runtime: presentationRuntime,
    workspaceReady,
    loading: loading || presentationData == null,
  });
  const isAuthoredDeck = presentationFeatureView.authored;
  const slidesLength = presentationData?.slides?.length ?? 0;
  const previewArtifactRequired =
    presentationRuntime !== null && slidesLength > 0 && !loading;
  const previewArtifactState = usePresentationPreviewArtifact(
    presentationData,
    previewArtifactRequired,
  );
  const previewSlides = previewArtifactState.artifact
    ? renderArtifactSlides(previewArtifactState.artifact)
    : null;
  useEffect(() => {
    if (previewArtifactState.error) setError(true);
  }, [previewArtifactState.error]);
  const showWorkspaceStaging = workspaceStaging && slidesLength === 0;
  const lastStreamingSlideIndex =
    slidesLength > 0
      ? presentationData?.slides?.[slidesLength - 1]?.index
      : undefined;
  const { isSaving, saveFailed } = useAutoSave({
    debounceMs: 2000,
    enabled: INLINE_MUTATIONS_ENABLED && !!presentationData && !isStreaming,
  });
  const { fetchUserSlides } = usePresentationData(
    presentation_id,
    setLoading,
    setError
  );
  useEffect(() => {
    if (!workspaceStaging || slidesLength > 0) return;

    let cancelled = false;
    let timer: number | null = null;
    const scheduleNextPoll = () => {
      if (!cancelled) {
        timer = window.setTimeout(pollWorkspaceStaging, WORKSPACE_STAGING_POLL_INTERVAL_MS);
      }
    };
    const pollWorkspaceStaging = async () => {
      try {
        const timeline = await getWorkspaceTimeline(presentation_id, { limit: 8 });
        if (cancelled) return;
        const designOperation = timeline.items.reduce<
          Extract<(typeof timeline.items)[number], { anchorType: "operation" }> | undefined
        >((latest, item) => {
          if (
            item.anchorType !== "operation" ||
            (item.operationType !== "design.apply" &&
              item.operationType !== "template.apply")
          ) {
            return latest;
          }
          return latest === undefined || item.position > latest.position
            ? item
            : latest;
        }, undefined);
        if (
          designOperation?.anchorType === "operation" &&
          WORKSPACE_STAGING_FAILURE_STATES.has(designOperation.status)
        ) {
          setLoading(false);
          setWorkspaceStagingFailure(
            designOperation.error ||
              "디자인 변경 작업을 완료하지 못했습니다. 원본 자료에서 다시 시도해 주세요.",
          );
          scheduleNextPoll();
          return;
        }

        if (workspaceStagingFailure) setWorkspaceStagingFailure(null);

        const refreshed = await fetchUserSlides({ clearHistory: false });
        if (cancelled) return;
        if ((refreshed?.slides?.length ?? 0) > 0) {
          const params = new URLSearchParams(window.location.search);
          params.delete("workspace_staging");
          const query = params.toString();
          router.replace(`${pathname}${query ? `?${query}` : ""}`);
          return;
        }
      } catch (pollError) {
        console.warn("Failed to refresh workspace staging presentation", pollError);
      }
      scheduleNextPoll();
    };

    timer = window.setTimeout(pollWorkspaceStaging, 500);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [
    fetchUserSlides,
    pathname,
    presentation_id,
    router,
    slidesLength,
    workspaceStaging,
    workspaceStagingFailure,
  ]);
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
  const handleMobileThumbnailSelect = useCallback(
    (slideIndex: number) => {
      setCompactWorkspacePane("slides");
      setMobileThumbnailsOpen(false);
      window.requestAnimationFrame(() => handleSlideClick(slideIndex));
    },
    [handleSlideClick],
  );
  useEffect(() => {
    const desktopRail = window.matchMedia("(min-width: 768px)");
    const closeMobileDrawer = (event: MediaQueryListEvent | MediaQueryList) => {
      if (event.matches) setMobileThumbnailsOpen(false);
    };
    closeMobileDrawer(desktopRail);
    desktopRail.addEventListener("change", closeMobileDrawer);
    return () => desktopRail.removeEventListener("change", closeMobileDrawer);
  }, []);
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
    const fallbackIndex = change?.fixedSlideIndexes?.find(
      (index) => index >= 0 && index < slideCount,
    ) ?? 0;
    window.requestAnimationFrame(() => handleSlideClick(fallbackIndex));
  }, [fetchUserSlides, handleSlideClick, selectedSlide]);
  const handleAgentActivityChange = useCallback((working: boolean) => {
    setIsAgentWorking(working);
    if (working) {
      setChatTargetedSlides((previous) => (previous.length === 0 ? previous : []));
      return;
    }
    setAgentFocusedSlide(null);
    setAgentFocusEventId(null);
  }, []);
  const handleAgentSlideFocus = useCallback(
    ({ slideIndex, eventId }: WorkspaceAgentSlideFocus) => {
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
  const handleSlidesScroll = useCallback(() => {
    if (slideScrollSyncFrameRef.current !== null) return;
    slideScrollSyncFrameRef.current = window.requestAnimationFrame(() => {
      slideScrollSyncFrameRef.current = null;
      const container = slidesScrollContainerRef.current;
      if (!container) return;
      const viewport = container.getBoundingClientRect();
      const candidates = Array.from(
        container.querySelectorAll<HTMLElement>("[data-slide-position]"),
      ).flatMap((element) => {
        const index = Number(element.dataset.slidePosition);
        if (!Number.isInteger(index)) return [];
        const rect = element.getBoundingClientRect();
        return [{ index, top: rect.top, bottom: rect.bottom }];
      });
      const visibleIndex = findMostVisibleSlideIndex(viewport, candidates);
      if (visibleIndex === null) return;
      setSelectedSlide((current) =>
        current === visibleIndex ? current : visibleIndex,
      );
    });
  }, []);
  useEffect(
    () => () => {
      if (slideScrollSyncFrameRef.current !== null) {
        window.cancelAnimationFrame(slideScrollSyncFrameRef.current);
      }
    },
    [],
  );
  useAgentSlideFocusOnce({
    presentationId: presentation_id,
    totalSlides,
    focusedSlide: agentFocusedSlide,
    focusEventId: agentFocusEventId,
    selectedSlide,
    onSlideFocus: handleSlideClick,
  });
  useEffect(() => {
    if (totalSlides <= 0) {
      setGlowingSlideIndex(null);
      setChatTargetedSlides([]);
      return;
    }
    if (!isAgentWorking) {
      if (glowingSlideIndex === null && chatTargetedSlides.length === 0) {
        return;
      }
      const clearTimer = window.setTimeout(() => {
        setGlowingSlideIndex(null);
        setChatTargetedSlides([]);
      }, 900);
      return () => window.clearTimeout(clearTimer);
    }
    if (agentFocusedSlide === null) {
      if (glowingSlideIndex !== null) {
        setGlowingSlideIndex(null);
      }
      return;
    }
    const targetIndex = Math.min(Math.max(agentFocusedSlide, 0), totalSlides - 1);
    setGlowingSlideIndex(targetIndex);
  }, [
    isAgentWorking,
    totalSlides,
    selectedSlide,
    agentFocusedSlide,
    chatTargetedSlides.length,
    glowingSlideIndex,
  ]);
  if (error || previewArtifactState.error) {
    return <PresentationLoadError pathname={pathname} router={router} />;
  }
  if (isPresentMode) {
    if (previewArtifactRequired && previewSlides === null) {
      return <LoadingState />;
    }
    return (
      <PresentationModeView
        presentationData={
          presentationData && previewSlides
            ? { ...presentationData, slides: previewSlides }
            : presentationData
        }
        presentSlide={presentSlideFromUrl}
        isFullscreen={isFullscreen}
        onFullscreenToggle={toggleFullscreen}
        onExit={handlePresentExit}
        onSlideChange={onSlideChange}
      />
    );
  }
  return (
    <PresentationShell
      title={presentationData?.title ?? t("presentationEditor")}
      background={presentationFeatureView.background}
      viewModel={presentationFeatureView}
      renderArtifact={previewArtifactState.artifact}
    >
      <PresentationFeatureSlot name="headerActions">
        <PresentationHeader
          presentation_id={presentation_id}
          isPresentationSaving={isSaving}
          isPresentationSaveFailed={saveFailed}
          currentSlide={selectedSlide}
          isAuthoredDeck={isAuthoredDeck}
          mutationsEnabled={INLINE_MUTATIONS_ENABLED}
          workspaceCommandsEnabled={
            workspaceReady &&
            workspaceCapability.commands &&
            workspaceCommandDispatcher !== null
          }
          onWorkspaceCommand={workspaceCommandDispatcher ?? undefined}
          onWorkspaceQualityReviewRequest={
            workspaceQualityReviewRequester ?? undefined
          }
          onWorkspacePageRemakeRequest={
            workspacePageRemakeRequester
              ? handleWorkspacePageRemakeRequest
              : undefined
          }
          headerFeatures={{
            showAuthoredActions: presentationFeatureView.authored,
            exportActions: presentationFeatureView.slots.exportActions,
          }}
        />
      </PresentationFeatureSlot>
      <PresentationFeatureSlot name="runtimeDependencies">
        <PresentationRuntimeDependencies
          dependencies={presentationFeatureView.runtimeDependencies}
        />
      </PresentationFeatureSlot>
      <PresentationFeatureSlot name="exportActions">
        <div
          aria-hidden="true"
          className="hidden"
          data-testid="presentation-export-actions-slot"
        />
      </PresentationFeatureSlot>
        {isAuthoredDeck && (
          <div className="shrink-0 bg-[#EFF4FF] border-y border-[#C7D7FE] px-6 py-2 text-center text-xs text-[#2D4E9A] font-medium">
            이 발표자료는 <span className="font-bold">AI 저작(고품질)</span> 모드입니다 — 인앱은 보기 전용입니다. 내보낸 PPTX를 PowerPoint에서 편집하세요.
          </div>
        )}
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
          <Sheet open={mobileThumbnailsOpen} onOpenChange={setMobileThumbnailsOpen}>
            <SheetTrigger asChild>
              <button
                type="button"
                data-testid="mobile-thumbnail-drawer-trigger"
                aria-label={mobileThumbnailsOpen ? "슬라이드 목록 닫기" : "슬라이드 목록 열기"}
                className="ml-2 inline-flex h-11 shrink-0 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-violet-700 focus-visible:ring-offset-2 md:hidden"
              >
                <PanelLeft aria-hidden="true" className="h-4 w-4" />
                <span className="hidden sm:inline">슬라이드 목록</span>
              </button>
            </SheetTrigger>
            <SheetContent
              side="left"
              data-testid="mobile-thumbnail-drawer"
              className="flex h-dvh w-[min(320px,calc(100vw-16px))] max-w-none flex-col gap-3 p-4 [&>button]:h-11 [&>button]:w-11"
            >
              <SheetHeader className="pr-12 text-left">
                <SheetTitle>슬라이드 목록</SheetTitle>
                <SheetDescription>
                  이동할 슬라이드를 선택하세요.
                </SheetDescription>
              </SheetHeader>
              <div className="min-h-0 flex-1 overflow-hidden pt-2">
                <SidePanel
                  selectedSlide={selectedSlide}
                  onSlideClick={handleMobileThumbnailSelect}
                  presentationId={presentation_id}
                  loading={loading}
                  mutationsEnabled={INLINE_MUTATIONS_ENABLED}
                  displayMode="drawer"
                />
              </div>
            </SheetContent>
          </Sheet>
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
        <div className="relative flex flex-1 min-h-0 gap-6 overflow-hidden">
          <PresentationFeatureSlot name="editorSurface">
          <div
            id="presentation-slides-pane"
            role="tabpanel"
            aria-labelledby="compact-slides-tab"
            className={`min-w-0 flex-1 gap-6 ${
              compactWorkspacePane === "slides" ? "flex" : "hidden lg:flex"
            }`}
          >
          <div className="hidden md:block w-[120px] h-full shrink-0 self-start sticky top-0 pt-[18px]">
            <SidePanel
              selectedSlide={selectedSlide}
              onSlideClick={handleSlideClick}
              presentationId={presentation_id}
              loading={loading}
              mutationsEnabled={INLINE_MUTATIONS_ENABLED}
            />
          </div>
          <div className="w-full min-w-0 h-full flex-1 pt-[18px]">
            <div
              ref={slidesScrollContainerRef}
              data-testid="slides-scroll-container"
              onScroll={handleSlidesScroll}
              className="font-inter h-full overflow-y-auto hide-scrollbar scroll-pt-[18px]"
            >
              <div className="w-full max-w-[1280px] min-h-full mx-auto flex flex-col items-center pb-8">
                {!presentationData ||
                  loading ||
                  (previewArtifactRequired && previewSlides === null) ||
                  !presentationData?.slides ||
                  showWorkspaceStaging ||
                  (presentationData.slides.length === 0 && stream) ? (
                  <div className="relative w-full h-[calc(100vh-120px)] mx-auto hide-scrollbar">
                    {workspaceStagingFailure ? (
                      <div
                        className="flex h-full items-center justify-center px-6"
                        data-testid="workspace-staging-failure"
                      >
                        <div className="flex max-w-lg flex-col items-center rounded-2xl border border-red-200 bg-white px-8 py-10 text-center shadow-sm">
                          <AlertCircle className="mb-4 h-12 w-12 text-red-500" />
                          <h2 className="text-xl font-semibold text-gray-950">
                            디자인 변경에 실패했습니다
                          </h2>
                          <p className="mt-3 text-sm leading-6 text-gray-600">
                            {workspaceStagingFailure}
                          </p>
                          <div className="mt-6 flex gap-2">
                            <Button type="button" onClick={() => window.close()}>
                              창 닫기
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              onClick={() => router.push("/dashboard")}
                            >
                              대시보드로 이동
                            </Button>
                          </div>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div>
                          {Array.from({ length: 2 }).map((_, index) => (
                            <Skeleton
                              key={index}
                              className="aspect-video bg-gray-400 my-4 w-full mx-auto "
                            />
                          ))}
                        </div>
                        {(stream || showWorkspaceStaging) && (
                          <div data-testid="workspace-staging-loading">
                            <LoadingState />
                          </div>
                        )}
                      </>
                    )}
                  </div>
                ) : presentationData.slides.length === 0 ? (
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
                    {previewSlides &&
                      previewSlides.length > 0 &&
                      previewSlides.map((slide: any, index: number) => (
                        <SlideContent
                          key={`${slide.type}-${index}-${slide.index}`}
                          slide={slide}
                          index={index}
                          presentationId={presentation_id}
                          mutationsEnabled={INLINE_MUTATIONS_ENABLED}
                          isChatEditing={
                            highlightedSlideIndex !== null &&
                            index === highlightedSlideIndex
                          }
                          isChatTargeted={
                            isAgentWorking &&
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
          </PresentationFeatureSlot>
          <PresentationFeatureSlot name="assistantPanel">
          <div
            id="workspace-chat-pane"
            role="tabpanel"
            aria-labelledby="compact-chat-tab"
            data-testid="workspace-chat-pane"
            className={`h-full min-w-0 overflow-hidden ${
              compactWorkspacePane === "chat" ? "flex" : "hidden"
            } ${workspaceDrawerOpen ? "lg:flex" : "lg:hidden"} xl:flex flex-1 lg:absolute lg:inset-y-0 lg:right-0 lg:z-40 lg:w-[min(440px,100%)] lg:max-w-[440px] lg:flex-none lg:shadow-2xl xl:sticky xl:top-0 xl:z-auto xl:w-full xl:max-w-[clamp(360px,30vw,560px)] xl:shrink-0 xl:self-start xl:shadow-none`}
          >
            {workspaceReady ? (
              <WorkspaceChatPanel
                presentationId={presentation_id}
                currentSlide={selectedSlide}
                commandsEnabled={workspaceCapability.commands}
                onPresentationChanged={handlePresentationChanged}
                onSlideFocus={handleSlideClick}
                onAgentActivityChange={handleAgentActivityChange}
                onAgentSlideFocus={handleAgentSlideFocus}
                onCommandDispatcherReady={handleWorkspaceCommandDispatcherReady}
                onQualityReviewRequesterReady={(requester) =>
                  setWorkspaceQualityReviewRequester(() => requester)
                }
                onPageRemakeRequesterReady={(requester) =>
                  setWorkspacePageRemakeRequester(() => requester)
                }
              />
            ) : workspaceCapabilityState === "unavailable" ? (
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
                    이 런타임에서는 작업 기록을 사용할 수 없습니다. 연결을 다시
                    시도해 주세요.
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={refreshWorkspaceCapability}
                >
                  다시 연결
                </Button>
              </div>
            ) : (
              <div
                className="flex h-full items-center justify-center border-l border-slate-200 bg-white text-sm text-slate-500"
                role="status"
                aria-live="polite"
              >
                작업 대화를 연결하는 중입니다.
              </div>
            )}
          </div>
          </PresentationFeatureSlot>
        </div>
    </PresentationShell>
  );
};
export default PresentationPage;
