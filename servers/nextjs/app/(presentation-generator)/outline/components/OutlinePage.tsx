"use client";

import React, { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RootState } from "@/store/store";
import { useSelector } from "react-redux";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import Wrapper from "@/components/Wrapper";
import OutlineContent from "./OutlineContent";
import LayoutSelection from "./LayoutSelection";
import EmptyStateView from "./EmptyStateView";
import PageHeader from "./PageHeader";
import GenerateButton from "./GenerateButton";

import { TABS, LayoutGroup } from "../types/index";
import { useOutlineStreaming } from "../hooks/useOutlineStreaming";
import { useOutlineManagement } from "../hooks/useOutlineManagement";
import { usePresentationGeneration } from "../hooks/usePresentationGeneration";

const OutlinePage: React.FC = () => {
  const { presentation_id, outlines } = useSelector(
    (state: RootState) => state.presentationGeneration
  );

  const [activeTab, setActiveTab] = useState<string>(TABS.OUTLINE);
  const [selectedLayoutGroup, setSelectedLayoutGroup] = useState<LayoutGroup | null>(null);

  // Custom hooks
  const streamState = useOutlineStreaming(presentation_id);
  const { handleDragEnd, handleAddSlide } = useOutlineManagement(outlines);
  const { loadingState, handleSubmit } = usePresentationGeneration(
    presentation_id,
    outlines,
    selectedLayoutGroup
  );

  if (!presentation_id) {
    return <EmptyStateView />;
  }

  return (
    <Wrapper>
      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
      />

      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 pb-6">
        <div className="mt-4 sm:mt-8">
          <PageHeader />

          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList className="grid w-[50%] mx-auto grid-cols-2">
              <TabsTrigger value={TABS.OUTLINE}>Outline & Content</TabsTrigger>
              <TabsTrigger value={TABS.LAYOUTS}>Layout Style</TabsTrigger>
            </TabsList>

            <TabsContent value={TABS.OUTLINE} className="mt-6">
              <OutlineContent
                outlines={outlines}
                isLoading={streamState.isLoading}
                isStreaming={streamState.isStreaming}
                onDragEnd={handleDragEnd}
                onAddSlide={handleAddSlide}
              />
            </TabsContent>

            <TabsContent value={TABS.LAYOUTS} className="mt-6">
              <LayoutSelection
                selectedLayoutGroup={selectedLayoutGroup}
                onSelectLayoutGroup={setSelectedLayoutGroup}
              />
            </TabsContent>
          </Tabs>

          <GenerateButton
            loadingState={loadingState}
            streamState={streamState}
            outlines={outlines}
            selectedLayoutGroup={selectedLayoutGroup}
            onSubmit={handleSubmit}
          />
        </div>
      </div>
    </Wrapper>
  );
};

export default OutlinePage;