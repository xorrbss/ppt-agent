"use client";

import React, { useMemo } from "react";

import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { readLayoutId } from "@/lib/template-v2-preview";
import { TemplateV2HtmlSlidePreview } from "../../../components/TemplateV2HtmlSlidePreview";

function createPreviewSlide(layout: TemplateV2Layout, index: number) {
  return {
    id: readLayoutId(layout, index),
    content: {},
    ui: layout,
    layout: readLayoutId(layout, index),
    layout_group: "template-v2",
  };
}

type TemplatePreviewSectionProps = {
  activeLayout: TemplateV2Layout | null;
  activeLayoutIndex: number;
};

export function TemplatePreviewSection({
  activeLayout,
  activeLayoutIndex,
}: TemplatePreviewSectionProps) {
  const previewSlide = useMemo(
    () =>
      activeLayout ? createPreviewSlide(activeLayout, activeLayoutIndex) : null,
    [activeLayout, activeLayoutIndex],
  );

  return (
    <div className="mt-6 overflow-auto rounded-2xl border border-gray-200 bg-gray-100 p-5 shadow-sm">
      <div className="relative mx-auto h-[720px] w-[1280px] overflow-hidden rounded-lg bg-white shadow">
        {previewSlide ? (
          <TemplateV2HtmlSlidePreviewMemoized slide={previewSlide} fixedSize />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            템플릿에 레이아웃이 없습니다.
          </div>
        )}
      </div>
    </div>
  );
}

// Memoized components to prevent unnecessary re-renders
const TemplateV2HtmlSlidePreviewMemoized = React.memo(
  TemplateV2HtmlSlidePreview,
);
