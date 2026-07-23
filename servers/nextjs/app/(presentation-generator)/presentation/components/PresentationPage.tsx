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
} from "../hooks";
import { PresentationPageProps } from "../types";
import LoadingState from "./LoadingState";
import { applyPresentationThemeToElement } from "../utils/applyPresentationThemeDom";
import { isAuthoredPresentation } from "../utils/isAuthoredPresentation";

import PresentationHeader from "./PresentationHeader";
import Chat from "./Chat";

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
  const slidesScrollContainerRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();



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

  // Auto-save functionality
  const { isSaving } = useAutoSave({
    debounceMs: 2000,
    enabled: !!presentationData && !isStreaming,
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

  const handlePresentationChanged = useCallback(() => {
    return fetchUserSlides({ clearHistory: false });
  }, [fetchUserSlides]);

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
    <div className="h-screen overflow-hidden font-syne">
      <div
        style={{
          background: "#EDEEEF",
        }}
        id="presentation-slides-wrapper"
        className="relative flex h-full flex-col overflow-hidden"
      >
        <PresentationHeader
          presentation_id={presentation_id}
          isPresentationSaving={isSaving}
          currentSlide={selectedSlide}
          isAuthoredDeck={isAuthoredDeck}
          onReload={() => fetchUserSlides({ clearHistory: true })}
        />
        {isAuthoredDeck && (
          <div className="shrink-0 bg-[#EFF4FF] border-y border-[#C7D7FE] px-6 py-2 text-center text-xs text-[#2D4E9A] font-medium">
            이 발표자료는 <span className="font-bold">AI 저작(고품질)</span> 모드입니다 — 인앱은 보기 전용입니다. 내보낸 PPTX를 PowerPoint에서 편집하세요.
          </div>
        )}
        <div className="flex flex-1 min-h-0 gap-6 overflow-hidden">
          <div className="w-[120px] h-full shrink-0 self-start sticky top-0 pt-[18px]">
            <SidePanel
              selectedSlide={selectedSlide}
              onSlideClick={handleSlideClick}
              presentationId={presentation_id}
              loading={loading}
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
          <div className="w-full max-w-[370px] h-full shrink-0 self-start sticky top-0">
            <Chat
              presentationId={presentation_id}
              currentSlide={selectedSlide}
              onPresentationChanged={handlePresentationChanged}
              onChatSendingStateChange={handleChatSendingStateChange}
              onFollowModeChange={setIsFollowModeEnabled}
              onAgentSlideFocus={handleAgentSlideFocus}
            />
          </div>
        </div>
      </div>
    </div>
  );
};

export default PresentationPage;
