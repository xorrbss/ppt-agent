"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  CHART_BROWSER_SCRIPT_URL,
  CHART_DATALABELS_SCRIPT_URL,
} from "@/lib/chart-browser";

const SLIDE_WIDTH = 1280;
const SLIDE_HEIGHT = 720;

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

function previewDocument(html: string, fonts: unknown) {
  return `<!doctype html>
  <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=1280, initial-scale=1">
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
        html,body{width:1280px;height:720px;min-width:1280px;min-height:720px;margin:0;overflow:hidden;background:#fff}
        *{box-sizing:border-box}
      </style>
    </head>
    <body>${html}</body>
  </html>`;
}

export default function SmartHtmlSlide({
  html,
  fonts,
  fixedSize = false,
  title = "Smart presentation slide",
}: {
  html: string;
  fonts?: unknown;
  fixedSize?: boolean;
  title?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const srcDoc = useMemo(() => previewDocument(html, fonts), [fonts, html]);

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

  const scale = fixedSize ? 1 : width ? Math.min(width / SLIDE_WIDTH, 1) : 0;

  return (
    <div
      ref={containerRef}
      className="relative w-full overflow-hidden bg-white"
      style={{
        width: fixedSize ? SLIDE_WIDTH : undefined,
        height: fixedSize ? SLIDE_HEIGHT : SLIDE_HEIGHT * (scale || 1),
      }}
    >
      <div
        className="absolute left-1/2 top-0"
        style={{
          width: SLIDE_WIDTH,
          height: SLIDE_HEIGHT,
          transform: `translateX(-50%) scale(${scale || 1})`,
          transformOrigin: "top center",
          opacity: scale ? 1 : 0,
        }}
      >
        <iframe
          className="block h-[720px] w-[1280px] border-0 bg-white"
          sandbox="allow-scripts"
          srcDoc={srcDoc}
          tabIndex={-1}
          title={title}
        />
      </div>
    </div>
  );
}
