"use client";

import React, { useEffect, useState } from "react";

interface AuthoredSlidePreviewProps {
  slideIndex: number;
  html?: unknown;
  imageSrc?: string;
  preferHtml?: boolean;
}

const AUTHORED_PREVIEW_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "connect-src 'none'",
  "media-src 'none'",
  "form-action 'none'",
  "img-src data:",
  "font-src data:",
  "style-src 'unsafe-inline'",
  "script-src 'none'",
].join("; ");

export function isolateAuthoredPreviewHtml(html: string): string {
  const csp = `<meta http-equiv="Content-Security-Policy" content="${AUTHORED_PREVIEW_CSP}">`;
  const withoutBaseOrCsp = html
    .replace(/<base\b[^>]*>/gi, "")
    .replace(
      /<meta\b(?=[^>]*http-equiv\s*=\s*["']?content-security-policy["']?)[^>]*>/gi,
      ""
    );
  if (/<head(?:\s[^>]*)?>/i.test(withoutBaseOrCsp)) {
    return withoutBaseOrCsp.replace(
      /<head(?:\s[^>]*)?>/i,
      (opening) => `${opening}${csp}`
    );
  }
  return `<head>${csp}</head>${withoutBaseOrCsp}`;
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
  // Fail closed during SSR/hydration. Only a confirmed ordinary web runtime
  // may construct authored srcDoc; Electron always consumes the isolated
  // backend worker's PNG artifact and therefore exposes zero authored DOM to
  // the privileged application window/preload.
  const [runtime, setRuntime] = useState<"unknown" | "web" | "electron">(
    "unknown"
  );
  useEffect(() => {
    setRuntime(window.electron ? "electron" : "web");
  }, []);
  const htmlSource = typeof html === "string" ? html.trim() : "";
  const canRenderHtml =
    runtime === "web" && preferHtml && htmlSource.length > 0;
  const isolatedHtmlSource = canRenderHtml
    ? isolateAuthoredPreviewHtml(htmlSource)
    : "";

  return (
    <div
      data-testid={`authored-slide-preview-${slideIndex}`}
      data-authored-runtime={runtime}
      className="aspect-video h-full w-full overflow-hidden bg-white"
    >
      {canRenderHtml ? (
        <iframe
          data-testid={`authored-html-preview-${slideIndex}`}
          srcDoc={isolatedHtmlSource}
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
