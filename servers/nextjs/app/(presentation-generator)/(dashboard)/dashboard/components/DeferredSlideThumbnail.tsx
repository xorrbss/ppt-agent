"use client";

import React, { useEffect, useRef, useState } from "react";

import SlideScale, {
  slideScaleReservedHeightCss,
} from "@/app/(presentation-generator)/components/PresentationRender";
import {
  LEGACY_AUTHORED_CANVAS,
  resolveAuthoredCanvas,
} from "@/lib/authored-canvas";
import { isAuthoredSlide } from "../../../presentation/utils/isAuthoredPresentation";
import { DashboardApi } from "../../../services/api/dashboard";
import { resolveAuthoredImageUrl } from "@/utils/api";

/**
 * How far outside the viewport a card starts rendering its slide stage —
 * roughly two grid rows, so an ordinary scroll never reaches a card whose stage
 * has not started rendering yet.
 */
const NEAR_VIEWPORT_ROOT_MARGIN = "600px 0px";

/**
 * Show a stored preview image near the viewport; fetch and render one cover
 * slide only when the summary has no usable preview image.
 *
 * Legacy list responses already include a cover slide. Each stage is a full slide
 * (CSS-scaled down) built from the real template component tree, so mounting one
 * per deck put thousands of nodes and several very long main-thread tasks on the
 * page before the user could see anything. Only the stage is deferred: the card
 * frame, title and date are rendered by <PresentationCard> and are unaffected.
 *
 * The wrapper reserves the stage's exact laid-out height up front (see
 * SLIDE_SCALE_RESERVED_HEIGHT_CSS) and keeps it after the stage mounts, so the
 * grid keeps its canvas ratio when a card materialises or when SlideScale's
 * ResizeObserver first measures its container.
 */
export const DeferredSlideThumbnail = ({
  slide,
  theme,
  presentationId,
  thumbnailUrl,
  coverVersion,
  loadCover = false,
}: {
  slide: any;
  theme?: unknown;
  presentationId?: string;
  thumbnailUrl?: string | null;
  coverVersion?: string;
  loadCover?: boolean;
}) => {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [isNearViewport, setIsNearViewport] = useState(false);
  const [failedImageUrl, setFailedImageUrl] = useState<string | null>(null);
  const [cover, setCover] = useState<{
    slide: unknown | null;
    theme: Record<string, unknown> | null;
  } | null>(null);
  const imageUrl = resolveAuthoredImageUrl(thumbnailUrl);
  const showImage = Boolean(imageUrl) && failedImageUrl !== imageUrl;

  useEffect(() => {
    setCover(null);
    setFailedImageUrl(null);
  }, [presentationId, thumbnailUrl, coverVersion]);

  useEffect(() => {
    if (!isNearViewport || !loadCover || !presentationId || showImage) return;
    const controller = new AbortController();
    void DashboardApi.getPresentationCover(presentationId, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setCover(result);
      })
      .catch(() => {
        // A missing preview must not make a usable dashboard card fail.
      });
    return () => controller.abort();
  }, [isNearViewport, loadCover, presentationId, showImage, thumbnailUrl, coverVersion]);

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
  // (SlideScale dereferences the slide unconditionally.)
  const previewSlide = slide ?? cover?.slide;
  const previewTheme = cover?.theme ?? theme;
  const canRender = showImage || Boolean(previewSlide);
  const state = !canRender
    ? "empty"
    : isNearViewport
      ? "rendered"
      : "deferred";
  const canvas = loadCover || isAuthoredSlide(previewSlide)
    ? resolveAuthoredCanvas(previewTheme)
    : LEGACY_AUTHORED_CANVAS;

  return (
    <div
      ref={boxRef}
      data-testid="deck-thumbnail"
      data-thumbnail-state={state}
      className="relative w-full"
      style={{ paddingTop: slideScaleReservedHeightCss(canvas) }}
    >
      <div className="absolute inset-0">
        {state === "rendered" ? (
          showImage ? (
            <img
              src={imageUrl}
              alt=""
              loading="lazy"
              decoding="async"
              className="h-full w-full object-contain"
              onError={() => setFailedImageUrl(imageUrl)}
            />
          ) : (
            <SlideScale slide={previewSlide} theme={previewTheme} isClickable={false} />
          )
        ) : (
          <div className="h-full w-full bg-gray-100" aria-hidden="true" />
        )}
      </div>
    </div>
  );
};

export default DeferredSlideThumbnail;
