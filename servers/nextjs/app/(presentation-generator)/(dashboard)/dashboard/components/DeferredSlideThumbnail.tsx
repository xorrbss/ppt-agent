"use client";

import React, { useEffect, useRef, useState } from "react";

import SlideScale, {
  SLIDE_SCALE_RESERVED_HEIGHT_CSS,
} from "@/app/(presentation-generator)/components/PresentationRender";

/**
 * How far outside the viewport a card starts rendering its slide stage —
 * roughly two grid rows, so an ordinary scroll never reaches a card whose stage
 * has not started rendering yet.
 */
const NEAR_VIEWPORT_ROOT_MARGIN = "600px 0px";

/**
 * A deck thumbnail that does not mount its slide stage until the card is near
 * the viewport.
 *
 * The dashboard renders every deck at once. Each stage is a full 1280x720 slide
 * (CSS-scaled down) built from the real template component tree, so mounting one
 * per deck put thousands of nodes and several very long main-thread tasks on the
 * page before the user could see anything. Only the stage is deferred: the card
 * frame, title and date are rendered by <PresentationCard> and are unaffected.
 *
 * The wrapper reserves the stage's exact laid-out height up front (see
 * SLIDE_SCALE_RESERVED_HEIGHT_CSS) and keeps it after the stage mounts, so the
 * grid never reflows — neither when a card materialises nor when SlideScale's
 * ResizeObserver first measures its container.
 */
export const DeferredSlideThumbnail = ({ slide }: { slide: any }) => {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [isNearViewport, setIsNearViewport] = useState(false);

  useEffect(() => {
    if (isNearViewport) return;

    const box = boxRef.current;
    if (!box) return;

    // Environments without IntersectionObserver must still show the thumbnail
    // rather than a permanent placeholder.
    if (typeof IntersectionObserver === "undefined") {
      setIsNearViewport(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setIsNearViewport(true);
        observer.disconnect();
      },
      { rootMargin: NEAR_VIEWPORT_ROOT_MARGIN }
    );
    observer.observe(box);

    return () => observer.disconnect();
  }, [isNearViewport]);

  // A deck with no slides has nothing to stage; render the reserved box only.
  // (SlideScale/V1ContentRender dereference the slide unconditionally.)
  const canRender = Boolean(slide);
  const state = !canRender
    ? "empty"
    : isNearViewport
      ? "rendered"
      : "deferred";

  return (
    <div
      ref={boxRef}
      data-testid="deck-thumbnail"
      data-thumbnail-state={state}
      className="relative w-full"
      style={{ paddingTop: SLIDE_SCALE_RESERVED_HEIGHT_CSS }}
    >
      <div className="absolute inset-0">
        {state === "rendered" ? (
          <SlideScale slide={slide} isClickable={false} />
        ) : (
          <div className="h-full w-full bg-gray-100" aria-hidden="true" />
        )}
      </div>
    </div>
  );
};

export default DeferredSlideThumbnail;
