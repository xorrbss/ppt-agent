export const BLANK_SLIDE_LAYOUT_ID = "__blank_slide__";
export const BLANK_SLIDE_LAYOUT_GROUP = "blank";

type BlankPresentationSlideOptions = {
  id: string;
  index?: number;
  presentationId?: string | null;
  templateId?: string | null;
};

export function createBlankPresentationSlide({
  id,
  index = 0,
  presentationId,
  templateId,
}: BlankPresentationSlideOptions) {
  const resolvedTemplateId =
    typeof templateId === "string" && templateId.trim()
      ? templateId.trim()
      : "general";

  return {
    id,
    index,
    content: {},
    layout_group: resolvedTemplateId,
    layout: BLANK_SLIDE_LAYOUT_ID,
    ...(presentationId ? { presentation: presentationId } : {}),
  };
}

export function isTemplateFreePresentation(presentation: unknown) {
  if (
    !presentation ||
    typeof presentation !== "object" ||
    Array.isArray(presentation)
  ) {
    return false;
  }

  const slides = (presentation as { slides?: unknown }).slides;
  if (!Array.isArray(slides) || slides.length === 0) return false;
  const firstSlide = slides[0];
  if (!firstSlide || typeof firstSlide !== "object" || Array.isArray(firstSlide)) {
    return false;
  }
  return (
    (firstSlide as { layout_group?: unknown }).layout_group ===
    BLANK_SLIDE_LAYOUT_GROUP
  );
}
