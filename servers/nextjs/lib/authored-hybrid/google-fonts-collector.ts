import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import { rewriteKnownFontFamiliesInCss } from "./font-policy.ts";

const GOOGLE_FONTS_CSS_HOST = "fonts.googleapis.com";
const GOOGLE_FONTS_FILE_HOST = "fonts.gstatic.com";
const DEFAULT_TIMEOUT_MS = 8_000;
// Google Fonts' modern CJK responses contain many unicode-range rules and can
// exceed 256 KiB even though the CSS itself remains small and bounded.
const DEFAULT_MAX_CSS_BYTES = 1024 * 1024;
const DEFAULT_MAX_FONT_BYTES = 8 * 1024 * 1024;
const DEFAULT_MAX_TOTAL_FONT_BYTES = 32 * 1024 * 1024;
const DEFAULT_MAX_REDIRECTS = 3;
const DEFAULT_MAX_FONT_FILES = 96;
const CACHE_SCHEMA_VERSION = 1;
const CSS_USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

const ALLOWED_CSS_CONTENT_TYPES = new Set(["text/css"]);
const ALLOWED_WOFF2_CONTENT_TYPES = new Set([
  "font/woff2",
  "application/font-woff2",
]);
const ALLOWED_CSS_QUERY_KEYS = new Set(["family", "display", "subset"]);
const FAMILY_QUERY_PATTERN = /^[A-Za-z0-9 +,:;@.\-]+$/;
const SUBSET_QUERY_PATTERN = /^[A-Za-z0-9,_\-]+$/;
const DISPLAY_VALUES = new Set([
  "auto",
  "block",
  "swap",
  "fallback",
  "optional",
]);

type GoogleFontsResourceKind = "css" | "woff2";

export type GoogleFontsCollectionStatus =
  | "not-requested"
  | "success"
  | "partial"
  | "unavailable";

export interface GoogleFontsCollectionFailure {
  url: string;
  reason: string;
}

export interface PreparedGoogleFontsHtml {
  /**
   * The untrusted document with only validated Google Fonts link/import
   * references removed. This value, never htmlForChrome, must pass the normal
   * authored-hybrid preflight before the trusted CSS is used.
   */
  htmlForPreflight: string;
  /**
   * htmlForPreflight plus collector-generated, validated data @font-face CSS.
   * It is safe to use only after htmlForPreflight passes the normal preflight.
   */
  htmlForChrome: string;
  status: GoogleFontsCollectionStatus;
  reason?: string;
  usedFallback: boolean;
  requestedStylesheets: number;
  requestedFontFaces: number;
  loadedStylesheets: number;
  fontFiles: number;
  /**
   * Font-family names parsed from collector-validated @font-face rules whose
   * WOFF2 payloads were fetched and verified successfully. Only these names
   * may cross into native OOXML as authored typefaces.
   */
  trustedFamilies: string[];
  failures: GoogleFontsCollectionFailure[];
}

export interface GoogleFontsCollectorOptions {
  cacheDirectory: string;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  maxCssBytes?: number;
  maxFontBytes?: number;
  maxTotalFontBytes?: number;
  maxRedirects?: number;
  maxFontFiles?: number;
}

interface NormalizedGoogleFontsCollectorOptions {
  cacheDirectory: string;
  fetchImpl: typeof fetch;
  timeoutMs: number;
  maxCssBytes: number;
  maxFontBytes: number;
  maxTotalFontBytes: number;
  maxRedirects: number;
  maxFontFiles: number;
}

interface CachedResource {
  bytes: Buffer;
  contentType: string;
}

interface CacheIndex {
  schemaVersion: number;
  kind: GoogleFontsResourceKind;
  url: string;
  sha256: string;
  contentType: string;
  size: number;
}

interface ExtractedGoogleFontsReferences {
  htmlForPreflight: string;
  stylesheets: string[];
  inlineFontFaceBodies: string[];
}

interface TrustedFontFace {
  css: string;
  fontBytes: number;
  family: string;
}

interface ParsedFontFace {
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

function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function positiveInteger(
  value: number | undefined,
  fallback: number,
  name: string
): number {
  const resolved = value ?? fallback;
  if (!Number.isSafeInteger(resolved) || resolved <= 0) {
    throw new Error(`${name} must be a positive integer`);
  }
  return resolved;
}

function canonicalContentType(value: string | null): string {
  return (value ?? "").split(";", 1)[0].trim().toLowerCase();
}

function isWoff2(bytes: Buffer): boolean {
  return (
    bytes.length >= 4 &&
    bytes[0] === 0x77 &&
    bytes[1] === 0x4f &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x32
  );
}

function escapeCssString(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}

function decodeHtmlUrlAttribute(value: string): string {
  return value
    .replace(/&amp;/gi, "&")
    .replace(/&#0*38;/gi, "&")
    .replace(/&#x0*26;/gi, "&")
    .trim();
}

function rewriteKnownFontFamiliesInHtml(html: string): string {
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

function parseUrl(value: string): URL | null {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function validateCommonHttpsUrl(url: URL): string | null {
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

function validateResourceUrl(
  value: string,
  kind: GoogleFontsResourceKind
): URL {
  return kind === "css"
    ? validateGoogleFontsCssUrl(value)
    : validateGoogleFontsWoff2Url(value);
}

async function readBoundedBytes(
  response: Response,
  maxBytes: number
): Promise<Buffer> {
  const declaredLength = response.headers.get("content-length");
  if (declaredLength !== null) {
    const parsed = Number(declaredLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > maxBytes) {
      throw new Error("google-font-resource-too-large");
    }
  }
  if (!response.body) return Buffer.alloc(0);
  const reader = response.body.getReader();
  const chunks: Buffer[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => {});
        throw new Error("google-font-resource-too-large");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks, total);
}

function cacheExtension(kind: GoogleFontsResourceKind): string {
  return kind === "css" ? ".css" : ".woff2";
}

function cacheLimit(
  kind: GoogleFontsResourceKind,
  options: NormalizedGoogleFontsCollectorOptions
): number {
  return kind === "css" ? options.maxCssBytes : options.maxFontBytes;
}

async function readCachedResource(
  url: string,
  kind: GoogleFontsResourceKind,
  options: NormalizedGoogleFontsCollectorOptions
): Promise<CachedResource | null> {
  const indexKey = sha256(`${kind}\0${url}\0${CSS_USER_AGENT}`);
  const indexPath = path.join(options.cacheDirectory, "index", `${indexKey}.json`);
  try {
    const indexStat = await fs.stat(indexPath);
    if (!indexStat.isFile() || indexStat.size > 4_096) return null;
    const parsed: unknown = JSON.parse(await fs.readFile(indexPath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const index = parsed as Partial<CacheIndex>;
    if (
      index.schemaVersion !== CACHE_SCHEMA_VERSION ||
      index.kind !== kind ||
      index.url !== url ||
      typeof index.sha256 !== "string" ||
      !/^[a-f0-9]{64}$/.test(index.sha256) ||
      typeof index.contentType !== "string" ||
      !Number.isSafeInteger(index.size) ||
      (index.size as number) < 0 ||
      (index.size as number) > cacheLimit(kind, options)
    ) {
      return null;
    }
    const contentPath = path.join(
      options.cacheDirectory,
      "objects",
      `${index.sha256}${cacheExtension(kind)}`
    );
    const contentStat = await fs.stat(contentPath);
    if (!contentStat.isFile() || contentStat.size !== index.size) return null;
    const bytes = await fs.readFile(contentPath);
    if (sha256(bytes) !== index.sha256) return null;
    const allowedTypes =
      kind === "css"
        ? ALLOWED_CSS_CONTENT_TYPES
        : ALLOWED_WOFF2_CONTENT_TYPES;
    if (!allowedTypes.has(index.contentType)) return null;
    if (kind === "woff2" && !isWoff2(bytes)) return null;
    if (kind === "css" && (bytes.includes(0) || bytes.toString("utf8").includes("\uFFFD"))) {
      return null;
    }
    return { bytes, contentType: index.contentType };
  } catch {
    return null;
  }
}

async function renameReplacing(source: string, target: string): Promise<void> {
  try {
    await fs.rename(source, target);
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code;
    if (code !== "EEXIST" && code !== "EPERM") throw error;
    await fs.unlink(target).catch(() => {});
    await fs.rename(source, target);
  }
}

async function writeCachedResource(
  url: string,
  kind: GoogleFontsResourceKind,
  resource: CachedResource,
  options: NormalizedGoogleFontsCollectorOptions
): Promise<void> {
  const digest = sha256(resource.bytes);
  const objectsDirectory = path.join(options.cacheDirectory, "objects");
  const indexDirectory = path.join(options.cacheDirectory, "index");
  await Promise.all([
    fs.mkdir(objectsDirectory, { recursive: true }),
    fs.mkdir(indexDirectory, { recursive: true }),
  ]);
  const contentPath = path.join(
    objectsDirectory,
    `${digest}${cacheExtension(kind)}`
  );
  const contentTemporary = `${contentPath}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(contentTemporary, resource.bytes, {
      flag: "wx",
      mode: 0o600,
    });
    await renameReplacing(contentTemporary, contentPath);
  } finally {
    await fs.unlink(contentTemporary).catch(() => {});
  }

  const indexKey = sha256(`${kind}\0${url}\0${CSS_USER_AGENT}`);
  const indexPath = path.join(indexDirectory, `${indexKey}.json`);
  const indexTemporary = `${indexPath}.${randomUUID()}.tmp`;
  const index: CacheIndex = {
    schemaVersion: CACHE_SCHEMA_VERSION,
    kind,
    url,
    sha256: digest,
    contentType: resource.contentType,
    size: resource.bytes.length,
  };
  try {
    await fs.writeFile(indexTemporary, JSON.stringify(index), {
      flag: "wx",
      mode: 0o600,
    });
    await renameReplacing(indexTemporary, indexPath);
  } finally {
    await fs.unlink(indexTemporary).catch(() => {});
  }
}

async function fetchValidatedResource(
  initialUrl: string,
  kind: GoogleFontsResourceKind,
  options: NormalizedGoogleFontsCollectorOptions
): Promise<CachedResource> {
  const validatedInitial = validateResourceUrl(initialUrl, kind);
  const cached = await readCachedResource(
    validatedInitial.href,
    kind,
    options
  );
  if (cached) return cached;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeoutMs);
  let current = validatedInitial;
  try {
    for (let redirects = 0; ; redirects += 1) {
      if (redirects > options.maxRedirects) {
        throw new Error("google-font-resource-too-many-redirects");
      }
      // Deliberately omit Cookie, Authorization, Proxy-Authorization, Referer,
      // and all incoming request headers. The stylesheet UA is fixed so cache
      // results are deterministic and Google serves WOFF2.
      const response = await options.fetchImpl(current.href, {
        method: "GET",
        headers: {
          Accept: kind === "css" ? "text/css" : "font/woff2",
          "User-Agent": CSS_USER_AGENT,
        },
        redirect: "manual",
        cache: "no-store",
        credentials: "omit",
        signal: controller.signal,
      });
      if (
        response.status === 301 ||
        response.status === 302 ||
        response.status === 303 ||
        response.status === 307 ||
        response.status === 308
      ) {
        const location = response.headers.get("location");
        if (!location) throw new Error("google-font-redirect-without-location");
        const redirected = new URL(location, current);
        // Every hop is validated against the same exact-host policy.
        current = validateResourceUrl(redirected.href, kind);
        continue;
      }
      if (!response.ok) {
        throw new Error(`google-font-resource-http-${response.status}`);
      }
      const contentType = canonicalContentType(
        response.headers.get("content-type")
      );
      const allowedTypes =
        kind === "css"
          ? ALLOWED_CSS_CONTENT_TYPES
          : ALLOWED_WOFF2_CONTENT_TYPES;
      if (!allowedTypes.has(contentType)) {
        throw new Error(`google-font-${kind}-mime-not-allowed`);
      }
      const resource: CachedResource = {
        bytes: await readBoundedBytes(response, cacheLimit(kind, options)),
        contentType,
      };
      if (kind === "woff2" && !isWoff2(resource.bytes)) {
        throw new Error("google-font-woff2-magic-mismatch");
      }
      if (
        kind === "css" &&
        (resource.bytes.includes(0) ||
          resource.bytes.toString("utf8").includes("\uFFFD"))
      ) {
        throw new Error("google-font-css-invalid-utf8");
      }
      await writeCachedResource(
        validatedInitial.href,
        kind,
        resource,
        options
      );
      return resource;
    }
  } finally {
    clearTimeout(timer);
  }
}

function extractAttribute(tag: string, name: string): string | null {
  const pattern = new RegExp(
    `\\b${name}\\s*=\\s*(?:(["'])(.*?)\\1|([^\\s>]+))`,
    "i"
  );
  const match = pattern.exec(tag);
  return match ? (match[2] ?? match[3] ?? "") : null;
}

function extractGoogleFontsReferences(
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

function parseFontFaceBody(body: string): ParsedFontFace {
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

function collectSourceCodePoints(html: string): Set<number> {
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

function fontFaceCoversSource(
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

async function buildTrustedFontFace(
  parsed: ParsedFontFace,
  options: NormalizedGoogleFontsCollectorOptions
): Promise<TrustedFontFace> {
  const font = await fetchValidatedResource(parsed.fontUrl, "woff2", options);
  const dataUrl = `data:font/woff2;base64,${font.bytes.toString("base64")}`;
  const descriptors = [
    `font-family:'${escapeCssString(parsed.family)}'`,
    `font-style:${parsed.style}`,
    `font-weight:${parsed.weight}`,
    `font-stretch:${parsed.stretch}`,
    "font-display:block",
    `src:url('${dataUrl}') format('woff2')`,
  ];
  if (parsed.unicodeRange) {
    descriptors.push(`unicode-range:${parsed.unicodeRange}`);
  }
  return {
    css: `@font-face{${descriptors.join(";")}}`,
    fontBytes: font.bytes.length,
    family: parsed.family,
  };
}

async function buildTrustedFontFaces(
  css: string,
  options: NormalizedGoogleFontsCollectorOptions,
  sourceCodePoints: Set<number>
): Promise<TrustedFontFace[]> {
  const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const blocks = [...withoutComments.matchAll(/@font-face\s*\{([^{}]*)\}/gi)];
  const outside = withoutComments.replace(/@font-face\s*\{[^{}]*\}/gi, "").trim();
  if (!blocks.length || outside) {
    throw new Error("google-font-css-contains-unsupported-rules");
  }
  const parsedFaces = blocks
    .map((block) => parseFontFaceBody(block[1]))
    .filter((face) => fontFaceCoversSource(face, sourceCodePoints));
  if (parsedFaces.length > options.maxFontFiles) {
    throw new Error("google-font-css-has-too-many-fonts");
  }

  const trusted: TrustedFontFace[] = [];
  let totalBytes = 0;
  for (const parsed of parsedFaces) {
    const face = await buildTrustedFontFace(parsed, options);
    totalBytes += face.fontBytes;
    if (totalBytes > options.maxTotalFontBytes) {
      throw new Error("google-font-total-size-limit-exceeded");
    }
    trusted.push(face);
  }
  return trusted;
}

function injectTrustedCss(html: string, css: string): string {
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

function errorReason(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "AbortError") return "google-font-fetch-timeout";
    return error.message || "google-font-collection-failed";
  }
  return "google-font-collection-failed";
}

/**
 * Server-only Google Fonts collector for authored-hybrid export. Chrome remains
 * fully offline: all network access happens here, before preflight/Chrome.
 */
export class GoogleFontsCollector {
  readonly options: NormalizedGoogleFontsCollectorOptions;

  constructor(options: GoogleFontsCollectorOptions) {
    if (!path.isAbsolute(options.cacheDirectory)) {
      throw new Error("google font cache directory must be absolute");
    }
    this.options = {
      cacheDirectory: path.resolve(options.cacheDirectory),
      fetchImpl: options.fetchImpl ?? fetch,
      timeoutMs: positiveInteger(
        options.timeoutMs,
        DEFAULT_TIMEOUT_MS,
        "timeoutMs"
      ),
      maxCssBytes: positiveInteger(
        options.maxCssBytes,
        DEFAULT_MAX_CSS_BYTES,
        "maxCssBytes"
      ),
      maxFontBytes: positiveInteger(
        options.maxFontBytes,
        DEFAULT_MAX_FONT_BYTES,
        "maxFontBytes"
      ),
      maxTotalFontBytes: positiveInteger(
        options.maxTotalFontBytes,
        DEFAULT_MAX_TOTAL_FONT_BYTES,
        "maxTotalFontBytes"
      ),
      maxRedirects: positiveInteger(
        options.maxRedirects,
        DEFAULT_MAX_REDIRECTS,
        "maxRedirects"
      ),
      maxFontFiles: positiveInteger(
        options.maxFontFiles,
        DEFAULT_MAX_FONT_FILES,
        "maxFontFiles"
      ),
    };
  }

  async prepareHtml(html: string): Promise<PreparedGoogleFontsHtml> {
    const extracted = extractGoogleFontsReferences(html);
    if (
      !extracted.stylesheets.length &&
      !extracted.inlineFontFaceBodies.length
    ) {
      return {
        htmlForPreflight: extracted.htmlForPreflight,
        htmlForChrome: extracted.htmlForPreflight,
        status: "not-requested",
        usedFallback: false,
        requestedStylesheets: 0,
        requestedFontFaces: 0,
        loadedStylesheets: 0,
        fontFiles: 0,
        trustedFamilies: [],
        failures: [],
      };
    }

    const failures: GoogleFontsCollectionFailure[] = [];
    const trustedCss: string[] = [];
    const trustedFamilies = new Set<string>();
    let loadedStylesheets = 0;
    let loadedInlineFontFaces = 0;
    let fontFiles = 0;
    let totalFontBytes = 0;
    const sourceCodePoints = collectSourceCodePoints(
      extracted.htmlForPreflight
    );
    for (const stylesheetUrl of extracted.stylesheets) {
      try {
        const stylesheet = await fetchValidatedResource(
          stylesheetUrl,
          "css",
          this.options
        );
        const faces = await buildTrustedFontFaces(
          stylesheet.bytes.toString("utf8"),
          this.options,
          sourceCodePoints
        );
        const stylesheetFontBytes = faces.reduce(
          (total, face) => total + face.fontBytes,
          0
        );
        if (
          fontFiles + faces.length > this.options.maxFontFiles ||
          totalFontBytes + stylesheetFontBytes >
            this.options.maxTotalFontBytes
        ) {
          throw new Error("google-font-total-size-limit-exceeded");
        }
        trustedCss.push(...faces.map((face) => face.css));
        faces.forEach((face) => trustedFamilies.add(face.family));
        loadedStylesheets += 1;
        fontFiles += faces.length;
        totalFontBytes += stylesheetFontBytes;
      } catch (error) {
        failures.push({
          url: stylesheetUrl,
          reason: errorReason(error),
        });
      }
    }
    for (const body of extracted.inlineFontFaceBodies) {
      let parsed: ParsedFontFace | null = null;
      try {
        parsed = parseFontFaceBody(body);
        const face = await buildTrustedFontFace(parsed, this.options);
        if (
          fontFiles + 1 > this.options.maxFontFiles ||
          totalFontBytes + face.fontBytes > this.options.maxTotalFontBytes
        ) {
          throw new Error("google-font-total-size-limit-exceeded");
        }
        trustedCss.push(face.css);
        trustedFamilies.add(face.family);
        fontFiles += 1;
        totalFontBytes += face.fontBytes;
        loadedInlineFontFaces += 1;
      } catch (error) {
        failures.push({
          url: parsed?.fontUrl ?? "inline-google-font-face",
          reason: errorReason(error),
        });
      }
    }
    const loadedResources = loadedStylesheets + loadedInlineFontFaces;
    const status: GoogleFontsCollectionStatus =
      failures.length === 0
        ? "success"
        : loadedResources > 0
          ? "partial"
          : "unavailable";
    return {
      htmlForPreflight: extracted.htmlForPreflight,
      htmlForChrome: injectTrustedCss(
        extracted.htmlForPreflight,
        trustedCss.join("\n")
      ),
      status,
      reason: failures[0]?.reason,
      usedFallback: failures.length > 0,
      requestedStylesheets: extracted.stylesheets.length,
      requestedFontFaces: extracted.inlineFontFaceBodies.length,
      loadedStylesheets,
      fontFiles,
      trustedFamilies: [...trustedFamilies],
      failures,
    };
  }
}
