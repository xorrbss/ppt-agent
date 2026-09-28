import { useCallback, useState } from "react";

import type { AuthoredStyleSummary } from "@/app/(presentation-generator)/services/api/authored";

function slideCount(style: AuthoredStyleSummary | undefined): number {
  return style?.samples?.slides.length ?? 0;
}

/** Keeps the page number when the deck changes, clamped to the new deck's last slide. */
function clampSlide(slideIndex: number, style: AuthoredStyleSummary | undefined): number {
  const count = slideCount(style);
  // A style without samples shows no pages, so it keeps the current page for the next deck.
  if (count === 0) return slideIndex;
  return Math.min(Math.max(slideIndex, 0), count - 1);
}

export interface StyleSampleNavigation {
  styleIndex: number;
  /** 0-based index into the current style's sample slides. */
  slideIndex: number;
  style: AuthoredStyleSummary | undefined;
  goToStyle: (delta: number) => void;
  goToSlide: (slideIndex: number) => void;
}

export function useStyleSampleNavigation(
  styles: AuthoredStyleSummary[],
  initialStyleId: string,
): StyleSampleNavigation {
  const [position, setPosition] = useState(() => ({
    styleIndex: Math.max(
      styles.findIndex((style) => style.id === initialStyleId),
      0,
    ),
    slideIndex: 0,
  }));

  const styleIndex = Math.min(position.styleIndex, Math.max(styles.length - 1, 0));
  const style = styles[styleIndex];
  const slideIndex = clampSlide(position.slideIndex, style);

  const goToStyle = useCallback(
    (delta: number) => {
      setPosition((current) => {
        const nextStyleIndex = current.styleIndex + delta;
        if (nextStyleIndex < 0 || nextStyleIndex >= styles.length) return current;
        return {
          styleIndex: nextStyleIndex,
          slideIndex: clampSlide(current.slideIndex, styles[nextStyleIndex]),
        };
      });
    },
    [styles],
  );

  const goToSlide = useCallback(
    (nextSlideIndex: number) => {
      setPosition((current) => {
        const count = slideCount(styles[current.styleIndex]);
        if (nextSlideIndex < 0 || nextSlideIndex >= count) return current;
        return { ...current, slideIndex: nextSlideIndex };
      });
    },
    [styles],
  );

  return { styleIndex, slideIndex, style, goToStyle, goToSlide };
}
