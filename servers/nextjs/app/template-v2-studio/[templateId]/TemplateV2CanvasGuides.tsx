"use client";

import { CanvasCoreGuides } from "@/components/canvas-core/CanvasCoreGuides";
import type { TemplateV2Guide } from "@/lib/template-v2-snapping";

/** Stable Template V2 wrapper around the shared Konva guide layer. */
export function TemplateV2CanvasGuides({
  guides,
  offset,
  scale,
}: {
  guides: TemplateV2Guide[];
  offset: { x: number; y: number };
  scale: number;
}) {
  return <CanvasCoreGuides guides={guides} offset={offset} scale={scale} />;
}
