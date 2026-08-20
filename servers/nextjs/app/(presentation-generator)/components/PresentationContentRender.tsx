"use client";

import React from "react";
import { useSelector } from "react-redux";

import type { PresentationDriverId } from "@/lib/presentation-profile";
import type { RootState } from "@/store/store";
import { resolveAuthoredImageUrl } from "@/utils/api";

import { AuthoredSlidePreview } from "./AuthoredSlidePreview";
import SlideErrorBoundary from "./SlideErrorBoundary";
import {
  shouldRenderTemplateV2HtmlPreview,
  TemplateV2HtmlSlidePreview,
} from "./TemplateV2HtmlSlidePreview";

type SlideRecord = Record<string, any>;

type PresentationContentRenderProps = {
  slide: SlideRecord;
  preferAuthoredHtml?: boolean;
  driverId?: PresentationDriverId;
};

type DriverRendererProps = PresentationContentRenderProps & {
  fonts?: unknown;
};

function AuthoredContent({
  slide,
  preferAuthoredHtml = false,
}: DriverRendererProps) {
  const imageRef = slide?.content?.image || slide?.properties?.image;
  const imageSrc = resolveAuthoredImageUrl(imageRef);

  return (
    <SlideErrorBoundary label={`Slide ${slide.index + 1}`}>
      <AuthoredSlidePreview
        slideIndex={slide.index}
        html={slide.html_content}
        imageSrc={imageSrc}
        preferHtml={preferAuthoredHtml}
      />
    </SlideErrorBoundary>
  );
}

function TemplateV2Content({ slide, fonts }: DriverRendererProps) {
  return (
    <SlideErrorBoundary label={`Slide ${slide.index + 1}`}>
      <TemplateV2HtmlSlidePreview slide={slide} fonts={fonts} fixedSize />
    </SlideErrorBoundary>
  );
}

const PRESENTATION_CONTENT_RENDERER_REGISTRY = Object.freeze({
  authored: AuthoredContent,
  "template-v2": TemplateV2Content,
} satisfies Record<PresentationDriverId, React.ComponentType<DriverRendererProps>>);

function inferSupportedDriver(
  slide: SlideRecord,
  presentationVersion: unknown,
): PresentationDriverId | null {
  if (
    slide?.content?.__authored__ === true ||
    slide?.layout_group === "authored"
  ) {
    return "authored";
  }
  if (shouldRenderTemplateV2HtmlPreview(slide, presentationVersion)) {
    return "template-v2";
  }
  return null;
}

export function PresentationContentRender({
  slide,
  preferAuthoredHtml = false,
  driverId,
}: PresentationContentRenderProps) {
  const presentationData = useSelector(
    (state: RootState) => state.presentationGeneration.presentationData,
  );
  const issuedDriver = presentationData?.profile?.driver_id;
  const resolvedDriver =
    driverId ??
    (issuedDriver === "authored" || issuedDriver === "template-v2"
      ? issuedDriver
      : inferSupportedDriver(slide, presentationData?.version));

  if (!resolvedDriver) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">
        Unsupported presentation content.
      </div>
    );
  }

  const Renderer = PRESENTATION_CONTENT_RENDERER_REGISTRY[resolvedDriver];
  return (
    <Renderer
      slide={slide}
      fonts={presentationData?.fonts}
      preferAuthoredHtml={preferAuthoredHtml}
      driverId={resolvedDriver}
    />
  );
}
