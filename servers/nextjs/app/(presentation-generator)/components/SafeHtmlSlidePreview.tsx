"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  AUTHORED_SLIDE_HEIGHT_PX,
  AUTHORED_SLIDE_WIDTH_PX,
} from "@/lib/authored-hybrid/schema";
import {
  CHART_BROWSER_SCRIPT_URL,
  CHART_DATALABELS_SCRIPT_URL,
} from "@/lib/chart-browser";

const SLIDE_WIDTH = AUTHORED_SLIDE_WIDTH_PX;
const SLIDE_HEIGHT = AUTHORED_SLIDE_HEIGHT_PX;

export interface SafeHtmlSlidePreviewCanvas {
  widthPx: number;
  heightPx: number;
}

const PREVIEW_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "connect-src 'none'",
  "media-src 'none'",
  "form-action 'none'",
  "img-src data: blob: http: https:",
  "font-src data: http: https:",
  "style-src 'unsafe-inline' http: https:",
  "script-src 'unsafe-inline' http: https:",
].join("; ");

function escapeAttribute(value: string) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}
function fontAssets(fonts: unknown) {
  if (!fonts || typeof fonts !== "object" || Array.isArray(fonts)) return "";
  return Object.entries(fonts as Record<string, unknown>)
    .filter((entry): entry is [string, string] => typeof entry[1] === "string")
    .map(([family, url]) => {
      if (url.includes("fonts.googleapis.com") || url.endsWith(".css")) {
        return `<link rel="stylesheet" href="${escapeAttribute(url)}">`;
      }
      return `<style>@font-face{font-family:'${family.replaceAll("'", "\\'")}';src:url('${url.replaceAll("'", "\\'")}');font-display:swap}</style>`;
    })
    .join("\n");
}

function isolatedPreviewDocument(
  html: string,
  fonts: unknown,
  canvas: SafeHtmlSlidePreviewCanvas,
) {
  const { widthPx, heightPx } = canvas;
  return `<!doctype html>
  <html>
    <head>
      <meta charset="utf-8">
      <meta http-equiv="Content-Security-Policy" content="${PREVIEW_CSP}">
      <meta name="viewport" content="width=${widthPx}, initial-scale=1">
      ${fontAssets(fonts)}
      <script src="https://cdn.tailwindcss.com"></script>
      <script src="${CHART_BROWSER_SCRIPT_URL}"></script>
      <script src="${CHART_DATALABELS_SCRIPT_URL}"></script>
      <script>
        if (window.Chart && window.ChartDataLabels) {
          window.Chart.register(window.ChartDataLabels);
        }
      </script>
      <style>
        html,body{width:${widthPx}px;height:${heightPx}px;min-width:${widthPx}px;min-height:${heightPx}px;margin:0;overflow:hidden;background:#fff}
        *{box-sizing:border-box}
      </style>
    </head>
    <body>${html}</body>
  </html>`;
}

const DEFAULT_CANVAS: SafeHtmlSlidePreviewCanvas = {
  widthPx: SLIDE_WIDTH,
  heightPx: SLIDE_HEIGHT,
};

export default function SafeHtmlSlidePreview({
  html,
  fonts,
  fixedSize = false,
  title = "Presentation design preview",
  canvas = DEFAULT_CANVAS,
}: {
  html: string;
  fonts?: unknown;
  fixedSize?: boolean;
  title?: string;
  canvas?: SafeHtmlSlidePreviewCanvas;
}) {
  const { widthPx, heightPx } = canvas;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const srcDoc = useMemo(
    () => isolatedPreviewDocument(html, fonts, canvas),
    [fonts, html, canvas],
  );

  useEffect(() => {
    if (fixedSize) return;
    const element = containerRef.current;
    if (!element) return;
    const update = () => setWidth(element.clientWidth);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, [fixedSize]);

  const scale = fixedSize ? 1 : width ? Math.min(width / widthPx, 1) : 0;

  return (
    <div
      ref={containerRef}
      className="relative w-full overflow-hidden bg-white"
      data-safe-html-preview="true"
      style={{
        width: fixedSize ? widthPx : undefined,
        height: fixedSize ? heightPx : heightPx * (scale || 1),
      }}
    >
      <div
        className="absolute left-1/2 top-0"
        style={{
          width: widthPx,
          height: heightPx,
          transform: `translateX(-50%) scale(${scale || 1})`,
          transformOrigin: "top center",
          opacity: scale ? 1 : 0,
        }}
      >
        <iframe
          className="block border-0 bg-white"
          style={{ width: widthPx, height: heightPx }}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          srcDoc={srcDoc}
          tabIndex={-1}
          title={title}
        />
      </div>
    </div>
  );
}
