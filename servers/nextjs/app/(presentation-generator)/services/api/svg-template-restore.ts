import { parseDocumentCanvas, documentCanvasForOrientation } from "@/lib/document-canvas";
import { CANVAS_ROW_GEOMETRY } from "../../upload/utils/autoCanvas";
import type { SvgTemplateMode, SvgTemplatePin } from "./svg-templates";
import { LanguageType, ToneType, VerbosityType, type PresentationConfig } from "../../upload/type";

const object = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const own = (value: Record<string, unknown>, key: string) => Object.prototype.hasOwnProperty.call(value, key);
export function savedSvgTemplateSelection(settingsValue: unknown, themeValue: unknown): Pick<PresentationConfig, "svgTemplatePin" | "svgTemplateMode" | "svgTemplateFrameMap"> & { authoredStyle?: string } {
  const settings = object(settingsValue), theme = object(themeValue);
  const explicitStyle = settings.authored_style ?? settings.authoredStyle;
  // An explicit legacy choice, including "default", supersedes a stale theme pin.
  if (typeof explicitStyle === "string") return { authoredStyle: explicitStyle, svgTemplatePin: undefined, svgTemplateMode: undefined, svgTemplateFrameMap: undefined };
  const source = own(settings, "svg_template_pin") || own(settings, "svgTemplatePin") ? settings : theme;
  const raw = source.svg_template_pin ?? source.svgTemplatePin;
  const pin = object(raw), mode = source.svg_template_mode ?? source.svgTemplateMode;
  if (typeof pin.templateId !== "string" || typeof pin.revision !== "number" || !Number.isInteger(pin.revision) || pin.revision < 1 || typeof pin.digest !== "string" || !/^sha256:[a-f0-9]{64}$/.test(pin.digest) || (mode !== "free-design" && mode !== "template-redesign")) return { svgTemplatePin: undefined, svgTemplateMode: undefined, svgTemplateFrameMap: undefined };
  const mapValue = source.svg_template_frame_map ?? source.svgTemplateFrameMap;
  const map = object(mapValue);
  return { svgTemplatePin: pin as unknown as SvgTemplatePin, svgTemplateMode: mode as SvgTemplateMode,
    svgTemplateFrameMap: mapValue && Object.values(map).every(value => typeof value === "string") ? map as Record<string, string> : undefined };
}
export function templateRestoreConfig(selection: ReturnType<typeof savedSvgTemplateSelection>, settingsValue: unknown): PresentationConfig {
  const settings = object(settingsValue);
  return { slides: String(settings.n_slides ?? 8), language: Object.values(LanguageType).includes(settings.language as LanguageType) ? settings.language as LanguageType : null, prompt: "", tone: Object.values(ToneType).includes(settings.tone as ToneType) ? settings.tone as ToneType : ToneType.Default, verbosity: Object.values(VerbosityType).includes(settings.verbosity as VerbosityType) ? settings.verbosity as VerbosityType : VerbosityType.Standard, instructions: typeof settings.instructions === "string" ? settings.instructions : "", includeTableOfContents: settings.include_table_of_contents === true, includeTitleSlide: settings.include_title_slide === true, webSearch: settings.web_search === true,
    commonHeaderMode: selection.svgTemplateMode === "template-redesign" ? "free" : "uniform", commonHeaderCoverException: false, aspectRatio: "16:9", codexGenerationPreset: null,
    svgTemplatePin: selection.svgTemplatePin, svgTemplateMode: selection.svgTemplateMode, svgTemplateFrameMap: selection.svgTemplateFrameMap };
}

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
    throw new Error("svg_template_saved_canvas_unrepresentable");
  }
  const legacy = object(object(theme.authored_vnext).canvas);
  const ratio = legacy.aspectRatio ?? legacy.aspect_ratio;
  if (["16:9", "4:3", "9:16", "9:13", "1:1", "21:9"].includes(String(ratio))) return { aspectRatio: ratio as PresentationConfig["aspectRatio"], pageSize: undefined, orientation: undefined, svgCanvas: undefined, sourceCanvas: undefined };
  throw new Error("svg_template_saved_canvas_missing");
}
