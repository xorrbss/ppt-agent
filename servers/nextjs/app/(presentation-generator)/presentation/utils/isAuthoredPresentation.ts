type PresentationLike = {
  mode?: string | null;
  theme?: { mode?: string | null } | null;
  slides?: Array<{
    layout_group?: string | null;
    layout?: string | null;
    content?: { __authored__?: boolean } | null;
  }> | null;
};

/**
 * Detect both current and legacy AI-authored decks.
 *
 * Older saved decks may not have presentation.mode/theme.mode populated, but
 * their generated slides still carry authored sentinels.
 */
export function isAuthoredPresentation(
  presentation: PresentationLike | null | undefined,
): boolean {
  if (!presentation) return false;

  if (
    presentation.mode === "authored" ||
    presentation.theme?.mode === "authored"
  ) {
    return true;
  }

  const hasAuthoredSlide = presentation.slides?.some(
    (slide) =>
      slide?.content?.__authored__ === true ||
      slide?.layout_group === "authored" ||
      slide?.layout?.startsWith("authored:") === true,
  );

  return hasAuthoredSlide === true;
}
