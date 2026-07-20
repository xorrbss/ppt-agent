interface StoredPresentationIdentity {
  mode?: unknown;
  theme?: unknown;
  slides?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Explicit mode wins; old authored sentinels keep pre-mode rows working. */
export function isAuthoredPresentation(
  presentation: StoredPresentationIdentity
): boolean {
  if (presentation.mode !== undefined && presentation.mode !== null) {
    return presentation.mode === "authored";
  }
  if (isRecord(presentation.theme) && presentation.theme.mode === "authored") {
    return true;
  }
  if (!Array.isArray(presentation.slides)) return false;
  return presentation.slides.some(
    (slide) =>
      isRecord(slide) &&
      (slide.layout_group === "authored" ||
        (isRecord(slide.content) && slide.content.__authored__ === true))
  );
}
