"use client";

import React, { useState, useEffect, useMemo, useRef } from "react";
import { useRouter } from "next/navigation";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RootState } from "@/store/store";
import { useDispatch, useSelector } from "react-redux";
import { setPresentationId } from "@/store/slices/presentationGeneration";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import Wrapper from "@/components/Wrapper";
import OutlineContent from "./OutlineContent";
import EmptyStateView from "./EmptyStateView";
import GenerateButton from "./GenerateButton";

import { TABS } from "../types/index";
import { useOutlineStreaming } from "../hooks/useOutlineStreaming";
import { useOutlineManagement } from "../hooks/useOutlineManagement";
import { usePresentationGeneration } from "../hooks/usePresentationGeneration";
import TemplateSelection, { AUTHORED_TEMPLATE_ID } from "./TemplateSelection";
import { findResumableGenerationTask } from "../hooks/durableGenerationTask";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { resolveTemplateSelection } from "@/app/presentation-templates/select";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";

const OutlinePage: React.FC<{ auto?: boolean }> = ({ auto = false }) => {
  const { presentation_id, outlines } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const storedTemplateId = useSelector(
    (state: RootState) => state.pptGenUpload.selectedTemplate
  );

  const [activeTab, setActiveTab] = useState<string>(TABS.OUTLINE);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateLayoutsWithSettings | string | null>(null);

  // In auto (compose) mode the template was chosen up-front and lives in Redux;
  // in manual mode the user picks it on this page.
  const autoTemplate = useMemo(
    () => resolveTemplateSelection(storedTemplateId),
    [storedTemplateId]
  );
  const generationTemplate = auto ? autoTemplate : selectedTemplate;

  // Custom hooks
  const dispatch = useDispatch();
  const router = useRouter();
  const streamState = useOutlineStreaming(presentation_id);
  const { handleDragEnd, handleAddSlide } = useOutlineManagement(outlines);
  const { loadingState, handleSubmit, cancelGeneration, resumableTask } =
    usePresentationGeneration(
      presentation_id,
      outlines,
      generationTemplate,
      setActiveTab
    );
  const slideProgress = useMemo(() => {
    const progress = loadingState.progress;
    if (
      !progress ||
      !Number.isFinite(progress.completed_slides) ||
      !Number.isFinite(progress.total_slides) ||
      progress.total_slides <= 0
    ) {
      return null;
    }
    const completed = Math.min(
      progress.total_slides,
      Math.max(0, progress.completed_slides)
    );
    return {
      // A deck with every slide persisted is still incomplete while the server
      // is assembling/publishing the PPTX. The completed response navigates away.
      value: Math.min(
        99,
        Math.floor((completed / progress.total_slides) * 100)
      ),
      label: progress.finalizing
        ? `슬라이드 ${completed}/${progress.total_slides} 완료 · 최종 파일 생성 중`
        : `슬라이드 ${completed}/${progress.total_slides} 완료`,
    };
  }, [loadingState.progress]);

  // Auto-bridge: once the outline stream completes, submit exactly once.
  const autoSubmittedRef = useRef(false);
  // Only auto-submit for a stream that actually ran THIS mount. If the outlines
  // were already in the store when this page mounted (back-navigation into a
  // completed auto flow), don't re-fire generation — show the outline instead.
  const outlinesPreexistingRef = useRef(outlines.length > 0);
  useEffect(() => {
    if (!auto || autoSubmittedRef.current) return;
    if (outlinesPreexistingRef.current) return;
    if (!presentation_id || streamState.isStreaming) return;
    if (!streamState.hasCompleted) return;
    if (!outlines || outlines.length === 0) return;
    if (!generationTemplate) return;
    autoSubmittedRef.current = true;
    handleSubmit();
  }, [
    auto,
    presentation_id,
    streamState.isStreaming,
    streamState.hasCompleted,
    outlines,
    generationTemplate,
    handleSubmit,
  ]);

  // A reload rebuilds Redux from scratch (nothing is persisted and /outline
  // carries no id), which would strand an interrupted-but-resumable generation
  // behind the EmptyStateView. Rehydrate the outline shell from the stored
  // durable task; storage is client-only, so read it in an effect to keep the
  // server and hydration renders identical.
  useEffect(() => {
    if (presentation_id) return;
    const storedTask = findResumableGenerationTask();
    if (!storedTask?.sourcePresentationId) return;
    // The auto-bridge submit is only for an outline stream run by a fresh
    // compose flow; a rehydrated interrupted task must be resumed explicitly
    // through the banner, never auto-fired.
    autoSubmittedRef.current = true;
    // Resumable tasks are always authored-mode (see usePresentationGeneration),
    // so restore that selection or handleSubmit would route to the default
    // template and start a fresh, fully re-billed generation.
    setSelectedTemplate(AUTHORED_TEMPLATE_ID);
    dispatch(setPptGenUploadState({ selectedTemplate: AUTHORED_TEMPLATE_ID }));
    dispatch(setPresentationId(storedTask.sourcePresentationId));
  }, [presentation_id, dispatch]);

  if (!presentation_id) {
    return <EmptyStateView />;
  }

  // Auto mode shows a progress-only view WHILE actively working: streaming the
  // outline or generating. handleSubmit enables loading synchronously before its
  // first await. If generation fails and loading ends without navigation, this
  // becomes false and the normal outline UI lets the user retry.
  const autoWorking =
    auto && (streamState.isStreaming || loadingState.isLoading);
  if (autoWorking) {
    const generating = loadingState.isLoading;
    return (
      <div className="font-syne pb-9">
        <OverlayLoader
          show={true}
          // Use the real generation message + ETA (authored decks can take many
          // minutes) instead of a fixed 60s bar that parks at 95%.
          text={
            streamState.isStreaming
              ? "개요를 생성하는 중…"
              : generating
                ? loadingState.message
                : "발표자료를 준비하는 중…"
          }
          showProgress={true}
          duration={generating ? loadingState.duration : 60}
          progress={generating ? slideProgress?.value : undefined}
          progressLabel={generating ? slideProgress?.label : undefined}
          // While generating: invalidate the run and fall back to the outline UI
          // so the user can review/edit and regenerate. While the outline is still
          // streaming: go back to compose (unmount closes the SSE stream).
          onCancel={generating ? cancelGeneration : () => router.back()}
          cancelText={generating ? "생성 취소" : "취소하고 돌아가기"}
        />
      </div>
    );
  }

  const handleTabChange = (tab: string) => {
    if (streamState.isStreaming) {
      return;
    }
    setActiveTab(tab);

  };

  // Interrupted-but-resumable generation: a same-key re-submit resumes the
  // server-side slide checkpoints, so the banner reuses handleSubmit as-is.
  const resumeProgress = resumableTask?.lastProgress;
  const resumeBannerText = resumableTask
    ? resumeProgress &&
      Number.isFinite(resumeProgress.completed_slides) &&
      Number.isFinite(resumeProgress.total_slides) &&
      resumeProgress.total_slides > 0
      ? `이전 생성이 슬라이드 ${resumeProgress.completed_slides}/${resumeProgress.total_slides}에서 중단되었습니다. 이어서 생성할 수 있습니다.`
      : "이전 생성이 중단되었습니다. 이어서 생성할 수 있습니다."
    : null;


  return (
    <div className=" font-syne  pb-9">

      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
        progress={slideProgress?.value}
        progressLabel={slideProgress?.label}
        onCancel={cancelGeneration}
        cancelText="생성 취소"
      />

      <Wrapper className="flex flex-col w-full relative px-5 sm:px-10 lg:px-20 ">
        <div className="w-full mx-auto">
          <Tabs value={activeTab} onValueChange={handleTabChange} className="flex w-full flex-col">
            {/* Reserves vertical space so content does not sit under the fixed tab bar */}
            <div className="h-[4.75rem] shrink-0 sm:h-[5rem]" aria-hidden />
            <div className="fixed top-26 left-0 right-0 z-50  pb-2">
              <div className="mx-auto w-full max-w-[1440px] px-5 sm:px-10 lg:px-20">
                <TabsList className="my-4 h-auto w-fit rounded-full border border-[#EDEEEF] bg-white p-1.5">
                  <TabsTrigger
                    value={TABS.OUTLINE}
                    className="rounded-full px-5 py-2  text-xs font-medium text-[#2D2D2D] shadow-none data-[state=active]:bg-[#F4F3FF] data-[state=active]:text-[#7E3AF2] data-[state=active]:shadow-none"
                  >
                    개요 및 내용
                  </TabsTrigger>
                  <Separator orientation="vertical" className="h-6 mx-1" />
                  <TabsTrigger
                    value={TABS.LAYOUTS}
                    className="relative rounded-full px-5  py-2 text-xs font-medium text-[#2D2D2D] shadow-none  data-[state=active]:bg-[#F4F3FF] data-[state=active]:text-[#7E3AF2] data-[state=active]:shadow-none"
                  >
                    템플릿 선택
                  </TabsTrigger>
                </TabsList>
              </div>
            </div>

            {resumeBannerText && !loadingState.isLoading && (
              <div
                data-cy="resume-generation-banner"
                className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[#EDEEEF] bg-[#F4F3FF] px-5 py-3"
              >
                <p className="text-sm font-medium text-[#2D2D2D]">
                  {resumeBannerText}
                </p>
                <Button
                  onClick={() => void handleSubmit()}
                  className="h-9 rounded-full bg-[#7E3AF2] px-5 text-xs font-medium text-white shadow-none hover:bg-[#7E3AF2] hover:opacity-90"
                >
                  이어서 생성
                </Button>
              </div>
            )}

            <div className="w-full mx-auto">
              <TabsContent value={TABS.OUTLINE} className="mt-0">
                <OutlineContent
                  outlines={outlines}
                  isLoading={streamState.isLoading}
                  isStreaming={streamState.isStreaming}
                  activeSlideIndex={streamState.activeSlideIndex}
                  highestActiveIndex={streamState.highestActiveIndex}
                  onDragEnd={handleDragEnd}
                  onAddSlide={handleAddSlide}
                />
              </TabsContent>

              <TabsContent value={TABS.LAYOUTS} className="mt-0 bg-white">
                <TemplateSelection
                  selectedTemplate={selectedTemplate}
                  onSelectTemplate={setSelectedTemplate}
                />
              </TabsContent>
            </div>
          </Tabs>

          <div className="fixed bottom-[26px] right-[26px] z-50">
            <GenerateButton
              loadingState={loadingState}
              streamState={streamState}
              selectedTemplate={selectedTemplate}
              onSubmit={handleSubmit}
            />
          </div>
        </div>



      </Wrapper>
    </div>
  );
};

export default OutlinePage;
