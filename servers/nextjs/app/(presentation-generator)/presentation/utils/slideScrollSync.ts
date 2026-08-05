export type VerticalViewport = {
  top: number;
  bottom: number;
};

export type VerticalSlideCandidate = VerticalViewport & {
  index: number;
};

/** Select the slide occupying the largest visible area of the main canvas. */
export function findMostVisibleSlideIndex(
  viewport: VerticalViewport,
  candidates: VerticalSlideCandidate[],
): number | null {
  const viewportCenter = (viewport.top + viewport.bottom) / 2;
  let best: { index: number; visible: number; centerDistance: number } | null =
    null;

  for (const candidate of candidates) {
    const visible = Math.max(
      0,
      Math.min(candidate.bottom, viewport.bottom) -
        Math.max(candidate.top, viewport.top),
    );
    if (visible <= 0) continue;
    const centerDistance = Math.abs(
      (candidate.top + candidate.bottom) / 2 - viewportCenter,
    );
    if (
      best === null ||
      visible > best.visible ||
      (visible === best.visible && centerDistance < best.centerDistance)
    ) {
      best = { index: candidate.index, visible, centerDistance };
    }
  }

  return best?.index ?? null;
}

/** Return a bounded thumbnail scroll target, or null when it is already visible. */
export function getThumbnailScrollTarget({
  scrollTop,
  clientHeight,
  targetTop,
  targetBottom,
  padding = 8,
}: {
  scrollTop: number;
  clientHeight: number;
  targetTop: number;
  targetBottom: number;
  padding?: number;
}): number | null {
  if (targetTop < scrollTop + padding) {
    return Math.max(targetTop - padding, 0);
  }
  if (targetBottom > scrollTop + clientHeight - padding) {
    return Math.max(targetBottom - clientHeight + padding, 0);
  }
  return null;
}
