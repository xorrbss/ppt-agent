"use client";

import { useEffect, useState } from "react";

import {
  numberValue,
  stringValue,
  type TemplateV2TextRun,
} from "@/lib/template-v2-konva";
import { isJsonRecord, type JsonRecord } from "@/lib/template-v2-studio";

export const CONTENT_PADDING = 8;

export function fillColor(element: JsonRecord): string {
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  return stringValue(fill.color, "#dbeafe");
}

export function fillOpacity(element: JsonRecord): number {
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  return numberValue(fill.opacity, 1);
}

export function strokeProps(element: JsonRecord) {
  const stroke = isJsonRecord(element.stroke) ? element.stroke : {};
  return {
    stroke: stringValue(stroke.color, "#2563eb"),
    strokeWidth: numberValue(stroke.width, 1),
    dash: Array.isArray(stroke.dash)
      ? stroke.dash.filter((item): item is number => typeof item === "number")
      : undefined,
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

export function useTemplateImage(source: unknown): HTMLImageElement | null {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    setImage(null);
    if (
      typeof source !== "string" ||
      (!source.startsWith("/") &&
        !/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(source))
    ) {
      return;
    }
    const next = new window.Image();
    next.onload = () => setImage(next);
    next.onerror = () => setImage(null);
    next.src = source;
    return () => {
      next.onload = null;
      next.onerror = null;
    };
  }, [source]);
  return image;
}

export function imagePlacement(
  image: HTMLImageElement,
  width: number,
  height: number,
  fit: unknown
) {
  if (fit === "contain") {
    const scale = Math.min(width / image.width, height / image.height);
    const renderedWidth = image.width * scale;
    const renderedHeight = image.height * scale;
    return {
      x: (width - renderedWidth) / 2,
      y: (height - renderedHeight) / 2,
      width: renderedWidth,
      height: renderedHeight,
    };
  }
  if (fit === "cover") {
    const scale = Math.max(width / image.width, height / image.height);
    const cropWidth = width / scale;
    const cropHeight = height / scale;
    return {
      x: 0,
      y: 0,
      width,
      height,
      crop: {
        x: (image.width - cropWidth) / 2,
        y: (image.height - cropHeight) / 2,
        width: cropWidth,
        height: cropHeight,
      },
    };
  }
  return { x: 0, y: 0, width, height };
}
