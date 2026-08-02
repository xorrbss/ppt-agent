"use client";

import React from "react";

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
 * used by thumbnails and as a fallback. A main-canvas preview chooses one
 * render kind until its persisted source changes: HTML when available,
 * otherwise the PNG. This prevents an asset-cache refresh or viewport
 * activation from replacing the visible slide with a differently scaled
 * representation.
 */
export function AuthoredSlidePreview({
  slideIndex,
  html,
  imageSrc,
  preferHtml = false,
}: AuthoredSlidePreviewProps) {
  const htmlSource = typeof html === "string" ? html.trim() : "";
  const canRenderHtml = preferHtml && htmlSource.length > 0;

  return (
    <div
      data-testid={`authored-slide-preview-${slideIndex}`}
      className="aspect-video h-full w-full overflow-hidden bg-white"
    >
      {canRenderHtml ? (
        <iframe
          data-testid={`authored-html-preview-${slideIndex}`}
          srcDoc={htmlSource}
          title={`Slide ${slideIndex + 1}`}
          sandbox=""
          loading="lazy"
          referrerPolicy="no-referrer"
          tabIndex={-1}
          className="pointer-events-none block h-full w-full border-0 bg-white"
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
