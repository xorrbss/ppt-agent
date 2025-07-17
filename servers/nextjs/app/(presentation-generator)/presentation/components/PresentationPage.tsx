"use client";
import React, { useState } from "react";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { Skeleton } from "@/components/ui/skeleton";
import PresentationMode from "../../components/PresentationMode";
import SidePanel from "../components/SidePanel";
import SlideContent from "../components/SlideContent";
import LoadingState from "../../components/LoadingState";
import Header from "../components/Header";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AlertCircle } from "lucide-react";
import Help from "./Help";
import {
  usePresentationStreaming,
  usePresentationData,
  usePresentationNavigation
} from "../hooks";
import { PresentationPageProps } from "../types";

const PresentationPage: React.FC<PresentationPageProps> = ({ presentation_id }) => {
  // State management
  const [loading, setLoading] = useState(true);
  const [selectedSlide, setSelectedSlide] = useState(0);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [error, setError] = useState(false);
  const [isMobilePanelOpen, setIsMobilePanelOpen] = useState(false);
  const [autoSaveLoading, setAutoSaveLoading] = useState(false);

  // Redux state
  const { currentTheme, currentColors } = useSelector(
    (state: RootState) => state.theme
  );
  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );

  // Custom hooks
  const { fetchUserSlides, handleDeleteSlide } = usePresentationData(
    presentation_id,
    setLoading,
    setError
  );

  const {
    isPresentMode,
    stream,
    currentSlide,
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

  const onDeleteSlide = (index: number) => {
    handleDeleteSlide(index, presentationData);
  };

  const onSlideChange = (newSlide: number) => {
    handleSlideChange(newSlide, presentationData);
  };

  // Presentation Mode View
  if (isPresentMode) {
    return (
      <PresentationMode
        slides={presentationData?.slides!}
        currentSlide={currentSlide}
        currentTheme={currentTheme}
        isFullscreen={isFullscreen}
        onFullscreenToggle={toggleFullscreen}
        onExit={handlePresentExit}
        onSlideChange={onSlideChange}
      />
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-gray-100">
        <div
          className="bg-white border border-red-300 text-red-700 px-6 py-8 rounded-lg shadow-lg flex flex-col items-center"
          role="alert"
        >
          <AlertCircle className="w-16 h-16 mb-4 text-red-500" />
          <strong className="font-bold text-4xl mb-2">Oops!</strong>
          <p className="block text-2xl py-2">
            We encountered an issue loading your presentation.
          </p>
          <p className="text-lg py-2">
            Please check your internet connection or try again later.
          </p>
          <Button
            className="mt-4 bg-red-500 text-white hover:bg-red-600 focus:ring-4 focus:ring-red-300"
            onClick={() => window.location.reload()}
          >
            Retry
          </Button>
        </div>
      </div>
    );
  }


  return (
    <div className="h-screen flex overflow-hidden flex-col">
      {/* Auto save loading indicator */}
      {autoSaveLoading && (
        <div className="fixed right-6 top-[5.2rem] z-50 bg-white bg-opacity-50 flex items-center justify-center">
          <Loader2 className="animate-spin text-primary" />
        </div>
      )}

      <Header presentation_id={presentation_id} currentSlide={currentSlide} />
      <Help />

      <div
        style={{
          background: currentColors.background,
        }}
        className="flex flex-1 relative pt-6"
      >
        <SidePanel
          selectedSlide={selectedSlide}
          onSlideClick={handleSlideClick}
          loading={loading}
          isMobilePanelOpen={isMobilePanelOpen}
          setIsMobilePanelOpen={setIsMobilePanelOpen}
        />

        <div className="flex-1 h-[calc(100vh-100px)] overflow-y-auto">
          <div className="mx-auto flex flex-col items-center overflow-hidden justify-center p-2 sm:p-6 pt-0">
            {!presentationData ||
              loading ||
              !presentationData?.slides ||
              presentationData?.slides.length === 0 ? (
              <div className="relative w-full h-[calc(100vh-120px)] mx-auto">
                <div className="">
                  {Array.from({ length: 2 }).map((_, index) => (
                    <Skeleton
                      key={index}
                      className="aspect-video bg-gray-400 my-4 w-full mx-auto max-w-[1280px]"
                    />
                  ))}
                </div>
                {stream && <LoadingState />}
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
                      onDeleteSlide={onDeleteSlide}
                    />
                  ))}
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default PresentationPage;
