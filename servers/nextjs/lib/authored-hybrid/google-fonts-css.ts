/**
 * HTML reference extraction and CSS @font-face parsing for the collector.
 *
 * Pulls Google Fonts links/imports out of an untrusted document, parses the
 * @font-face rules that come back, and decides which rules the slide text
 * actually needs via unicode-range coverage. No network access lives here.
 */

import { rewriteKnownFontFamiliesInCss } from "./font-policy.ts";
import {
  DISPLAY_VALUES,
  GOOGLE_FONTS_CSS_HOST,
  GOOGLE_FONTS_FILE_HOST,
  parseUrl,
  validateCommonHttpsUrl,
  validateGoogleFontsCssUrl,
  validateGoogleFontsWoff2Url,
} from "./google-fonts-url-policy.ts";

interface ExtractedGoogleFontsReferences {
  htmlForPreflight: string;
  stylesheets: string[];
  inlineFontFaceBodies: string[];
}

export interface ParsedFontFace {
  family: string;
  style: string;
  weight: string;
  stretch: string;
  unicodeRange?: string;
  fontUrl: string;
}

interface UnicodeInterval {
  start: number;
  end: number;
}

export function escapeCssString(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function decodeHtmlUrlAttribute(value: string): string {
  return value
    .replace(/&amp;/gi, "&")
    .replace(/&#0*38;/gi, "&")
    .replace(/&#x0*26;/gi, "&")
    .trim();
}

export function rewriteKnownFontFamiliesInHtml(html: string): string {
  let rewritten = html.replace(
    /<style\b([^>]*)>([\s\S]*?)<\/style>/gi,
    (_block, attributes: string, css: string) =>
      `<style${attributes}>${rewriteKnownFontFamiliesInCss(css)}</style>`
  );
  rewritten = rewritten.replace(
    /(\bstyle\s*=\s*)(?:"([^"]*)"|'([^']*)')/gi,
    (_attribute, prefix: string, doubleQuoted: string, singleQuoted: string) => {
      const quote = doubleQuoted !== undefined ? '"' : "'";
      const value = doubleQuoted ?? singleQuoted ?? "";
      const css = rewriteKnownFontFamiliesInCss(value);
      const attributeSafeCss =
        quote === '"' ? css.replace(/"/g, "&quot;") : css;
      return `${prefix}${quote}${attributeSafeCss}${quote}`;
    }
  );
  return rewritten;
}


function extractAttribute(tag: string, name: string): string | null {
  const pattern = new RegExp(
    `\\b${name}\\s*=\\s*(?:(["'])(.*?)\\1|([^\\s>]+))`,
    "i"
  );
  const match = pattern.exec(tag);
  return match ? (match[2] ?? match[3] ?? "") : null;
}

export function extractGoogleFontsReferences(
  html: string
): ExtractedGoogleFontsReferences {
  const stylesheets: string[] = [];
  const inlineFontFaceBodies: string[] = [];
  let htmlForPreflight = html.replace(/<link\b[^>]*>/gi, (tag) => {
    const rawHref = extractAttribute(tag, "href");
    const rawRel = extractAttribute(tag, "rel");
    if (!rawHref || !rawRel) return tag;
    const href = decodeHtmlUrlAttribute(rawHref);
    const rel = rawRel.toLowerCase().split(/\s+/);
    if (rel.includes("stylesheet")) {
      try {
        const url = validateGoogleFontsCssUrl(href);
        stylesheets.push(url.href);
        return "";
      } catch {
        return tag;
      }
    }
    if (rel.includes("preconnect")) {
      const url = parseUrl(href);
      if (
        url &&
        !validateCommonHttpsUrl(url) &&
        (url.hostname.toLowerCase() === GOOGLE_FONTS_CSS_HOST ||
          url.hostname.toLowerCase() === GOOGLE_FONTS_FILE_HOST) &&
        url.pathname === "/" &&
        !url.search
      ) {
        return "";
      }
    }
    return tag;
  });

  htmlForPreflight = htmlForPreflight.replace(
    /<style\b([^>]*)>([\s\S]*?)<\/style>/gi,
    (block, attributes: string, css: string) => {
      const sanitized = css.replace(
        /@import\s+(?:url\(\s*(["']?)(https:\/\/fonts\.googleapis\.com\/[^"')\s]+)\1\s*\)|(["'])(https:\/\/fonts\.googleapis\.com\/[^"']+)\3)\s*;/gi,
        (statement, _urlQuote: string, urlValue: string, _quote: string, quotedValue: string) => {
          const candidate = decodeHtmlUrlAttribute(urlValue || quotedValue);
          try {
            const url = validateGoogleFontsCssUrl(candidate);
            stylesheets.push(url.href);
            return "";
          } catch {
            return statement;
          }
        }
      );
      const withoutTrustedRemoteFaces = sanitized.replace(
        /@font-face\s*\{([^{}]*)\}/gi,
        (fontFaceBlock, body: string) => {
          try {
            // Validation is intentionally complete before removal. A malformed
            // or ambiguous block stays untrusted and the ordinary preflight
            // will reject its remote/data URL.
            parseFontFaceBody(body);
            inlineFontFaceBodies.push(body);
            return "";
          } catch {
            return fontFaceBlock;
          }
        }
      );
      return `<style${attributes}>${withoutTrustedRemoteFaces}</style>`;
    }
  );

  return {
    htmlForPreflight: rewriteKnownFontFamiliesInHtml(htmlForPreflight),
    stylesheets: [...new Set(stylesheets)],
    inlineFontFaceBodies,
  };
}

function parseCssDeclarations(body: string): Map<string, string> {
  const declarations = new Map<string, string>();
  for (const raw of body.split(";")) {
    if (!raw.trim()) continue;
    const colon = raw.indexOf(":");
    if (colon < 1) throw new Error("google-font-css-declaration-invalid");
    const name = raw.slice(0, colon).trim().toLowerCase();
    const value = raw.slice(colon + 1).trim();
    if (
      !/^[a-z-]+$/.test(name) ||
      !value ||
      declarations.has(name)
    ) {
      throw new Error("google-font-css-declaration-invalid");
    }
    declarations.set(name, value);
  }
  return declarations;
}

function normalizeFontFamily(value: string): string {
  const trimmed = value.trim();
  const match = /^(?:(["'])(.*?)\1|([A-Za-z0-9 _\-]+))$/.exec(trimmed);
  const family = (match?.[2] ?? match?.[3] ?? "").trim();
  if (!family || family.length > 128 || /[\u0000-\u001f\u007f]/.test(family)) {
    throw new Error("google-font-css-family-invalid");
  }
  return family;
}

function normalizeDescriptor(
  value: string | undefined,
  pattern: RegExp,
  fallback: string,
  reason: string
): string {
  if (!value) return fallback;
  const normalized = value.trim();
  if (!pattern.test(normalized)) throw new Error(reason);
  return normalized;
}

export function parseFontFaceBody(body: string): ParsedFontFace {
  const declarations = parseCssDeclarations(body);
  const allowed = new Set([
    "font-family",
    "font-style",
    "font-weight",
    "font-stretch",
    "font-display",
    "src",
    "unicode-range",
  ]);
  for (const name of declarations.keys()) {
    if (!allowed.has(name)) {
      throw new Error("google-font-css-descriptor-not-allowed");
    }
  }
  const family = normalizeFontFamily(declarations.get("font-family") ?? "");
  const style = normalizeDescriptor(
    declarations.get("font-style"),
    /^(?:normal|italic|oblique(?:\s+-?(?:\d+(?:\.\d+)?)deg(?:\s+-?(?:\d+(?:\.\d+)?)deg)?)?)$/i,
    "normal",
    "google-font-css-style-invalid"
  );
  const weight = normalizeDescriptor(
    declarations.get("font-weight"),
    /^(?:normal|bold|[1-9]00|[1-9]00\s+[1-9]00)$/i,
    "400",
    "google-font-css-weight-invalid"
  );
  const stretch = normalizeDescriptor(
    declarations.get("font-stretch"),
    /^(?:(?:ultra-|extra-|semi-)?(?:condensed|expanded)|normal|(?:\d+(?:\.\d+)?)%(?:\s+(?:\d+(?:\.\d+)?)%)?)$/i,
    "normal",
    "google-font-css-stretch-invalid"
  );
  const display = declarations.get("font-display");
  if (display && !DISPLAY_VALUES.has(display.toLowerCase())) {
    throw new Error("google-font-css-display-invalid");
  }
  const unicodeRange = declarations.get("unicode-range");
  if (
    unicodeRange &&
    !/^U\+[0-9A-F?]{1,6}(?:-[0-9A-F]{1,6})?(?:\s*,\s*U\+[0-9A-F?]{1,6}(?:-[0-9A-F]{1,6})?)*$/i.test(
      unicodeRange
    )
  ) {
    throw new Error("google-font-css-unicode-range-invalid");
  }
  const source = declarations.get("src") ?? "";
  const sourceMatch =
    /^url\(\s*(["']?)(https:\/\/fonts\.gstatic\.com\/[^"')\s]+)\1\s*\)\s+format\(\s*(["']?)woff2\3\s*\)$/i.exec(
      source
    );
  if (!sourceMatch) throw new Error("google-font-css-src-invalid");
  const fontUrl = validateGoogleFontsWoff2Url(sourceMatch[2]).href;
  return { family, style, weight, stretch, unicodeRange, fontUrl };
}

export function collectSourceCodePoints(html: string): Set<number> {
  const codePoints = new Set<number>();
  for (const character of html) {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined) codePoints.add(codePoint);
  }

  // Preserve coverage when authored text uses numeric HTML entities instead of
  // literal Unicode. This is local-only analysis; no slide text or `text=`
  // query is ever sent to Google.
  for (const match of html.matchAll(/&#(?:x([0-9a-f]{1,6})|([0-9]{1,7}));?/gi)) {
    const codePoint = Number.parseInt(match[1] ?? match[2], match[1] ? 16 : 10);
    if (
      Number.isSafeInteger(codePoint) &&
      codePoint >= 0 &&
      codePoint <= 0x10ffff &&
      !(codePoint >= 0xd800 && codePoint <= 0xdfff)
    ) {
      codePoints.add(codePoint);
    }
  }
  return codePoints;
}

function parseUnicodeInterval(value: string): UnicodeInterval | null {
  const token = value.trim().replace(/^U\+/i, "");
  let startText = token;
  let endText = token;
  if (token.includes("?")) {
    startText = token.replace(/\?/g, "0");
    endText = token.replace(/\?/g, "F");
  } else if (token.includes("-")) {
    [startText, endText] = token.split("-", 2);
  }
  const start = Number.parseInt(startText, 16);
  const end = Number.parseInt(endText, 16);
  if (
    !Number.isSafeInteger(start) ||
    !Number.isSafeInteger(end) ||
    start < 0 ||
    end < start ||
    start > 0x10ffff
  ) {
    return null;
  }
  return { start, end: Math.min(end, 0x10ffff) };
}

export function fontFaceCoversSource(
  face: ParsedFontFace,
  sourceCodePoints: Set<number>
): boolean {
  if (!face.unicodeRange) return true;
  const intervals = face.unicodeRange
    .split(",")
    .map(parseUnicodeInterval)
    .filter((interval): interval is UnicodeInterval => interval !== null);
  if (!intervals.length) return false;
  for (const codePoint of sourceCodePoints) {
    if (
      intervals.some(
        (interval) =>
          codePoint >= interval.start && codePoint <= interval.end
      )
    ) {
      return true;
    }
  }
  return false;
}


export function injectTrustedCss(html: string, css: string): string {
  if (!css) return html;
  const style =
    `<style data-presenton-trusted-google-fonts="v1">` +
    `${css}</style>`;
  const headClose = /<\/head\s*>/i;
  if (headClose.test(html)) return html.replace(headClose, `${style}</head>`);
  const htmlOpen = /<html\b[^>]*>/i;
  if (htmlOpen.test(html)) return html.replace(htmlOpen, `$&<head>${style}</head>`);
  return `<!doctype html><html><head>${style}</head><body>${html}</body></html>`;
}

