"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { resolveAuthoredImageUrl } from "@/utils/api";

/**
 * The remake preview renders inside a narrow chat panel, which is too small to
 * judge the findings the automatic check reports (clipping, contrast, spacing).
 * Clicking the thumbnail opens it at viewport size; clicking again restores it.
 */
export function PageRemakePreviewImage({
  image,
  pageNumber,
}: {
  image: string;
  pageNumber: number;
}) {
  const [isZoomed, setIsZoomed] = useState(false);
  const source = resolveAuthoredImageUrl(image);
  const label = `${pageNumber}페이지 새 버전 미리보기`;

  // A regenerated candidate must never inherit the previous one's zoom state.
  useEffect(() => {
    setIsZoomed(false);
  }, [image]);

  useEffect(() => {
    if (!isZoomed) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsZoomed(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isZoomed]);

  return (
    <>
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <button
          type="button"
          className="block w-full cursor-zoom-in"
          onClick={() => setIsZoomed(true)}
          aria-label={`${label} 확대`}
          data-testid="page-remake-preview-zoom-in"
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- immutable runtime preview */}
          <img
            src={source}
            alt={label}
            className="aspect-video w-full object-cover"
          />
        </button>
      </div>

      {isZoomed &&
        typeof document !== "undefined" &&
        createPortal(
          // The legacy host renders this flow inside a translated dialog, and a
          // transformed ancestor anchors `fixed` to itself rather than to the
          // viewport. Portal to the body so the overlay always fills the screen.
          // A modal dialog also parks `pointer-events: none` on the body, so the
          // overlay has to opt back in or it renders but cannot be dismissed.
          // That opt-in is behaviour rather than styling, so it is set inline and
          // stays correct even where the utility stylesheet is absent.
          <div
            style={{ pointerEvents: "auto" }}
            className="fixed inset-0 z-[80] flex cursor-zoom-out items-center justify-center bg-black/80 p-4"
            onClick={() => setIsZoomed(false)}
            role="dialog"
            aria-modal="true"
            aria-label={label}
            data-testid="page-remake-preview-zoom"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- immutable runtime preview */}
            <img
              src={source}
              alt={label}
              className="max-h-[95vh] max-w-[95vw] object-contain shadow-2xl"
            />
          </div>,
          document.body,
        )}
    </>
  );
}
