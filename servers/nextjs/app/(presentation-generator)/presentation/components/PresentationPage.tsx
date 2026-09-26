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
import { AlertCircle, Keyboard, Layers, X } from "lucide-react";
import { ConversationWorkspaceToolbar } from "./ConversationWorkspaceToolbar";
import { ConversationResultNotice } from "./ConversationResultNotice";
import { useConversationWorkspaceLayout } from "../hooks/useConversationWorkspaceLayout";
import {
  usePresentationStreaming,
  usePresentationData,
  usePresentationNavigation,
  useAutoSave,
} from "../hooks";
import { PresentationPageProps } from "../types";
import LoadingState from "./LoadingState";
import { applyPresentationThemeToElement } from "../utils/applyPresentationThemeDom";
import { tryResolveServerIssuedPresentationProfile } from "@/lib/presentation-profile";
import PresentationHeader from "./PresentationHeader";
import PresentationShell, {
  PresentationFeatureSlot,
  PresentationRuntimeDependencies,
} from "./PresentationShell";
import { usePresentationPageController } from "../hooks/usePresentationPageController";
import {
  WorkspaceChatPanel,
  useWorkspaceCapability,
  type ExportOperationType,
  type WorkspaceAgentSlideFocus,
  type WorkspaceCommandDispatcher,
  type WorkspacePageRemakeRequester,
  type WorkspacePresentationChange,
  type WorkspaceQualityReviewRequester,
} from "../workspace";
import { findMostVisibleSlideIndex } from "../utils/slideScrollSync";
import {
  getSlideNavigationIntent,
  isEditorEditableTarget,
} from "../utils/slideKeyboardNavigation";
import {
  PresentationLoadError,
  PresentationModeView,
} from "./PresentationTerminalStates";
import { renderArtifactSlides } from "@/lib/render-service/production-core";
import { usePresentationPreviewArtifact } from "../hooks/usePresentationPreviewArtifact";
import { useWorkspaceStaging } from "../hooks/useWorkspaceStaging";
import { consumeRasterOriginalPreserveResult } from "../../services/api/source-content/raster-result-hint";
/**
 * Every deck is served by the Slides Workbench, so the editor never takes a
 * direct write: the backend answers those with `chat_first_command_required`.
 * Inline editing stays rendered but read-only.
 */
const INLINE_MUTATIONS_ENABLED = false;
const NAVIGATION_HINT_STORAGE_KEY =
  "ppt-agent:workbench-slide-navigation-hint:v1";
const NAVIGATION_HINT_KEYS = ["←", "↑", "↓", "→"];
const NAVIGATION_HINT_TIMEOUT_MS = 5_000;
const NAVIGATION_SCROLL_THRESHOLD = 240;
const NAVIGATION_SCROLL_WINDOW_MS = 800;

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
  const tPage = useTranslations("presentationPage");
  const pathname = usePathname();
  const dispatch = useDispatch();
  const [loading, setLoading] = useState(true);
  const [selectedSlide, setSelectedSlide] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isAgentWorking, setIsAgentWorking] = useState(false);
  const [agentFocusedSlide, setAgentFocusedSlide] = useState<number | null>(
    null,
  );
  const [agentFocusEventId, setAgentFocusEventId] = useState<string | null>(
    null,
  );
  const [glowingSlideIndex, setGlowingSlideIndex] = useState<number | null>(
    null,
  );
  const [chatTargetedSlides, setChatTargetedSlides] = useState<number[]>([]);
  const [error, setError] = useState(false);
  const [rasterOriginalPreserveNoteId, setRasterOriginalPreserveNoteId] = useState<string | null>(null);
  const [generationObservation, setGenerationObservation] = useState<{
    presentationId: string; loading: boolean; operationId: string | null;
    status: string | null; resultRevision?: number;
  }>({ presentationId: presentation_id, loading: true, operationId: null, status: null });
  const handleGenerationStateChange = useCallback((state: {
    loading: boolean; operationId: string | null; status: string | null; resultRevision?: number;
  }) => setGenerationObservation({ presentationId: presentation_id, ...state }), [presentation_id]);
  const [mobileThumbnailsOpen, setMobileThumbnailsOpen] = useState(false);
  const [showNavigationHint, setShowNavigationHint] = useState(false);
  const [openedPresentationId, setOpenedPresentationId] = useState<string | null>(null);
  const [editCurrentSlide, setEditCurrentSlide] = useState<(() => void) | null>(null);
  const handleEditCurrentSlideReady = useCallback((handler: (() => void) | null) => {
    setEditCurrentSlide(() => handler);
  }, []);
  const slidesScrollContainerRef = useRef<HTMLDivElement | null>(null);
  const slideScrollSyncFrameRef = useRef<number | null>(null);
  const navigationHintShownRef = useRef(false);
  const navigationHintSlideRef = useRef<number | null>(null);
  const navigationScrollIntentRef = useRef({ amount: 0, lastAt: 0 });
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
  const [activeExportOperation, setActiveExportOperation] = useState<ExportOperationType | null>(null);
  const handleWorkspaceCommandDispatcherReady = useCallback(
    (dispatcher: WorkspaceCommandDispatcher | null) => {
      setWorkspaceCommandDispatcher(() => dispatcher);
    },
    [],
  );
  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration,
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
  const observedGeneration = generationObservation.presentationId === presentation_id
    ? generationObservation : null;
  // Published data is already a committed result, including when a later
  // generation attempt has failed. Never hide that earlier result on reload.
  const generationConfirmed = presentationData?.lifecycle_status === "published"
    || observedGeneration?.status === "succeeded"
    || (presentationData?.lifecycle_status == null && !observedGeneration?.operationId
      && observedGeneration?.loading === false && slidesLength > 0);
  const resultReady = Boolean(
    presentationData?.id === presentation_id && generationConfirmed && !loading && !error
    && !isStreaming && !previewArtifactState.pending && !previewArtifactState.error && previewSlides?.length
    && (observedGeneration?.resultRevision === undefined
      || (presentationData.revision ?? -1) >= observedGeneration.resultRevision),
  );
  useEffect(() => {
    if (resultReady && consumeRasterOriginalPreserveResult(presentation_id)) {
      setRasterOriginalPreserveNoteId(presentation_id);
    }
  }, [presentation_id, resultReady]);
  useEffect(() => {
    if (resultReady) setOpenedPresentationId(presentation_id);
  }, [resultReady, presentation_id]);
  const showResult = resultReady || (openedPresentationId === presentation_id && presentationData?.id === presentation_id && slidesLength > 0);
  const resultCompletionKey = resultReady
    ? observedGeneration?.status === "succeeded" && observedGeneration.operationId
      ? `${presentation_id}:${presentationData?.revision ?? "published"}:${observedGeneration.operationId}`
      : !observedGeneration?.operationId && observedGeneration?.loading === false
        ? `${presentation_id}:${presentationData?.revision ?? "published"}:published`
        : null
    : null;
  const workspaceLayout = useConversationWorkspaceLayout({
    presentationId: presentation_id,
    resultReady,
    completionKey: resultCompletionKey,
  });
  const compactWorkspacePane = workspaceLayout.activePane;
  const setCompactWorkspacePane = workspaceLayout.setActivePane;
  const openWorkspaceOperation = useCallback(() => {
    workspaceLayout.setActivePane("chat");
    if (workspaceLayout.chatCollapsed) workspaceLayout.toggleChatCollapsed();
  }, [workspaceLayout.setActivePane, workspaceLayout.chatCollapsed, workspaceLayout.toggleChatCollapsed]);
  const handleWorkspacePageRemakeRequest = useCallback<WorkspacePageRemakeRequester>((requested) => {
    openWorkspaceOperation();
    workspacePageRemakeRequester?.(requested);
  }, [openWorkspaceOperation, workspacePageRemakeRequester]);
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
    setError,
  );
  const {
    content: workspaceStagingContent,
    pollingPaused: workspaceStagingPollingPaused,
    refresh: refreshWorkspaceStaging,
    terminal: workspaceStagingTerminal,
  } = useWorkspaceStaging({
    workspaceStaging,
    presentationId: presentation_id,
    slidesLength,
    pathname,
    router,
    fetchUserSlides,
  });
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
    setIsFullscreen,
  );
  const handleMobileThumbnailSelect = useCallback(
    (slideIndex: number) => {
      setCompactWorkspacePane("slides");
      setMobileThumbnailsOpen(false);
      window.requestAnimationFrame(() => handleSlideClick(slideIndex));
    },
    [handleSlideClick],
  );
  const dismissNavigationHint = useCallback(() => {
    setShowNavigationHint(false);
    navigationHintSlideRef.current = null;
    try {
      window.localStorage.setItem(NAVIGATION_HINT_STORAGE_KEY, "seen");
    } catch {
      // A private or unavailable storage area must not prevent dismissal.
    }
  }, []);
  useEffect(() => {
    if (
      isPresentMode ||
      loading ||
      isStreaming ||
      slidesLength <= 1 ||
      navigationHintShownRef.current ||
      !window.matchMedia("(min-width: 768px)").matches
    ) {
      return;
    }
    try {
      if (window.localStorage.getItem(NAVIGATION_HINT_STORAGE_KEY)) return;
    } catch {
      // Show once this visit when browser storage is unavailable.
    }
    navigationHintShownRef.current = true;
    navigationHintSlideRef.current = selectedSlide;
    setShowNavigationHint(true);
  }, [isPresentMode, isStreaming, loading, selectedSlide, slidesLength]);
  useEffect(() => {
    if (!showNavigationHint) return;
    const timer = window.setTimeout(
      dismissNavigationHint,
      NAVIGATION_HINT_TIMEOUT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [dismissNavigationHint, showNavigationHint]);
  useEffect(() => {
    if (
      !showNavigationHint ||
      navigationHintSlideRef.current === null ||
      navigationHintSlideRef.current === selectedSlide
    ) {
      return;
    }
    dismissNavigationHint();
  }, [dismissNavigationHint, selectedSlide, showNavigationHint]);
  useEffect(() => {
    const scrollContainer = slidesScrollContainerRef.current;
    if (
      !scrollContainer ||
      isPresentMode ||
      loading ||
      isStreaming ||
      slidesLength <= 1 ||
      navigationHintShownRef.current ||
      !window.matchMedia("(min-width: 768px)").matches
    ) {
      return;
    }
    const handleWheel = (event: WheelEvent) => {
      if (
        showNavigationHint ||
        Math.abs(event.deltaY) < 4 ||
        isEditorEditableTarget(event.target)
      ) {
        return;
      }
      const now = Date.now();
      const scrollIntent = navigationScrollIntentRef.current;
      if (now - scrollIntent.lastAt > NAVIGATION_SCROLL_WINDOW_MS) {
        scrollIntent.amount = 0;
      }
      const multiplier =
        event.deltaMode === WheelEvent.DOM_DELTA_LINE
          ? 16
          : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
            ? window.innerHeight
            : 1;
      scrollIntent.amount += Math.abs(event.deltaY) * multiplier;
      scrollIntent.lastAt = now;
      if (scrollIntent.amount < NAVIGATION_SCROLL_THRESHOLD) return;
      scrollIntent.amount = 0;
      navigationHintShownRef.current = true;
      navigationHintSlideRef.current = selectedSlide;
      setShowNavigationHint(true);
    };
    scrollContainer.addEventListener("wheel", handleWheel, { passive: true });
    return () => scrollContainer.removeEventListener("wheel", handleWheel);
  }, [
    isPresentMode,
    isStreaming,
    loading,
    selectedSlide,
    showNavigationHint,
    slidesLength,
  ]);
  useEffect(() => {
    if (isPresentMode || slidesLength <= 1) return;
    const handleEditorArrowNavigation = (event: KeyboardEvent) => {
      const intent = getSlideNavigationIntent(event);
      if (intent === null) return;
      event.preventDefault();
      handleSlideClick(
        Math.min(Math.max(selectedSlide + intent, 0), slidesLength - 1),
      );
    };
    window.addEventListener("keydown", handleEditorArrowNavigation);
    return () =>
      window.removeEventListener("keydown", handleEditorArrowNavigation);
  }, [handleSlideClick, isPresentMode, selectedSlide, slidesLength]);
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
    fetchUserSlides,
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
        `slide-${lastStreamingSlideIndex}`,
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
  const handlePresentationChanged = useCallback(
    async (change?: WorkspacePresentationChange) => {
      const refreshed = await fetchUserSlides({ clearHistory: false, quiet: true });
      if (!refreshed) {
        throw new Error(
          "The committed presentation revision could not be loaded.",
        );
      }
      const slideCount = refreshed?.slides?.length ?? 0;
      if (
        slideCount === 0 ||
        (selectedSlide >= 0 && selectedSlide < slideCount)
      ) {
        return;
      }
      const fallbackIndex =
        change?.fixedSlideIndexes?.find(
          (index) => index >= 0 && index < slideCount,
        ) ?? 0;
      window.requestAnimationFrame(() => handleSlideClick(fallbackIndex));
    },
    [fetchUserSlides, handleSlideClick, selectedSlide],
  );
  const handleAgentActivityChange = useCallback((working: boolean) => {
    setIsAgentWorking(working);
    if (working) {
      setChatTargetedSlides((previous) =>
        previous.length === 0 ? previous : [],
      );
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
        previous.includes(slideIndex) ? previous : [...previous, slideIndex],
      );
    },
    [],
  );
  const totalSlides = presentationData?.slides?.length ?? 0;
  const highlightedSlideIndex = glowingSlideIndex;
  const targetedSlidesSet = useMemo(
    () => new Set(chatTargetedSlides),
    [chatTargetedSlides],
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
    const targetIndex = Math.min(
      Math.max(agentFocusedSlide, 0),
      totalSlides - 1,
    );
    setGlowingSlideIndex(targetIndex);
  }, [
    isAgentWorking,
    totalSlides,
    selectedSlide,
    agentFocusedSlide,
    chatTargetedSlides.length,
    glowingSlideIndex,
  ]);
  if ((error || previewArtifactState.error) && isPresentMode) {
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
      {showResult && <PresentationFeatureSlot name="headerActions">
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
          activeExportOperation={activeExportOperation}
          onCanonicalSaved={handlePresentationChanged}
          headerFeatures={{
            showAuthoredActions: presentationFeatureView.authored,
            exportActions: presentationFeatureView.slots.exportActions,
          }}
        />
      </PresentationFeatureSlot>}
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
      {isAuthoredDeck && showResult && (
        <div className="shrink-0 bg-[#EFF4FF] border-y border-[#C7D7FE] px-6 py-2 text-center text-xs text-[#2D4E9A] font-medium">
          {tPage.rich("authoredNotice", { mode: (chunks) => <span className="font-bold">{chunks}</span>, edit: (chunks) => <span className="font-bold">{chunks}</span> })}
        </div>
      )}
      {showResult && rasterOriginalPreserveNoteId === presentation_id && (
        <div className="shrink-0 border-y border-blue-200 bg-blue-50 px-6 py-2 text-center text-xs font-medium text-blue-950" role="status" data-testid="raster-original-preserve-result-note">
          {tPage("rasterPreserveNotice")}
        </div>
      )}
      {showResult && <ConversationWorkspaceToolbar
        split={workspaceLayout.isSplit} activePane={compactWorkspacePane}
        onPaneChange={setCompactWorkspacePane}
        chatCollapsed={workspaceLayout.chatCollapsed} onToggleChat={workspaceLayout.toggleChatCollapsed}
        showThumbnails={workspaceLayout.showThumbnails}
        thumbnailsOpen={mobileThumbnailsOpen} onThumbnailsOpenChange={setMobileThumbnailsOpen}
        selectedSlide={selectedSlide} onSlideClick={handleMobileThumbnailSelect}
        presentationId={presentation_id} loading={loading}
      />}
      {!showResult && <div className="shrink-0 border-b border-neutral-100 bg-white px-6 py-4 text-sm font-medium text-neutral-800">
        {presentationData?.id === presentation_id ? presentationData.title || tPage("createPresentation") : tPage("createPresentation")}
      </div>}
      {!showResult && <ConversationResultNotice
        failed={error || Boolean(previewArtifactState.error)}
        empty={!loading && !observedGeneration?.loading && !observedGeneration?.operationId && slidesLength === 0}
        staging={showWorkspaceStaging ? workspaceStagingContent : undefined}
        stagingTerminal={workspaceStagingTerminal} stagingPaused={workspaceStagingPollingPaused}
        onReload={() => { setError(false); void fetchUserSlides({ clearHistory: false }); }}
        onUpload={() => router.push("/upload")} onDashboard={() => router.push("/dashboard")}
        onRefreshStaging={refreshWorkspaceStaging} onOpenChat={openWorkspaceOperation}
      />}
      <div ref={workspaceLayout.containerRef} className="relative flex flex-1 min-h-0 min-w-0 overflow-hidden">
        {showResult && <PresentationFeatureSlot name="editorSurface">
          <div
            id="presentation-slides-pane"
            role={workspaceLayout.isSplit ? "region" : "tabpanel"}
            aria-label={tPage("pptPane")}
            aria-labelledby={!workspaceLayout.isSplit && showResult ? "compact-slides-tab" : undefined}
            className={`min-w-0 flex-1 gap-3 ${showResult && (workspaceLayout.isSplit || compactWorkspacePane === "slides") ? "flex" : "hidden"}`}
          >
            <div className={`${workspaceLayout.showThumbnails ? "block" : "hidden"} w-[112px] h-full shrink-0 self-start sticky top-0 pt-[18px]`}>
              <SidePanel
                selectedSlide={selectedSlide}
                onSlideClick={handleSlideClick}
                presentationId={presentation_id}
                loading={loading}
                mutationsEnabled={INLINE_MUTATIONS_ENABLED}
              />
            </div>
            <div className="flex w-full min-w-0 h-full flex-1 flex-col pt-[18px]">
              {editCurrentSlide && <div className="flex shrink-0 justify-end px-4 pb-2">
                <Button variant="outline" size="sm" onClick={() => { openWorkspaceOperation(); editCurrentSlide(); }}>{tPage("editThisSlide")}</Button>
              </div>}
              {showResult && (error || previewArtifactState.error) && <div role="alert" className="flex shrink-0 items-center justify-between gap-3 px-4 py-2 text-sm text-red-700">
                <span>{tPage("latestLoadFailed")}</span>
                <Button size="sm" variant="outline" onClick={() => { setError(false); void fetchUserSlides({ clearHistory: false }); }}>{tPage("reload")}</Button>
              </div>}
              <div
                ref={slidesScrollContainerRef}
                data-testid="slides-scroll-container"
                onScroll={handleSlidesScroll}
                className="font-inter min-h-0 flex-1 overflow-y-auto hide-scrollbar scroll-pt-[18px]"
              >
                {showNavigationHint ? (
                  <div
                    className="pointer-events-none fixed top-[72px] z-[95] hidden items-center gap-3 rounded-full border border-violet-200 bg-white/95 py-2 pl-3 pr-2 text-[13px] text-slate-700 shadow-[0_8px_24px_rgba(16,24,40,0.14)] backdrop-blur md:flex"
                    role="status"
                    aria-live="polite"
                    data-testid="slide-navigation-hint"
                    style={{ left: "50%", transform: "translateX(-50%)" }}
                  >
                    <Keyboard
                      className="h-4 w-4 text-violet-700"
                      aria-hidden="true"
                    />
                    <span>{tPage("arrowNavigation")}</span>
                    <span
                      className="flex items-center gap-1"
                      aria-label={tPage("arrowKeys")}
                    >
                      {NAVIGATION_HINT_KEYS.map((key) => (
                        <kbd
                          key={key}
                          className="flex h-6 min-w-6 items-center justify-center rounded-md border border-slate-200 bg-slate-50 px-1 text-[12px] font-medium text-slate-900 shadow-sm"
                        >
                          {key}
                        </kbd>
                      ))}
                    </span>
                    <span className="hidden lg:inline">
                      {tPage("orSelectFromList")}
                    </span>
                    <button
                      type="button"
                      aria-label={tPage("closeNavigationHint")}
                      onClick={dismissNavigationHint}
                      className="pointer-events-auto ml-1 flex h-11 w-11 items-center justify-center rounded-full text-slate-500 transition hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-700"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                ) : null}
                <div className="w-full max-w-[1280px] min-h-full mx-auto flex flex-col items-center pb-8">
                  {!presentationData ||
                  loading ||
                  (previewArtifactRequired && previewSlides === null) ||
                  !presentationData?.slides ||
                  showWorkspaceStaging ||
                  (presentationData.slides.length === 0 && stream) ? (
                    <div className="relative w-full h-[calc(100vh-120px)] mx-auto hide-scrollbar">
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
                            <div data-testid={workspaceStagingTerminal ? "workspace-staging-terminal" : "workspace-staging-loading"}>
                              {showWorkspaceStaging ? (
                                <LoadingState
                                  title={workspaceStagingContent.title}
                                  description={workspaceStagingContent.description}
                                  notice={workspaceStagingContent.notice}
                                  animate={!workspaceStagingPollingPaused && !workspaceStagingTerminal}
                                  actions={<>
                                    <Button type="button" variant="outline" size="sm" onClick={refreshWorkspaceStaging}>{tPage("refreshStatus")}</Button>
                                    <Button type="button" size="sm" onClick={openWorkspaceOperation}>{tPage("viewTask")}</Button>
                                  </>}
                                />
                              ) : <LoadingState />}
                            </div>
                          )}
                      </>
                    </div>
                  ) : presentationData.slides.length === 0 ? (
                    <div className="flex w-full h-[calc(100vh-160px)] flex-col items-center justify-center text-center">
                      <div className="flex max-w-md flex-col items-center rounded-lg border border-gray-200 bg-white px-6 py-8 shadow-sm">
                        <Layers className="mb-4 h-14 w-14 text-gray-400" />
                        <h2 className="mb-2 text-lg font-semibold text-gray-800">
                          {tPage("noSlides")}
                        </h2>
                        <p className="mb-5 text-sm text-gray-500">
                          {tPage("noSlidesDescription")}
                        </p>
                        <div className="flex items-center justify-center gap-2">
                          <Button
                            onClick={() =>
                              fetchUserSlides({ clearHistory: false })
                            }
                          >
                            {tPage("reload")}
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
                            {tPage("dashboard")}
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
                            deferRender
                            resolveSvgAssets={index === selectedSlide}
                            renderPriority={
                              index === selectedSlide ||
                              index === highlightedSlideIndex ||
                              targetedSlidesSet.has(index)
                            }
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
        </PresentationFeatureSlot>}
        {showResult && !presentationFeatureView.driverId && (
          <div
            role="alert"
            className="flex min-w-0 flex-1 flex-col items-center justify-center gap-1 px-6 py-10 text-center text-sm text-neutral-600"
            data-testid="presentation-unsupported-driver-notice"
          >
            {t("unsupportedTemplateFormat")}
          </div>
        )}
        {showResult && workspaceLayout.isSplit && !workspaceLayout.chatCollapsed && (
          <div {...workspaceLayout.separatorProps} className="group flex w-2 shrink-0 touch-none cursor-col-resize items-center justify-center bg-white outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-600" data-testid="workspace-pane-separator">
            <span className="h-10 w-1 rounded-full bg-neutral-200 group-hover:bg-neutral-400" />
          </div>
        )}
          <div
            id="workspace-chat-pane"
            role={workspaceLayout.isSplit || !showResult ? "region" : "tabpanel"}
            aria-label={tPage("chat")}
            aria-labelledby={!workspaceLayout.isSplit && showResult ? "compact-chat-tab" : undefined}
            data-testid="workspace-chat-pane"
            style={showResult && workspaceLayout.isSplit ? { width: workspaceLayout.chatWidth, flex: "0 0 auto" } : { flex: "1 1 0%" }}
            className={`h-full min-w-0 overflow-hidden bg-white ${!showResult || (workspaceLayout.isSplit ? !workspaceLayout.chatCollapsed : compactWorkspacePane === "chat") ? "flex" : "hidden"} ${!showResult ? "justify-center [&>section]:max-w-[800px] [&>section]:w-full" : ""}`}
          >
            {workspaceReady ? (
              <WorkspaceChatPanel
                presentationId={presentation_id}
                presentation={presentationData}
                currentSlide={selectedSlide}
                commandsEnabled={workspaceCapability.commands}
                generationPending={!showResult && (Boolean(loading || error) || observedGeneration?.loading !== false || Boolean(observedGeneration?.operationId) || Boolean(isStreaming))}
                onGenerationStateChange={handleGenerationStateChange}
                onEditCurrentSlideReady={handleEditCurrentSlideReady}
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
                onExportOperationStateChange={setActiveExportOperation}
              />
            ) : workspaceCapabilityState === "unavailable" ? (
              <div
                className="flex h-full flex-col items-center justify-center gap-3 border-l border-slate-200 bg-white px-6 text-center"
                role="alert"
              >
                <AlertCircle
                  className="h-5 w-5 text-amber-600"
                  aria-hidden="true"
                />
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {tPage("chatUnavailable")}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-slate-500">
                    {tPage("historyUnavailable")}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={refreshWorkspaceCapability}
                >
                  {tPage("reconnect")}
                </Button>
              </div>
            ) : (
              <div
                className="flex h-full items-center justify-center border-l border-slate-200 bg-white text-sm text-slate-500"
                role="status"
                aria-live="polite"
              >
                {tPage("connectingChat")}
              </div>
            )}
          </div>
      </div>
    </PresentationShell>
  );
};
export default PresentationPage;
