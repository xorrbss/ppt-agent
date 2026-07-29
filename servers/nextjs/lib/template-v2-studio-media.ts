import {
  cropImageFrameForDraft,
  normalizeImageCropScale,
  normalizeImageFocusPoint,
  type ImageFit,
} from "../components/slide-editor/images/imageCrop.ts";
import type { ElementPath } from "./template-v2-studio.ts";

export type StudioImagePlacement = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type StudioCanvasRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type TemplateV2CanvasSelectionBounds = {
  left: number;
  top: number;
  width: number;
  height: number;
};

export interface TemplateV2CanvasCropDraft {
  path: ElementPath;
  focusX: number;
  focusY: number;
  cropScale: number;
}

export function closestStudioPath<T extends string | number>(
  path: readonly T[],
  hasPath: (candidate: T[]) => boolean
): T[] | null {
  for (let length = path.length; length > 0; length -= 1) {
    const candidate = path.slice(0, length);
    if (hasPath(candidate)) return candidate;
  }
  return null;
}

const finiteNumber = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

export function normalizeStudioImageFit(value: unknown): ImageFit {
  if (value === "cover" || value === "contain") return value;
  // Some older templates call the non-proportional mode "stretch".
  return "fill";
}

export function studioImagePlacement({
  naturalWidth,
  naturalHeight,
  width,
  height,
  fit,
  focusX,
  focusY,
  cropScale,
}: {
  naturalWidth: number;
  naturalHeight: number;
  width: number;
  height: number;
  fit: unknown;
  focusX?: unknown;
  focusY?: unknown;
  cropScale?: unknown;
}): StudioImagePlacement {
  const safeWidth = Math.max(0, finiteNumber(width) ?? 0);
  const safeHeight = Math.max(0, finiteNumber(height) ?? 0);
  const safeNaturalWidth = Math.max(1, finiteNumber(naturalWidth) ?? 1);
  const safeNaturalHeight = Math.max(1, finiteNumber(naturalHeight) ?? 1);
  const normalizedFit = normalizeStudioImageFit(fit);
  const focus = normalizeImageFocusPoint({
    x: finiteNumber(focusX) ?? undefined,
    y: finiteNumber(focusY) ?? undefined,
  });

  if (normalizedFit === "fill") {
    return { x: 0, y: 0, width: safeWidth, height: safeHeight };
  }

  if (normalizedFit === "cover") {
    const frame = cropImageFrameForDraft(
      { left: 0, top: 0, width: safeWidth, height: safeHeight },
      { width: safeNaturalWidth, height: safeNaturalHeight },
      {
        x: focus.x,
        y: focus.y,
        scale: normalizeImageCropScale(cropScale),
      }
    );
    return {
      x: frame.left,
      y: frame.top,
      width: frame.width,
      height: frame.height,
    };
  }

  const scale = Math.min(
    safeWidth / safeNaturalWidth,
    safeHeight / safeNaturalHeight
  );
  const renderedWidth = safeNaturalWidth * scale;
  const renderedHeight = safeNaturalHeight * scale;
  return {
    x: (safeWidth - renderedWidth) * (focus.x / 100),
    y: (safeHeight - renderedHeight) * (focus.y / 100),
    width: renderedWidth,
    height: renderedHeight,
  };
}

export function unionStudioCanvasRects(
  rects: readonly StudioCanvasRect[]
): StudioCanvasRect | null {
  const valid = rects.filter(
    (rect) =>
      Number.isFinite(rect.x) &&
      Number.isFinite(rect.y) &&
      Number.isFinite(rect.width) &&
      Number.isFinite(rect.height) &&
      rect.width >= 0 &&
      rect.height >= 0
  );
  if (valid.length === 0) return null;
  const left = Math.min(...valid.map((rect) => rect.x));
  const top = Math.min(...valid.map((rect) => rect.y));
  const right = Math.max(...valid.map((rect) => rect.x + rect.width));
  const bottom = Math.max(...valid.map((rect) => rect.y + rect.height));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

export function clampStudioSelectionBounds(
  rect: StudioCanvasRect | null,
  container: { width: number; height: number }
): TemplateV2CanvasSelectionBounds | null {
  if (!rect) return null;
  const maxWidth = Math.max(0, finiteNumber(container.width) ?? 0);
  const maxHeight = Math.max(0, finiteNumber(container.height) ?? 0);
  const left = clamp(rect.x, 0, maxWidth);
  const top = clamp(rect.y, 0, maxHeight);
  const right = clamp(rect.x + rect.width, 0, maxWidth);
  const bottom = clamp(rect.y + rect.height, 0, maxHeight);
  if (right <= left || bottom <= top) return null;
  return { left, top, width: right - left, height: bottom - top };
}

export function sameStudioSelectionBounds(
  left: TemplateV2CanvasSelectionBounds | null,
  right: TemplateV2CanvasSelectionBounds | null,
  tolerance = 0.25
): boolean {
  if (!left || !right) return left === right;
  return (
    Math.abs(left.left - right.left) <= tolerance &&
    Math.abs(left.top - right.top) <= tolerance &&
    Math.abs(left.width - right.width) <= tolerance &&
    Math.abs(left.height - right.height) <= tolerance
  );
}

export function recolorStudioSvgMarkup(markup: string, color: string): string {
  const normalized = color.trim();
  if (!normalized || !markup.trim().startsWith("<")) return markup;
  return markup
    .replace(
      /\b(fill|stroke)=("|')(?!none\b|transparent\b|url\()[^"']*\2/gi,
      (_match, property: string, quote: string) =>
        `${property}=${quote}${normalized}${quote}`
    )
    .replace(
      /\b(fill|stroke)\s*:\s*(?!none\b|transparent\b|url\()[^;"'}]+/gi,
      (_match, property: string) => `${property}:${normalized}`
    );
}
