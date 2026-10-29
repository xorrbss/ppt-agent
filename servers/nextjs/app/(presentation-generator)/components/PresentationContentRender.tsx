"use client";

import React from "react";
import { useTranslations } from "next-intl";
import { useSelector } from "react-redux";

import type { PresentationDriverId } from "@/lib/presentation-profile";
import { resolveAuthoredCanvas, type AuthoredCanvasContract } from "@/lib/authored-canvas";
import type { RootState } from "@/store/store";
import { resolveAuthoredImageUrl } from "@/utils/api";

import { AuthoredSlidePreview } from "./AuthoredSlidePreview";
import { SvgSlidePreview } from "./SvgSlidePreview";
import SlideErrorBoundary from "./SlideErrorBoundary";
import { inferSlideDriver } from "../presentation/utils/isAuthoredPresentation";

type SlideRecord = Record<string, any>;

type PresentationContentRenderProps = {
  slide: SlideRecord;
  preferAuthoredHtml?: boolean;
  resolveSvgAssets?: boolean;
  driverId?: PresentationDriverId;
  canvas?: AuthoredCanvasContract;
};

type DriverRendererProps = PresentationContentRenderProps & {
  fonts?: unknown;
};

function AuthoredContent({
  slide,
  preferAuthoredHtml = false,
  canvas,
}: DriverRendererProps) {
  const t = useTranslations("a2cPreview");
  const imageRef = slide?.content?.image || slide?.properties?.image;
  const imageSrc = resolveAuthoredImageUrl(imageRef);

  return (
    <SlideErrorBoundary label={t("slide", { number: slide.index + 1 })}>
      <AuthoredSlidePreview
        slideIndex={slide.index}
        html={slide.html_content}
        imageSrc={imageSrc}
        preferHtml={preferAuthoredHtml}
        canvas={canvas}
      />
    </SlideErrorBoundary>
  );
}

function SvgContent({ slide, canvas, resolveSvgAssets }: DriverRendererProps) {
  const t = useTranslations("a2cPreview");
  const imageRef = slide?.content?.image || slide?.properties?.image;
  const manifest = slide?.content?.svgAssetManifest;
  const fixedGlyphPreview = slide?.content?.svgReceipt?.fixedGlyphPreview;
  const revision = fixedGlyphPreview?.version === "svg-fixed-glyph-preview/v1"
    ? (manifest?.slideRevision ?? fixedGlyphPreview?.canonicalSvgDigest)
    : (manifest?.slideRevision ?? slide?.content?.svgReceipt?.source_digest ?? slide?.svg_content);
  return <SlideErrorBoundary label={t("slide", { number: slide.index + 1 })}><SvgSlidePreview slideIndex={slide.index} slideId={slide.id} svg={slide.svg_content} resolveAssets={resolveSvgAssets} revision={revision} imageSrc={resolveAuthoredImageUrl(imageRef)} canvas={canvas} fixedGlyphPreview={fixedGlyphPreview} /></SlideErrorBoundary>;
}

const PRESENTATION_CONTENT_RENDERER_REGISTRY = Object.freeze({
  authored: AuthoredContent,
  "svg-v1": SvgContent,
} satisfies Record<PresentationDriverId, React.ComponentType<DriverRendererProps>>);

export function PresentationContentRender({
  slide,
  resolveSvgAssets = false,
  preferAuthoredHtml = false,
  driverId,
  canvas,
}: PresentationContentRenderProps) {
  const t = useTranslations("a2cPreview");
  const presentationData = useSelector(
    (state: RootState) => state.presentationGeneration.presentationData,
  );
  const issuedDriver = presentationData?.profile?.driver_id;
  const resolvedDriver =
    driverId ??
    (issuedDriver === "authored" || issuedDriver === "svg-v1"
      ? issuedDriver
      : inferSlideDriver(slide));

  if (!resolvedDriver) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">
        {t("unsupported")}
      </div>
    );
  }

  const Renderer = PRESENTATION_CONTENT_RENDERER_REGISTRY[resolvedDriver];
  const authoredCanvas = (resolvedDriver === "authored" || resolvedDriver === "svg-v1")
    ? canvas ?? resolveAuthoredCanvas(presentationData?.theme)
    : undefined;
  return (
    <Renderer
      slide={slide}
      fonts={presentationData?.fonts}
      preferAuthoredHtml={preferAuthoredHtml}
      resolveSvgAssets={resolveSvgAssets}
      driverId={resolvedDriver}
      canvas={authoredCanvas}
    />
  );
}
