"use client";

import { useEffect, useState } from "react";

// Recolors monochrome SVG fill/stroke declarations to a target color. Used
// when loading an icon-style image so its color can follow the surrounding
// UI instead of the color baked into the source SVG.
function recolorSvgMarkup(markup: string, color: string): string {
  const normalized = color.trim();
  if (!normalized || !markup.trim().startsWith("<")) return markup;
  return markup
    .replace(
      /\b(fill|stroke)=("|')(?!none\b|transparent\b|url\()[^"']*\2/gi,
      (_match, property: string, quote: string) =>
        `${property}=${quote}${normalized}${quote}`
    )
    .replace(
      /\b(fill|stroke)\s*:\s*(?!none\b|transparent\b|url\()[^;"'}]+/gi,
      (_match, property: string) => `${property}:${normalized}`
    );
}

/**
 * Loads a source (local path or data URI) into an HTMLImageElement. When
 * `iconColor` is provided and the source is an SVG, the SVG markup is
 * recolored before loading so monochrome icons can follow a target color.
 */
export function useTemplateImage(
  source: unknown,
  iconColor?: string | null
): HTMLImageElement | null {
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    setImage(null);
    if (
      typeof source !== "string" ||
      (!source.startsWith("/") &&
        !/^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(source) &&
        !/^data:image\/svg\+xml[;,]/i.test(source))
    ) {
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    const next = new window.Image();
    next.onload = () => {
      if (!cancelled) setImage(next);
    };
    next.onerror = () => {
      if (!cancelled) setImage(null);
    };

    const load = async () => {
      let resolvedSource = source;
      const shouldRecolor =
        Boolean(iconColor?.trim()) &&
        (/\.svg(?:[?#]|$)/i.test(source) ||
          /^data:image\/svg\+xml[;,]/i.test(source));
      if (shouldRecolor) {
        try {
          const response = await fetch(source);
          if (!response.ok) throw new Error("Could not load icon SVG");
          const markup = recolorSvgMarkup(
            await response.text(),
            iconColor ?? ""
          );
          objectUrl = URL.createObjectURL(
            new Blob([markup], { type: "image/svg+xml" })
          );
          resolvedSource = objectUrl;
        } catch {
          // The original source remains a safe fallback for cross-origin icons.
        }
      }
      if (!cancelled) next.src = resolvedSource;
    };
    void load();
    return () => {
      cancelled = true;
      next.onload = null;
      next.onerror = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [iconColor, source]);
  return image;
}
