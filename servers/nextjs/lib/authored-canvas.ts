import { parseDocumentCanvas, documentCanvasPhysicalSize, type DocumentCanvas } from "./document-canvas.ts";

export const AUTHORED_CANVAS_CONTRACT_VERSION = "authored-canvas/v1" as const;

export type AuthoredAspectRatio = "16:9" | "9:16" | "9:13" | "1:1" | "21:9";

const MIN_CANVAS_WIDTH_PX = 320;
const MAX_CANVAS_WIDTH_PX = 7_680;
const MIN_CANVAS_HEIGHT_PX = 180;
const MAX_CANVAS_HEIGHT_PX = 4_320;

export interface AuthoredCanvasContract {
  contractVersion: typeof AUTHORED_CANVAS_CONTRACT_VERSION;
  /** Display metadata only. Width and height are the authoritative geometry. */
  aspectRatio: string;
  widthPx: number;
  heightPx: number;
  locale: string;
  /** Internal compatibility view; never serialize this as legacy wire metadata. */
  documentCanvas?: DocumentCanvas;
  safeArea: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
}

const SAFE_MARGIN_PX = 64;

function preset(
  aspectRatio: AuthoredAspectRatio,
  widthPx: number,
  heightPx: number,
): AuthoredCanvasContract {
  return Object.freeze({
    contractVersion: AUTHORED_CANVAS_CONTRACT_VERSION,
    aspectRatio,
    widthPx,
    heightPx,
    locale: "ko-KR",
    safeArea: Object.freeze({
      x: SAFE_MARGIN_PX,
      y: SAFE_MARGIN_PX,
      width: widthPx - SAFE_MARGIN_PX * 2,
      height: heightPx - SAFE_MARGIN_PX * 2,
    }),
  });
}

export const AUTHORED_CANVAS_BY_ASPECT_RATIO = Object.freeze({
  "16:9": preset("16:9", 1280, 720),
  "9:16": preset("9:16", 720, 1280),
  "9:13": preset("9:13", 720, 1040),
  "1:1": preset("1:1", 720, 720),
  "21:9": preset("21:9", 1680, 720),
} satisfies Record<AuthoredAspectRatio, AuthoredCanvasContract>);

export const LEGACY_AUTHORED_CANVAS = AUTHORED_CANVAS_BY_ASPECT_RATIO["16:9"];

function isCanvasDimension(
  value: unknown,
  minimum: number,
  maximum: number,
): value is number {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= minimum &&
    value <= maximum;
}

function aspectRatioForSize(widthPx: number, heightPx: number): string {
  let dividend = widthPx;
  let divisor = heightPx;
  while (divisor) [dividend, divisor] = [divisor, dividend % divisor];
  return `${widthPx / dividend}:${heightPx / dividend}`;
}

export function isSupportedAuthoredCanvasSize(
  widthPx: unknown,
  heightPx: unknown,
): boolean {
  return isCanvasDimension(widthPx, MIN_CANVAS_WIDTH_PX, MAX_CANVAS_WIDTH_PX) &&
    isCanvasDimension(heightPx, MIN_CANVAS_HEIGHT_PX, MAX_CANVAS_HEIGHT_PX);
}

export function authoredCanvasForSize(
  widthPx: number,
  heightPx: number,
): AuthoredCanvasContract {
  if (!isSupportedAuthoredCanvasSize(widthPx, heightPx)) {
    throw new Error("authored_canvas_contract_invalid");
  }
  return Object.freeze({
    contractVersion: AUTHORED_CANVAS_CONTRACT_VERSION,
    aspectRatio: aspectRatioForSize(widthPx, heightPx),
    widthPx,
    heightPx,
    locale: "ko-KR",
    safeArea: Object.freeze({
      x: SAFE_MARGIN_PX,
      y: SAFE_MARGIN_PX,
      width: widthPx - SAFE_MARGIN_PX * 2,
      height: heightPx - SAFE_MARGIN_PX * 2,
    }),
  });
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function parseCanvas(value: Record<string, unknown>): AuthoredCanvasContract | null {
  const canvasKeys = [
    "contractVersion",
    "aspectRatio",
    "widthPx",
    "heightPx",
    "locale",
    "safeArea",
  ];
  if (
    Object.keys(value).length !== canvasKeys.length ||
    canvasKeys.some((key) => !(key in value))
  ) return null;
  const aspectRatio = value.aspectRatio;
  const safeArea = record(value.safeArea);
  const numbers = safeArea
    ? [safeArea.x, safeArea.y, safeArea.width, safeArea.height]
    : [];
  if (
    value.contractVersion !== AUTHORED_CANVAS_CONTRACT_VERSION ||
    typeof aspectRatio !== "string" ||
    aspectRatio.trim().length < 1 ||
    aspectRatio.trim().length > 32 ||
    !isSupportedAuthoredCanvasSize(value.widthPx, value.heightPx) ||
    typeof value.locale !== "string" ||
    value.locale.trim().length < 2 ||
    value.locale.trim().length > 32 ||
    !safeArea ||
    Object.keys(safeArea).length !== 4 ||
    ["x", "y", "width", "height"].some((key) => !(key in safeArea)) ||
    numbers.some((item) => typeof item !== "number" || !Number.isFinite(item)) ||
    Number(safeArea.x) < 0 ||
    Number(safeArea.y) < 0 ||
    Number(safeArea.width) <= 0 ||
    Number(safeArea.height) <= 0 ||
    Number(safeArea.x) + Number(safeArea.width) > Number(value.widthPx) ||
    Number(safeArea.y) + Number(safeArea.height) > Number(value.heightPx)
  ) return null;
  return Object.freeze({
    contractVersion: AUTHORED_CANVAS_CONTRACT_VERSION,
    aspectRatio: aspectRatio.trim(),
    widthPx: Number(value.widthPx),
    heightPx: Number(value.heightPx),
    locale: value.locale.trim(),
    safeArea: Object.freeze({
      x: Number(safeArea.x),
      y: Number(safeArea.y),
      width: Number(safeArea.width),
      height: Number(safeArea.height),
    }),
  });
}

/** Resolve the durable document canvas. Only a missing canvas is legacy 16:9. */
export function resolveAuthoredCanvas(theme: unknown): AuthoredCanvasContract {
  const root = record(theme);
  const authored = record(root?.authored_vnext);
  if (root && "documentCanvas" in root) {
    const canvas = authoredCanvasForDocument(parseDocumentCanvas(root.documentCanvas));
    if (authored && "canvas" in authored) {
      const legacy = record(authored.canvas);
      const parsed = legacy ? parseCanvas(legacy) : null;
      if (!parsed || JSON.stringify(authoredCanvasPhysicalSize(parsed)) !== JSON.stringify(authoredCanvasPhysicalSize(canvas)) ||
          parsed.widthPx !== canvas.widthPx || parsed.heightPx !== canvas.heightPx ||
          ["x", "y", "width", "height"].some(key => parsed.safeArea[key as keyof typeof parsed.safeArea] !== canvas.safeArea[key as keyof typeof canvas.safeArea])) {
        throw new Error("document_canvas_legacy_conflict");
      }
    }
    return canvas;
  }
  if (!authored || !("canvas" in authored)) return LEGACY_AUTHORED_CANVAS;
  const canvas = record(authored.canvas);
  const parsed = canvas ? parseCanvas(canvas) : null;
  if (parsed) return parsed;
  throw new Error("authored_canvas_contract_invalid");
}

export function authoredCanvasPhysicalSize(canvas: AuthoredCanvasContract): {
  widthEmu: number;
  heightEmu: number;
  widthPt: number;
  heightPt: number;
} {
  if (canvas.documentCanvas) return documentCanvasPhysicalSize(canvas.documentCanvas);
  return {
    widthEmu: canvas.widthPx * 9_525,
    heightEmu: canvas.heightPx * 9_525,
    widthPt: canvas.widthPx * 0.75,
    heightPt: canvas.heightPx * 0.75,
  };
}

export function authoredCanvasForDocument(value: DocumentCanvas): AuthoredCanvasContract {
  const documentCanvas = parseDocumentCanvas(value);
  return Object.freeze({
    contractVersion: AUTHORED_CANVAS_CONTRACT_VERSION,
    aspectRatio: aspectRatioForSize(documentCanvas.logicalSize.width, documentCanvas.logicalSize.height),
    widthPx: documentCanvas.logicalSize.width,
    heightPx: documentCanvas.logicalSize.height,
    locale: "ko-KR",
    safeArea: documentCanvas.safeArea,
    documentCanvas,
  });
}

export function canvasEmuPerUnit(canvas: AuthoredCanvasContract): number {
  return authoredCanvasPhysicalSize(canvas).widthEmu / canvas.widthPx;
}

export function canvasPointsPerUnit(canvas: AuthoredCanvasContract): number {
  return canvasEmuPerUnit(canvas) / 12700;
}
