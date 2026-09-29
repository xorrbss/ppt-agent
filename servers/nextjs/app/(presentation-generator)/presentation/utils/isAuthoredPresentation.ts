import type { PresentationDriverId } from "@/lib/presentation-profile";

type SlideLike = {
  layout_group?: string | null;
  layout?: string | null;
  content?: { __authored__?: boolean } | null;
};

type PresentationLike = {
  mode?: string | null;
  theme?: { mode?: string | null } | null;
  slides?: Array<SlideLike> | null;
};

/**
 * The driver a persisted slide belongs to, read from the slide itself: the
 * svg pipeline stamps `layout_group: "svg"`, the authored pipeline stamps
 * `layout_group: "authored"` or the `__authored__` content sentinel. Every
 * "is this an authored-family slide" decision in the UI goes through here.
 */
export function inferSlideDriver(
  slide: SlideLike | null | undefined,
): PresentationDriverId | null {
  if (!slide) return null;
  if (slide.layout_group === "svg") return "svg-v1";
  if (slide.content?.__authored__ === true || slide.layout_group === "authored") {
    return "authored";
  }
  return null;
}

/** Authored-family slide (authored HTML or svg): rendered on the authored canvas. */
export function isAuthoredSlide(slide: SlideLike | null | undefined): boolean {
  return inferSlideDriver(slide) !== null;
}

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
      isAuthoredSlide(slide) || slide?.layout?.startsWith("authored:") === true,
  );

  return hasAuthoredSlide === true;
}
