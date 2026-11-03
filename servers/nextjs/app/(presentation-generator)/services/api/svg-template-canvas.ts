import { parseDocumentCanvas, documentCanvasForOrientation, type DocumentCanvas } from "@/lib/document-canvas";
import { CANVAS_ROW_GEOMETRY } from "../../upload/utils/autoCanvas";
import type { PresentationConfig } from "../../upload/type";

const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Recover a selector from the owner-authorized stored canvas; never guess its size. */
export function savedSvgTemplateCanvas(themeValue: unknown): Partial<PresentationConfig> {
  const theme = object(themeValue);
  if (theme.documentCanvas) {
    const canvas = parseDocumentCanvas(theme.documentCanvas);
    for (const orientation of ["landscape", "portrait"] as const) {
      if (JSON.stringify(canvas) === JSON.stringify(documentCanvasForOrientation(orientation))) return { pageSize: "A4", orientation, svgCanvas: undefined, sourceCanvas: undefined };
    }
    const ratios = { "wide-16x9": "16:9", "standard-4x3": "4:3", "tall-9x16": "9:16", "tall-9x13": "9:13", "square-1x1": "1:1", "ultrawide-21x9": "21:9" } as const;
    for (const [id, ratio] of Object.entries(ratios)) {
      const geometry = CANVAS_ROW_GEOMETRY[id as keyof typeof ratios];
      if (canvas.logicalSize.width === geometry.widthPx && canvas.logicalSize.height === geometry.heightPx && canvas.physicalSize.widthEmu === geometry.widthPx * 9525 && canvas.physicalSize.heightEmu === geometry.heightPx * 9525) return { aspectRatio: ratio, pageSize: undefined, orientation: undefined, svgCanvas: undefined, sourceCanvas: undefined };
    }
    const width_mm = canvas.physicalSize.widthEmu / 36000, height_mm = canvas.physicalSize.heightEmu / 36000;
    if ([width_mm, height_mm].every(value => value >= 50 && value <= 1000 && Math.abs(value * 100 - Math.round(value * 100)) < 1e-7)) return { svgCanvas: { width_mm, height_mm }, pageSize: undefined, orientation: undefined, sourceCanvas: undefined };
    // A source slide size finer than 0.01 mm is requested exactly, in EMU.
    const exact = { width_emu: canvas.physicalSize.widthEmu, height_emu: canvas.physicalSize.heightEmu };
    if (JSON.stringify(sourceSlideCanvas(exact.width_emu, exact.height_emu)) === JSON.stringify(canvas)) return { svgCanvas: exact, pageSize: undefined, orientation: undefined, sourceCanvas: undefined };
    throw new Error("svg_template_saved_canvas_unrepresentable");
  }
  const legacy = object(object(theme.authored_vnext).canvas);
  const ratio = legacy.aspectRatio ?? legacy.aspect_ratio;
  if (["16:9", "4:3", "9:16", "9:13", "1:1", "21:9"].includes(String(ratio))) return { aspectRatio: ratio as PresentationConfig["aspectRatio"], pageSize: undefined, orientation: undefined, svgCanvas: undefined, sourceCanvas: undefined };
  throw new Error("svg_template_saved_canvas_missing");
}

/**
 * The canvas selector a template-redesign request uses: exactly the template's
 * own source canvas (design D-4) -- a preset when it is one, otherwise the
 * source slide size in EMU, which the server resolves as the source does.
 */
export function templateSourceCanvasSelector(sourceCanvas: DocumentCanvas): Partial<PresentationConfig> {
  const preset = savedSvgTemplateCanvasOrNull(sourceCanvas);
  if (preset && !preset.svgCanvas) return preset;
  return { svgCanvas: { width_emu: sourceCanvas.physicalSize.widthEmu, height_emu: sourceCanvas.physicalSize.heightEmu },
    pageSize: undefined, orientation: undefined, sourceCanvas: undefined };
}

function savedSvgTemplateCanvasOrNull(canvas: DocumentCanvas): Partial<PresentationConfig> | null {
  try { return savedSvgTemplateCanvas({ documentCanvas: canvas }); } catch { return null; }
}

/**
 * Mirror of the server's ``document_canvas_from_source_slide_size``: the
 * canvas an exact EMU selector resolves to.
 */
export function sourceSlideCanvas(widthEmu: number, heightEmu: number): DocumentCanvas {
  const widthMm = widthEmu / 36000, heightMm = heightEmu / 36000;
  const legacyA4 = widthMm >= 296 && widthMm <= 298 && heightMm >= 209 && heightMm <= 211;
  const logicalWidth = legacyA4 ? 1188 : Math.round(1280 * widthMm / Math.max(widthMm, heightMm));
  const margin = legacyA4 ? 60 : 64;
  const logicalHeight = Math.round(logicalWidth * heightEmu / widthEmu);
  return { contractVersion: "document-canvas/v1", logicalSize: { width: logicalWidth, height: logicalHeight },
    physicalSize: { widthEmu, heightEmu },
    safeArea: { x: margin, y: margin, width: logicalWidth - 2 * margin, height: logicalHeight - 2 * margin } } as DocumentCanvas;
}
