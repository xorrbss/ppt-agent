"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  LEGACY_AUTHORED_CANVAS,
  type AuthoredCanvasContract,
} from "@/lib/authored-canvas";

interface SvgSlidePreviewProps {
  slideIndex: number;
  slideId?: string;
  svg?: unknown;
  revision?: string | number | null;
  imageSrc?: string;
  resolveAssets?: boolean;
  canvas?: AuthoredCanvasContract;
}

const previewCache = new Map<string, Promise<string>>();
const MAX_CACHED_PREVIEWS = 128;

export function prefetchSvgSlidePreview(slideId: string, revision: string | number): Promise<string> {
  const key = JSON.stringify([slideId, String(revision)]);
  const existing = previewCache.get(key);
  if (existing) return existing;
  const previewUrl = new URL(`/api/v1/ppt/slide/${encodeURIComponent(slideId)}/svg-preview`, window.location.origin);
  previewUrl.searchParams.set("revision", String(revision));
  const request = fetch(previewUrl, { credentials: "same-origin", cache: "no-store" })
    .then(async (response) => {
      if (!response.ok) throw new Error("svg_preview_unavailable");
      return response.text();
    })
    .catch((error) => {
      if (previewCache.get(key) === request) previewCache.delete(key);
      throw error;
    });
  previewCache.set(key, request);
  if (previewCache.size > MAX_CACHED_PREVIEWS) {
    const oldest = previewCache.keys().next().value;
    if (oldest !== undefined) previewCache.delete(oldest);
  }
  return request;
}

const SVG_PREVIEW_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "connect-src 'none'",
  "img-src data:",
  "font-src data:",
  "style-src 'unsafe-inline'",
  "script-src 'none'",
].join("; ");

/** SVG stays in the same unprivileged iframe/PNG boundary as authored previews. */
export function SvgSlidePreview({
  slideIndex,
  slideId,
  svg,
  revision,
  imageSrc,
  resolveAssets = false,
  canvas = LEGACY_AUTHORED_CANVAS,
}: SvgSlidePreviewProps) {
  // The app is web-only now: there is no Electron runtime branch left to
  // special-case (mirrors AuthoredSlidePreview). `runtime` stays only as a
  // post-mount gate so the SVG resolution effect below, and the srcDoc it
  // feeds, never run during server rendering.
  const [runtime, setRuntime] = useState<"unknown" | "web">("unknown");
  const [resolved, setResolved] = useState<{ key: string; svg: string } | null>(null);
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const revisionKey = String(revision ?? svg ?? "");
  const requestKey = JSON.stringify([slideId, revisionKey]);
  const canonicalSvg = typeof svg === "string" && !svg.includes("source-slot-") ? svg.trim() : "";
  const shouldResolve = resolveAssets && !canonicalSvg && Boolean(slideId);
  useEffect(() => setRuntime("web"), []);
  useEffect(() => {
    if (runtime !== "web" || !shouldResolve || !slideId) return;
    let active = true;
    setFailedKey(null);
    prefetchSvgSlidePreview(slideId, revisionKey).then((source) => {
      if (active) setResolved({ key: requestKey, svg: source });
    }).catch(() => {
      if (active) setFailedKey(requestKey);
    });
    // Shared requests outlive individual views so reselecting a page reuses them.
    return () => { active = false; };
  }, [runtime, shouldResolve, slideId, revisionKey, requestKey]);
  const svgSource = canonicalSvg || (resolveAssets && resolved?.key === requestKey ? resolved.svg : "");
  const loading = runtime === "unknown" || (runtime === "web" && shouldResolve && failedKey !== requestKey && !svgSource);
  const srcDoc = useMemo(() => {
    if (!svgSource || runtime !== "web") return "";
    const withoutForeignDocument = svgSource
      .replace(/<script\b[\s\S]*?<\/script\s*>/gi, "")
      .replace(/<foreignObject\b[\s\S]*?<\/foreignObject\s*>/gi, "");
    return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${SVG_PREVIEW_CSP}"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden}body>svg{display:block;width:100%;height:100%}</style><body>${withoutForeignDocument}</body>`;
  }, [runtime, svgSource]);

  return (
    <div
      data-testid={`svg-slide-preview-${slideIndex}`}
      data-svg-runtime={runtime}
      className="h-full w-full overflow-hidden bg-white"
      style={{ aspectRatio: `${canvas.widthPx} / ${canvas.heightPx}` }}
    >
      {srcDoc ? <iframe title={`Slide ${slideIndex + 1}`} srcDoc={srcDoc} sandbox="" loading="eager" referrerPolicy="no-referrer" tabIndex={-1} className="pointer-events-none block h-full w-full border-0" /> : imageSrc ? (
        // eslint-disable-next-line @next/next/no-img-element -- backend artifacts may use non-Next URLs.
        <img src={imageSrc} alt={`Slide ${slideIndex + 1}`} className="h-full w-full object-cover" draggable={false} />
      ) : loading ? <div role="status" className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">SVG preview loading.</div> : <div role="alert" className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">SVG slide preview is unavailable.</div>}
    </div>
  );
}
