import { isJsonRecord, type ElementGeometry, type JsonRecord } from "./template-v2-studio.ts";

export const TEMPLATE_V2_SLIDE_WIDTH = 1280;
export const TEMPLATE_V2_SLIDE_HEIGHT = 720;
export const MIN_TEMPLATE_V2_ZOOM = 0.25;
export const MAX_TEMPLATE_V2_ZOOM = 4;

export interface ViewportTransform {
  scale: number;
  x: number;
  y: number;
}

export interface ElementCapabilities {
  move: boolean;
  resize: boolean;
  rotate: boolean;
}

export interface TemplateV2TextRunStyle {
  fontSize: number;
  fontFamily: string;
  fontStyle: string;
  fill: string;
  opacity: number;
  lineHeight: number;
  letterSpacing: number;
  textDecoration: string;
}

export interface TemplateV2TextRun extends TemplateV2TextRunStyle {
  index: number;
  text: string;
}

export type TemplateV2TextLayout =
  | {
      mode: "runs";
      runs: Array<TemplateV2TextRun & { x: number; width: number }>;
    }
  | {
      mode: "fallback";
      reason: "no-runs" | "multiline" | "overflow";
      text: string;
      style: TemplateV2TextRunStyle;
    };

export function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

export function elementPosition(element: JsonRecord) {
  const value = isJsonRecord(element.position) ? element.position : {};
  return { x: numberValue(value.x, 0), y: numberValue(value.y, 0) };
}

export function elementSize(
  element: JsonRecord,
  fallback = { width: 240, height: 80 }
) {
  const value = isJsonRecord(element.size) ? element.size : {};
  return {
    width: Math.max(1, numberValue(value.width, fallback.width)),
    height: Math.max(1, numberValue(value.height, fallback.height)),
  };
}

export function elementRotation(element: JsonRecord): number {
  return element.type === "group" ? 0 : numberValue(element.rotation, 0);
}

export function elementCapabilities(element: JsonRecord): ElementCapabilities {
  if (element.type === "group") {
    return { move: true, resize: false, rotate: false };
  }
  if (["text", "container", "image"].includes(String(element.type))) {
    return { move: true, resize: true, rotate: true };
  }
  return { move: false, resize: false, rotate: false };
}

export function roundTemplateV2Value(value: number): number {
  return Math.round(value * 100) / 100;
}

export function normalizeRotation(value: number): number {
  const normalized = ((value % 360) + 360) % 360;
  return roundTemplateV2Value(normalized);
}

export function normalizeElementGeometry(
  element: JsonRecord,
  value: {
    x: number;
    y: number;
    width?: number;
    height?: number;
    scaleX?: number;
    scaleY?: number;
    rotation?: number;
  }
): ElementGeometry {
  const capabilities = elementCapabilities(element);
  const geometry: ElementGeometry = {
    x: roundTemplateV2Value(value.x),
    y: roundTemplateV2Value(value.y),
  };
  if (
    capabilities.resize &&
    value.width !== undefined &&
    value.height !== undefined
  ) {
    geometry.width = roundTemplateV2Value(
      Math.max(8, value.width * (value.scaleX ?? 1))
    );
    geometry.height = roundTemplateV2Value(
      Math.max(8, value.height * (value.scaleY ?? 1))
    );
  }
  if (capabilities.rotate && value.rotation !== undefined) {
    geometry.rotation = normalizeRotation(value.rotation);
  }
  return geometry;
}

export function fitTemplateV2Viewport(
  width: number,
  height: number,
  padding = 24
): ViewportTransform {
  const usableWidth = Math.max(1, width - padding * 2);
  const usableHeight = Math.max(1, height - padding * 2);
  const scale = Math.min(
    1,
    usableWidth / TEMPLATE_V2_SLIDE_WIDTH,
    usableHeight / TEMPLATE_V2_SLIDE_HEIGHT
  );
  return {
    scale,
    x: (width - TEMPLATE_V2_SLIDE_WIDTH * scale) / 2,
    y: (height - TEMPLATE_V2_SLIDE_HEIGHT * scale) / 2,
  };
}

export function preserveTemplateV2ViewportOnResize(
  viewport: ViewportTransform,
  previous: { width: number; height: number },
  next: { width: number; height: number }
): ViewportTransform {
  if (
    viewport.scale <= 0 ||
    previous.width <= 0 ||
    previous.height <= 0 ||
    next.width <= 0 ||
    next.height <= 0
  ) {
    return fitTemplateV2Viewport(next.width, next.height);
  }
  const logicalCenter = {
    x: (previous.width / 2 - viewport.x) / viewport.scale,
    y: (previous.height / 2 - viewport.y) / viewport.scale,
  };
  return {
    scale: viewport.scale,
    x: next.width / 2 - logicalCenter.x * viewport.scale,
    y: next.height / 2 - logicalCenter.y * viewport.scale,
  };
}

export function zoomTemplateV2Viewport(
  viewport: ViewportTransform,
  pointer: { x: number; y: number },
  requestedScale: number
): ViewportTransform {
  const scale = Math.min(
    MAX_TEMPLATE_V2_ZOOM,
    Math.max(MIN_TEMPLATE_V2_ZOOM, requestedScale)
  );
  const logical = {
    x: (pointer.x - viewport.x) / viewport.scale,
    y: (pointer.y - viewport.y) / viewport.scale,
  };
  return {
    scale,
    x: pointer.x - logical.x * scale,
    y: pointer.y - logical.y * scale,
  };
}

export function pathKey(path: Array<string | number>): string {
  return path.map((part) => `${typeof part}:${String(part)}`).join("/");
}

function textFont(element: JsonRecord, run?: JsonRecord): TemplateV2TextRunStyle {
  const elementFont = isJsonRecord(element.font) ? element.font : {};
  const runFont = run && isJsonRecord(run.font) ? run.font : {};
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  const inheritedNumber = (key: string, fallback: number) =>
    numberValue(runFont[key], numberValue(elementFont[key], fallback));
  const inheritedString = (key: string, fallback: string) =>
    stringValue(runFont[key], stringValue(elementFont[key], fallback));
  const inheritedBoolean = (key: string) =>
    typeof runFont[key] === "boolean"
      ? runFont[key] === true
      : elementFont[key] === true;
  const styles = [
    inheritedBoolean("bold") ? "bold" : "",
    inheritedBoolean("italic") ? "italic" : "",
  ].filter(Boolean);
  return {
    fontSize: Math.max(6, inheritedNumber("size", 28)),
    fontFamily: inheritedString("family", "Arial"),
    fontStyle: styles.join(" ") || "normal",
    fill: inheritedString("color", stringValue(fill.color, "#0f172a")),
    opacity: inheritedNumber(
      "opacity",
      numberValue(fill.opacity, numberValue(element.opacity, 1))
    ),
    lineHeight: inheritedNumber("line_height", 1.2),
    letterSpacing: inheritedNumber("letter_spacing", 0),
    textDecoration: inheritedBoolean("underline") ? "underline" : "",
  };
}

export function layoutTemplateV2TextRuns(
  element: JsonRecord,
  availableWidth: number,
  measureText: (run: TemplateV2TextRun) => number
): TemplateV2TextLayout {
  const rawRuns = Array.isArray(element.runs)
    ? element.runs.filter(isJsonRecord)
    : [];
  const runs = rawRuns.map((run, index): TemplateV2TextRun => ({
    index,
    text: stringValue(run.text, ""),
    ...textFont(element, run),
  }));
  const text = runs.map((run) => run.text).join("");
  const fallback = (
    reason: Extract<TemplateV2TextLayout, { mode: "fallback" }>["reason"]
  ): TemplateV2TextLayout => ({
    mode: "fallback",
    reason,
    text,
    style: textFont(element),
  });

  if (runs.length === 0) return fallback("no-runs");
  if (runs.some((run) => /[\r\n]/.test(run.text))) {
    return fallback("multiline");
  }

  let x = 0;
  const positioned = runs.map((run) => {
    const measured = measureText(run);
    const width = Number.isFinite(measured) ? Math.max(0, measured) : 0;
    const positionedRun = { ...run, x, width };
    x += width;
    return positionedRun;
  });
  if (x > Math.max(0, availableWidth)) return fallback("overflow");
  return { mode: "runs", runs: positioned };
}
