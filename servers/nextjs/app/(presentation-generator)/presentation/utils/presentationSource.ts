import { isRecord } from "@/lib/guards";

export function isSvgPresentation(presentation: unknown): boolean {
  if (!isRecord(presentation) || !Array.isArray(presentation.slides)) return false;
  return presentation.slides.length > 0 && presentation.slides.every(
    (slide) => isRecord(slide) && (
      slide.source_format === "svg" ||
      (typeof slide.svg_content === "string" && slide.svg_content.trim().length > 0)
    ),
  );
}
