import { getApiUrl } from "@/utils/api";
import { isRecord } from "@/lib/guards";

/** Pixel canvas shared by every slide image of one sample deck. */
export interface AuthoredStyleSampleCanvas {
  width: number;
  height: number;
}

export interface AuthoredStyleSampleSlide {
  /** 1-based slide number; the same number means the same demo page across styles. */
  index: number;
  title: string;
  thumbUrl: string;
  fullUrl: string;
}

export interface AuthoredStyleSamples {
  canvas: AuthoredStyleSampleCanvas;
  slides: AuthoredStyleSampleSlide[];
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

/**
 * Sample images are served by the backend under `/api/v1/`. Only same-origin
 * paths are accepted so the session cookie the image route requires is sent
 * and a catalogue entry can never point the dialog at another host.
 */
function normalizeSamplePath(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const path = value.trim();
  if (!path.startsWith("/") || path.startsWith("//")) return null;
  return getApiUrl(path);
}

function normalizeCanvas(value: unknown): AuthoredStyleSampleCanvas | null {
  if (!isRecord(value)) return null;
  const { width, height } = value;
  if (!isPositiveInteger(width) || !isPositiveInteger(height)) return null;
  return { width, height };
}

function normalizeSlide(value: unknown): AuthoredStyleSampleSlide | null {
  if (!isRecord(value)) return null;
  const { index, title } = value;
  if (!isPositiveInteger(index)) return null;
  if (typeof title !== "string" || !title.trim()) return null;
  const thumbUrl = normalizeSamplePath(value.thumb);
  const fullUrl = normalizeSamplePath(value.full);
  if (!thumbUrl || !fullUrl) return null;
  return { index, title: title.trim(), thumbUrl, fullUrl };
}

/**
 * Normalises the optional `samples` object of one catalogue entry. Malformed
 * slides are dropped individually; an invalid canvas or an empty deck yields
 * `undefined`, so callers can keep the "attach only when present" rule.
 */
export function normalizeAuthoredStyleSamples(
  value: unknown,
): AuthoredStyleSamples | undefined {
  if (!isRecord(value)) return undefined;
  const canvas = normalizeCanvas(value.canvas);
  if (!canvas || !Array.isArray(value.slides)) return undefined;

  const seenIndices = new Set<number>();
  const slides: AuthoredStyleSampleSlide[] = [];
  for (const entry of value.slides) {
    const slide = normalizeSlide(entry);
    if (!slide || seenIndices.has(slide.index)) continue;
    seenIndices.add(slide.index);
    slides.push(slide);
  }
  if (slides.length === 0) return undefined;

  slides.sort((left, right) => left.index - right.index);
  return { canvas, slides };
}
