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

export { savedSvgTemplateCanvas, templateSourceCanvasSelector } from "./svg-template-canvas";
