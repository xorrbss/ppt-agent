"use client";

import { useEffect, useState } from "react";
import type Konva from "konva";

import {
  elementCapabilities,
  numberValue,
  stringValue,
  type TemplateV2TextRun,
} from "@/lib/template-v2-konva";
import {
  isJsonRecord,
  type ElementPath,
  type JsonRecord,
} from "@/lib/template-v2-studio";
import {
  recolorStudioSvgMarkup,
  studioImagePlacement,
  type TemplateV2CanvasCropDraft,
} from "@/lib/template-v2-studio-media";
import { isTemplateV2AdditiveSelectionModifier } from "@/lib/template-v2-studio-keyboard";

export const CONTENT_PADDING = 8;

export interface StudioElementProps {
  element: JsonRecord;
  path: ElementPath;
  /**
   * Changes whenever selection, locks, or the global disable can alter what
   * `isDisabled` answers. `isDisabled` itself keeps a stable identity, so this
   * is what tells a memoized element that its interactive state may have moved.
   */
  interactionKey: string;
  cropDraft?: TemplateV2CanvasCropDraft | null;
  isDisabled(path: ElementPath): boolean;
  setNode(path: ElementPath, node: Konva.Node | null): void;
  onSelect(path: ElementPath, additive?: boolean): void;
  onDragStart(path: ElementPath, node: Konva.Node): void;
  onDragMove(path: ElementPath, node: Konva.Node): void;
  onDragEnd(path: ElementPath, element: JsonRecord, node: Konva.Node): void;
}

export function interactionProps({
  element,
  path,
  isDisabled,
  setNode,
  onSelect,
  onDragStart,
  onDragMove,
  onDragEnd,
}: StudioElementProps) {
  const capabilities = elementCapabilities(element);
  return {
    ref: (node: Konva.Node | null) => setNode(path, node),
    draggable: !isDisabled(path) && capabilities.move,
    onClick: (event: Konva.KonvaEventObject<MouseEvent>) => {
      event.cancelBubble = true;
      onSelect(path, isTemplateV2AdditiveSelectionModifier(event.evt));
    },
    onTap: (event: Konva.KonvaEventObject<TouchEvent>) => {
      event.cancelBubble = true;
      onSelect(path, false);
    },
    onDragStart: (event: Konva.KonvaEventObject<DragEvent>) => {
      event.cancelBubble = true;
      onDragStart(path, event.currentTarget);
    },
    onDragMove: (event: Konva.KonvaEventObject<DragEvent>) => {
      event.cancelBubble = true;
      onDragMove(path, event.currentTarget);
    },
    onDragEnd: (event: Konva.KonvaEventObject<DragEvent>) => {
      event.cancelBubble = true;
      onDragEnd(path, element, event.currentTarget);
    },
  };
}

export function fillColor(element: JsonRecord): string {
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  return stringValue(fill.color, "#dbeafe");
}

export function fillOpacity(element: JsonRecord): number {
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  return numberValue(fill.opacity, 1);
}

export function fillProps(element: JsonRecord, fallback = "#dbeafe") {
  return {
    fill: stringValue(
      isJsonRecord(element.fill) ? element.fill.color : undefined,
      fallback
    ),
    fillOpacity: fillOpacity(element),
  };
}

export function strokeProps(
  element: JsonRecord,
  fallbackColor = "#2563eb",
  fallbackWidth = 1
) {
  const stroke = isJsonRecord(element.stroke) ? element.stroke : {};
  return {
    stroke: stringValue(stroke.color, fallbackColor),
    strokeWidth: numberValue(stroke.width, fallbackWidth),
    strokeOpacity: numberValue(stroke.opacity, 1),
    dash: Array.isArray(stroke.dash)
      ? stroke.dash.filter(
          (item): item is number =>
            typeof item === "number" && Number.isFinite(item) && item >= 0
        )
      : undefined,
  };
}

export function elementShadowProps(element: JsonRecord) {
  const shadow = isJsonRecord(element.shadow) ? element.shadow : null;
  if (!shadow) return {};
  return {
    shadowColor: stringValue(shadow.color, "#000000"),
    shadowBlur: Math.max(0, numberValue(shadow.blur, 0)),
    shadowOpacity: numberValue(shadow.opacity, 1),
    shadowOffsetX: numberValue(shadow.offset_x ?? shadow.offsetX, 0),
    shadowOffsetY: numberValue(shadow.offset_y ?? shadow.offsetY, 0),
  };
}

export function cornerRadius(element: JsonRecord): number[] | number {
  const radius = isJsonRecord(element.border_radius)
    ? element.border_radius
    : null;
  return radius
    ? [
        numberValue(radius.tl, 0),
        numberValue(radius.tr, 0),
        numberValue(radius.br, 0),
        numberValue(radius.bl, 0),
      ]
    : 0;
}

export function measureTextRun(run: TemplateV2TextRun): number {
  if (typeof document === "undefined") {
    return Array.from(run.text).length * run.fontSize * 0.6;
  }
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return Array.from(run.text).length * run.fontSize * 0.6;
  context.font = `${run.fontStyle} ${run.fontSize}px ${run.fontFamily}`;
  const glyphWidth = context.measureText(run.text).width;
  const gaps = Math.max(0, Array.from(run.text).length - 1);
  return glyphWidth + gaps * run.letterSpacing;
}

export function useTemplateImage(
  source: unknown,
  iconColor?: string | null
): HTMLImageElement | null {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    setImage(null);
    if (
      typeof source !== "string" ||
      (!source.startsWith("/") &&
        !/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(source) &&
        !/^data:image\/svg\+xml[;,]/i.test(source))
    ) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    const next = new window.Image();
    next.onload = () => {
      if (!cancelled) setImage(next);
    };
    next.onerror = () => {
      if (!cancelled) setImage(null);
    };

    const load = async () => {
      let resolvedSource = source;
      const shouldRecolor =
        Boolean(iconColor?.trim()) &&
        (/\.svg(?:[?#]|$)/i.test(source) ||
          /^data:image\/svg\+xml[;,]/i.test(source));
      if (shouldRecolor) {
        try {
          const response = await fetch(source);
          if (!response.ok) throw new Error("Could not load icon SVG");
          const markup = recolorStudioSvgMarkup(
            await response.text(),
            iconColor ?? ""
          );
          objectUrl = URL.createObjectURL(
            new Blob([markup], { type: "image/svg+xml" })
          );
          resolvedSource = objectUrl;
        } catch {
          // The original source remains a safe fallback for cross-origin icons.
        }
      }
      if (!cancelled) next.src = resolvedSource;
    };
    void load();
    return () => {
      cancelled = true;
      next.onload = null;
      next.onerror = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [iconColor, source]);
  return image;
}

export function imagePlacement(
  image: HTMLImageElement,
  width: number,
  height: number,
  fit: unknown,
  focusX?: unknown,
  focusY?: unknown,
  cropScale?: unknown
) {
  return studioImagePlacement({
    naturalWidth: image.naturalWidth || image.width,
    naturalHeight: image.naturalHeight || image.height,
    width,
    height,
    fit,
    focusX,
    focusY,
    cropScale,
  });
}

export function clipRoundedRect(
  context: Konva.Context,
  width: number,
  height: number,
  radius: number | number[]
) {
  const values = Array.isArray(radius)
    ? radius
    : [radius, radius, radius, radius];
  const maxRadius = Math.max(0, Math.min(width, height) / 2);
  const [tl, tr, br, bl] = values.map((value) =>
    Math.min(maxRadius, Math.max(0, value ?? 0))
  );
  context.beginPath();
  context.moveTo(tl, 0);
  context.lineTo(width - tr, 0);
  context.quadraticCurveTo(width, 0, width, tr);
  context.lineTo(width, height - br);
  context.quadraticCurveTo(width, height, width - br, height);
  context.lineTo(bl, height);
  context.quadraticCurveTo(0, height, 0, height - bl);
  context.lineTo(0, tl);
  context.quadraticCurveTo(0, 0, tl, 0);
  context.closePath();
}
