"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  LEGACY_AUTHORED_CANVAS,
  type AuthoredCanvasContract,
} from "@/lib/authored-canvas";
import { AUTH_SESSION_EXPIRED_EVENT } from "../services/api/api-error-handler";

interface SvgSlidePreviewProps {
  slideIndex: number;
  slideId?: string;
  svg?: unknown;
  revision?: string | number | null;
  imageSrc?: string;
  resolveAssets?: boolean;
  canvas?: AuthoredCanvasContract;
  fixedGlyphPreview?: unknown;
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
  "font-src 'none'",
  "style-src 'unsafe-inline'",
  "script-src 'none'",
].join("; ");
const FIXED_GLYPH_SVG_PREVIEW_CSP = SVG_PREVIEW_CSP.replace("font-src 'none'", "font-src data:");

/** SVG stays in the same unprivileged iframe/PNG boundary as authored previews. */
export function SvgSlidePreview({
  slideIndex,
  slideId,
  svg,
  revision,
  imageSrc,
  resolveAssets = false,
  canvas = LEGACY_AUTHORED_CANVAS,
  fixedGlyphPreview,
}: SvgSlidePreviewProps) {
  const t = useTranslations("a2cPreview");
  // The app is web-only now: there is no Electron runtime branch left to
  // special-case (mirrors AuthoredSlidePreview). `runtime` stays only as a
  // post-mount gate so the SVG resolution effect below, and the srcDoc it
  // feeds, never run during server rendering.
  const [runtime, setRuntime] = useState<"unknown" | "web">("unknown");
  const [resolved, setResolved] = useState<{ key: string; svg: string } | null>(null);
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const [authInvalidated, setAuthInvalidated] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const wantsFixedGlyph = fixedGlyphPreview != null;
  const fixed = typeof fixedGlyphPreview === "object" && fixedGlyphPreview !== null
    ? fixedGlyphPreview as Record<string, unknown> : null;
  const fixedDigest = fixed?.version === "svg-fixed-glyph-preview/v1"
    && typeof fixed?.screenDisplaySvgDigest === "string"
    && /^sha256:[0-9a-f]{64}$/.test(fixed.screenDisplaySvgDigest)
    && typeof fixed?.authorityDigest === "string"
    && /^sha256:[0-9a-f]{64}$/.test(fixed.authorityDigest)
    && typeof fixed?.materializerCodeDigest === "string"
    && /^sha256:[0-9a-f]{64}$/.test(fixed.materializerCodeDigest)
    ? fixed.screenDisplaySvgDigest : null;
  const revisionKey = String(revision ?? svg ?? "");
  const requestKey = JSON.stringify([slideId, revisionKey,
    wantsFixedGlyph ? fixedGlyphPreview : null]);
  const canonicalSvg = !wantsFixedGlyph && typeof svg === "string" && !svg.includes("source-slot-") ? svg.trim() : "";
  const shouldResolve = wantsFixedGlyph
    ? Boolean(slideId && revision != null && fixedDigest)
    : resolveAssets && !canonicalSvg && Boolean(slideId);
  useEffect(() => setRuntime("web"), []);
  useEffect(() => {
    if (!wantsFixedGlyph) return;
    const invalidate = () => {
      pending.current?.abort();
      setAuthInvalidated(true);
      setResolved(null);
      setFailedKey(requestKey);
    };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate);
    window.addEventListener("presenton:auth-logout", invalidate);
    return () => {
      pending.current?.abort();
      window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate);
      window.removeEventListener("presenton:auth-logout", invalidate);
    };
  }, [wantsFixedGlyph, requestKey]);
  useEffect(() => {
    if (runtime !== "web" || !shouldResolve || !slideId || authInvalidated) return;
    let active = true;
    setFailedKey(null);
    const controller = wantsFixedGlyph ? new AbortController() : null;
    if (controller) pending.current = controller;
    const request = controller ? (async () => {
      const previewUrl = new URL(`/api/v1/ppt/slide/${encodeURIComponent(slideId)}/svg-preview`, window.location.origin);
      previewUrl.searchParams.set("revision", revisionKey);
      const response = await fetch(previewUrl, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
      if (!response.ok || response.headers.get("X-Presenton-Display-Digest") !== fixedDigest) {
        throw new Error("svg_preview_unavailable");
      }
      const source = await response.text();
      const bytes = new TextEncoder().encode(source);
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      const actual = "sha256:" + Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("");
      if (actual !== fixedDigest) throw new Error("svg_preview_unavailable");
      return source;
    })() : prefetchSvgSlidePreview(slideId, revisionKey);
    request.then((source) => {
      if (active) setResolved({ key: requestKey, svg: source });
    }).catch(() => {
      if (active) setFailedKey(requestKey);
    });
    // Only legacy requests use the shared cache; v3 requests are scoped to this view.
    return () => { active = false; controller?.abort(); if (pending.current === controller) pending.current = null; };
  }, [runtime, shouldResolve, slideId, revisionKey, requestKey, wantsFixedGlyph, fixedDigest, authInvalidated]);
  const svgSource = authInvalidated ? "" : canonicalSvg ||
    ((resolveAssets || wantsFixedGlyph) && resolved?.key === requestKey ? resolved.svg : "");
  const loading = runtime === "unknown" || (runtime === "web" && !authInvalidated
    && shouldResolve && failedKey !== requestKey && !svgSource);
  const srcDoc = useMemo(() => {
    if (!svgSource || runtime !== "web") return "";
    const withoutForeignDocument = svgSource
      .replace(/<script\b[\s\S]*?<\/script\s*>/gi, "")
      .replace(/<foreignObject\b[\s\S]*?<\/foreignObject\s*>/gi, "");
    return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${wantsFixedGlyph ? FIXED_GLYPH_SVG_PREVIEW_CSP : SVG_PREVIEW_CSP}"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden}body>svg{display:block;width:100%;height:100%}</style><body>${withoutForeignDocument}</body>`;
  }, [runtime, svgSource, wantsFixedGlyph]);

  return (
    <div
      data-testid={`svg-slide-preview-${slideIndex}`}
      data-svg-runtime={runtime}
      className="h-full w-full overflow-hidden bg-white"
      style={{ aspectRatio: `${canvas.widthPx} / ${canvas.heightPx}` }}
    >
      {srcDoc ? <iframe title={t("slide", { number: slideIndex + 1 })} srcDoc={srcDoc} sandbox="" loading="eager" referrerPolicy="no-referrer" tabIndex={-1} className="pointer-events-none block h-full w-full border-0" /> : !wantsFixedGlyph && imageSrc ? (
        // eslint-disable-next-line @next/next/no-img-element -- backend artifacts may use non-Next URLs.
        <img src={imageSrc} alt={t("slide", { number: slideIndex + 1 })} className="h-full w-full object-cover" draggable={false} />
      ) : loading ? <div role="status" className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">{t("loading")}</div> : <div role="alert" className="flex h-full w-full items-center justify-center bg-gray-100 text-sm text-gray-600">{t("unavailable")}</div>}
    </div>
  );
}
