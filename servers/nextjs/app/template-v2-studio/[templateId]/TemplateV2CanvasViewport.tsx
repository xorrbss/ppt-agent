"use client";

import { useTranslations } from "next-intl";

import {
  CanvasCoreZoomControls,
  useCanvasCoreViewport,
} from "@/components/canvas-core/CanvasCoreViewport";
import {
  TEMPLATE_V2_SLIDE_HEIGHT,
  TEMPLATE_V2_SLIDE_WIDTH,
} from "@/lib/template-v2-konva";

/** Stable compatibility wrapper used by the existing Template V2 studio. */
export function useTemplateV2Viewport() {
  return useCanvasCoreViewport({
    slideWidth: TEMPLATE_V2_SLIDE_WIDTH,
    slideHeight: TEMPLATE_V2_SLIDE_HEIGHT,
  });
}

export function TemplateV2CanvasZoomControls({
  scale,
  zoomBy,
  fit,
}: {
  scale: number;
  zoomBy: (factor: number) => void;
  fit: () => void;
}) {
  const t = useTranslations("studio");
  return (
    <CanvasCoreZoomControls
      scale={scale}
      zoomBy={zoomBy}
      fit={fit}
      zoomOutLabel={t("canvas.zoomOut")}
      zoomInLabel={t("canvas.zoomIn")}
      fitLabel={t("canvas.fitSlideToView")}
      fitText={t("canvas.fit")}
    />
  );
}
