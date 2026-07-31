/**
 * Host pinning and URL validation for the Google Fonts trust boundary.
 *
 * Every URL the collector is willing to touch passes through here, including
 * each redirect hop. Kept separate from fetching so the rules that define the
 * boundary can be read and tested without the transport around them.
 */

export const GOOGLE_FONTS_CSS_HOST = "fonts.googleapis.com";
export const GOOGLE_FONTS_FILE_HOST = "fonts.gstatic.com";
const ALLOWED_CSS_QUERY_KEYS = new Set(["family", "display", "subset"]);
const FAMILY_QUERY_PATTERN = /^[A-Za-z0-9 +,:;@.\-]+$/;
const SUBSET_QUERY_PATTERN = /^[A-Za-z0-9,_\-]+$/;
export const DISPLAY_VALUES = new Set([
  "auto",
  "block",
  "swap",
  "fallback",
  "optional",
]);

export type GoogleFontsResourceKind = "css" | "woff2";

export function parseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

export function validateCommonHttpsUrl(url: URL): string | null {
  if (url.protocol !== "https:") return "google-font-url-must-use-https";
  if (url.username || url.password) return "google-font-url-has-credentials";
  if (url.port && url.port !== "443") return "google-font-url-has-invalid-port";
  if (url.hash) return "google-font-url-has-fragment";
  // URL.hostname canonicalizes bracketed IPv6 and ordinary IPv4 literals.
  if (
    /^\[.*\]$/.test(url.hostname) ||
    /^(?:\d{1,3}\.){3}\d{1,3}$/.test(url.hostname)
  ) {
    return "google-font-url-ip-literal";
  }
  return null;
}

/**
 * Strictly validates a Google Fonts stylesheet endpoint. No wildcard or
 * sibling Google domain is accepted, and `text=` is never permitted.
 */
export function validateGoogleFontsCssUrl(value: string): URL {
  const url = parseUrl(value);
  if (!url) throw new Error("invalid-google-fonts-css-url");
  const commonFailure = validateCommonHttpsUrl(url);
  if (commonFailure) throw new Error(commonFailure);
  if (url.hostname.toLowerCase() !== GOOGLE_FONTS_CSS_HOST) {
    throw new Error("google-fonts-css-host-not-allowed");
  }
  if (url.pathname !== "/css" && url.pathname !== "/css2") {
    throw new Error("google-fonts-css-path-not-allowed");
  }
  if (!url.search || url.search.length > 4_096) {
    throw new Error("google-fonts-css-query-invalid");
  }
  for (const key of url.searchParams.keys()) {
    if (key.toLowerCase() === "text") {
      throw new Error("google-fonts-css-text-query-forbidden");
    }
    if (!ALLOWED_CSS_QUERY_KEYS.has(key)) {
      throw new Error("google-fonts-css-query-key-not-allowed");
    }
  }
  const families = url.searchParams.getAll("family");
  if (
    families.length < 1 ||
    families.length > 16 ||
    families.some(
      (family) =>
        !family ||
        family.length > 256 ||
        !FAMILY_QUERY_PATTERN.test(family)
    )
  ) {
    throw new Error("google-fonts-css-family-query-invalid");
  }
  const displays = url.searchParams.getAll("display");
  if (
    displays.length > 1 ||
    (displays.length === 1 && !DISPLAY_VALUES.has(displays[0]))
  ) {
    throw new Error("google-fonts-css-display-query-invalid");
  }
  const subsets = url.searchParams.getAll("subset");
  if (
    subsets.length > 1 ||
    (subsets.length === 1 &&
      (!subsets[0] ||
        subsets[0].length > 128 ||
        !SUBSET_QUERY_PATTERN.test(subsets[0])))
  ) {
    throw new Error("google-fonts-css-subset-query-invalid");
  }
  return url;
}

/** Strictly validates the only remote binary resource the collector accepts. */
export function validateGoogleFontsWoff2Url(value: string): URL {
  const url = parseUrl(value);
  if (!url) throw new Error("invalid-google-fonts-woff2-url");
  const commonFailure = validateCommonHttpsUrl(url);
  if (commonFailure) throw new Error(commonFailure);
  if (url.hostname.toLowerCase() !== GOOGLE_FONTS_FILE_HOST) {
    throw new Error("google-fonts-woff2-host-not-allowed");
  }
  if (
    !url.pathname.startsWith("/s/") ||
    !url.pathname.toLowerCase().endsWith(".woff2")
  ) {
    throw new Error("google-fonts-woff2-path-not-allowed");
  }
  if (url.search) throw new Error("google-fonts-woff2-query-not-allowed");
  return url;
}

export function validateResourceUrl(
  value: string,
  kind: GoogleFontsResourceKind
): URL {
  return kind === "css"
    ? validateGoogleFontsCssUrl(value)
    : validateGoogleFontsWoff2Url(value);
}

