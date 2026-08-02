"use client";

import React, { useEffect, useRef, useState } from "react";

interface AuthoredSlidePreviewProps {
  slideIndex: number;
  html?: unknown;
  imageSrc?: string;
  preferHtml?: boolean;
}

/**
 * Read-only authored slide preview.
 *
 * Authored HTML is the persisted source of truth. The generated PNG is a cache
 * used by thumbnails and as a fallback. Rendering the HTML in the main canvas
 * avoids showing stale mutable PNGs after a version restore while the empty
 * sandbox keeps model-authored markup isolated from the editor application.
 */
export function AuthoredSlidePreview({
  slideIndex,
  html,
  imageSrc,
  preferHtml = false,
}: AuthoredSlidePreviewProps) {
  const htmlSource = typeof html === "string" ? html.trim() : "";
  const canRenderHtml = preferHtml && htmlSource.length > 0;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [htmlActivated, setHtmlActivated] = useState(false);

  useEffect(() => {
    if (!canRenderHtml || htmlActivated) return;

    const container = containerRef.current;
    if (!container || typeof IntersectionObserver === "undefined") {
      setHtmlActivated(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry?.isIntersecting) return;
        setHtmlActivated(true);
        observer.disconnect();
      },
      { rootMargin: "720px 0px" }
    );
    observer.observe(container);
    return () => observer.disconnect();
  }, [canRenderHtml, htmlActivated]);

  const renderHtml = canRenderHtml && htmlActivated;

  return (
    <div ref={containerRef} className="aspect-video h-full w-full overflow-hidden bg-white">
      {renderHtml ? (
        <iframe
          data-testid={`authored-html-preview-${slideIndex}`}
          srcDoc={htmlSource}
          title={`Slide ${slideIndex + 1}`}
          sandbox=""
          loading="lazy"
          referrerPolicy="no-referrer"
          tabIndex={-1}
          className="pointer-events-none block h-[720px] w-[1280px] border-0 bg-white"
        />
      ) : imageSrc ? (
        // eslint-disable-next-line @next/next/no-img-element -- Authored slide images may be backend, data, or blob URLs and must render unchanged.
        <img
          src={imageSrc}
          alt={`Slide ${slideIndex + 1}`}
          className="h-full w-full object-cover"
          draggable={false}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">
          Authored slide preview is unavailable.
        </div>
      )}
    </div>
  );
}
