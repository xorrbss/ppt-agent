import { preflightAuthoredHtmlForHybrid } from "./security.ts";

const GOOGLE_CSS_HOST = "fonts.googleapis.com";
const GOOGLE_FONT_HOST = "fonts.gstatic.com";
const MAX_REDIRECTS = 3;
const MAX_STYLESHEET_BYTES = 256 * 1024;
const MAX_FONT_BYTES = 8 * 1024 * 1024;
const MAX_TOTAL_FONT_BYTES = 24 * 1024 * 1024;
// Google serves large CJK families as many small unicode-range subsets (Noto
// Sans KR currently exceeds 100). Total decoded bytes remains the primary DoS
// bound; this count cap prevents pathological request fan-out.
const MAX_FONT_FILES = 256;
const MAX_STYLESHEET_REFERENCES = 8;
const DEFAULT_TIMEOUT_MS = 8_000;
const STYLESHEET_CACHE_LIMIT = 32;

type FetchLike = typeof fetch;

interface CollectedStylesheet {
  css: string;
  fontFiles: number;
  fontBytes: number;
}

export interface GoogleFontCollectionFailure {
  source: "link" | "import";
  reason: string;
}

export interface GoogleFontCollectionResult {
  html: string;
  collectedStylesheets: number;
  collectedFontFiles: number;
  cacheHits: number;
  failures: GoogleFontCollectionFailure[];
}

export interface GoogleFontCollectorOptions {
  fetchImpl?: FetchLike;
  timeoutMs?: number;
}

const stylesheetCache = new Map<string, Promise<CollectedStylesheet>>();

export function clearGoogleFontCollectorCacheForTests(): void {
  stylesheetCache.clear();
}

function assertAllowedUrl(value: string, kind: "css" | "font"): URL {
  const parsed = new URL(value);
  const host = kind === "css" ? GOOGLE_CSS_HOST : GOOGLE_FONT_HOST;
  if (
    parsed.protocol !== "https:" ||
    parsed.hostname.toLowerCase() !== host ||
    parsed.username ||
    parsed.password
  ) {
    throw new Error(`disallowed-${kind}-url`);
  }
  return parsed;
}

async function fetchWithValidatedRedirects(
  source: string,
  kind: "css" | "font",
  fetchImpl: FetchLike,
  timeoutMs: number
): Promise<Response> {
  let current = assertAllowedUrl(source, kind);
  for (let redirect = 0; redirect <= MAX_REDIRECTS; redirect += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    let response: Response;
    try {
      response = await fetchImpl(current, {
        redirect: "manual",
        signal: controller.signal,
        headers:
          kind === "css"
            ? {
                accept: "text/css,*/*;q=0.1",
                "user-agent":
                  "Mozilla/5.0 AppleWebKit/537.36 Chrome/126 Safari/537.36",
              }
            : { accept: "font/woff2,font/woff,*/*;q=0.1" },
      });
    } finally {
      clearTimeout(timer);
    }
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      if (redirect === MAX_REDIRECTS) throw new Error("too-many-redirects");
      const location = response.headers.get("location");
      if (!location) throw new Error("redirect-without-location");
      current = assertAllowedUrl(new URL(location, current).href, kind);
      continue;
    }
    if (!response.ok) throw new Error(`upstream-http-${response.status}`);
    return response;
  }
  throw new Error("too-many-redirects");
}

async function readBoundedBytes(
  response: Response,
  maxBytes: number
): Promise<Buffer> {
  const declared = response.headers.get("content-length");
  if (declared !== null) {
    const parsed = Number(declared);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > maxBytes) {
      throw new Error("upstream-size-limit");
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
        throw new Error("upstream-size-limit");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks, total);
}

function fontMimeFromMagic(bytes: Buffer): string | undefined {
  if (bytes.length >= 4 && bytes.toString("ascii", 0, 4) === "wOF2") {
    return "font/woff2";
  }
  if (bytes.length >= 4 && bytes.toString("ascii", 0, 4) === "wOFF") {
    return "font/woff";
  }
  if (bytes.length >= 4 && bytes.toString("ascii", 0, 4) === "OTTO") {
    return "font/otf";
  }
  if (
    bytes.length >= 4 &&
    bytes[0] === 0x00 &&
    bytes[1] === 0x01 &&
    bytes[2] === 0x00 &&
    bytes[3] === 0x00
  ) {
    return "font/ttf";
  }
  return undefined;
}

function contentType(response: Response): string {
  return (response.headers.get("content-type") ?? "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
}

function fontMimeAllowed(declared: string, detected: string): boolean {
  if (declared === "application/octet-stream") return true;
  const aliases = new Map<string, readonly string[]>([
    ["font/woff2", ["font/woff2", "application/font-woff2"]],
    ["font/woff", ["font/woff", "application/font-woff"]],
    ["font/otf", ["font/otf", "application/vnd.ms-opentype"]],
    ["font/ttf", ["font/ttf", "application/x-font-ttf"]],
  ]);
  return (aliases.get(detected) ?? []).includes(declared);
}

async function replaceAsync(
  value: string,
  pattern: RegExp,
  replacer: (match: RegExpExecArray) => Promise<string>
): Promise<string> {
  const matches = [...value.matchAll(pattern)];
  if (!matches.length) return value;
  let result = "";
  let cursor = 0;
  for (const match of matches) {
    const index = match.index ?? 0;
    result += value.slice(cursor, index);
    result += await replacer(match);
    cursor = index + match[0].length;
  }
  return result + value.slice(cursor);
}

async function collectStylesheetUncached(
  stylesheetUrl: string,
  fetchImpl: FetchLike,
  timeoutMs: number
): Promise<CollectedStylesheet> {
  const cssResponse = await fetchWithValidatedRedirects(
    stylesheetUrl,
    "css",
    fetchImpl,
    timeoutMs
  );
  if (contentType(cssResponse) !== "text/css") {
    throw new Error("invalid-css-mime");
  }
  const css = (await readBoundedBytes(cssResponse, MAX_STYLESHEET_BYTES)).toString(
    "utf8"
  );
  if (/@import\b/i.test(css)) throw new Error("nested-css-import");

  let fontFiles = 0;
  let fontBytes = 0;
  const inlined = await replaceAsync(
    css,
    /url\(\s*(["']?)(https:[^)'" \t\r\n]+)\1\s*\)/gi,
    async (match) => {
      const assetUrl = assertAllowedUrl(match[2], "font").href;
      if (fontFiles >= MAX_FONT_FILES) throw new Error("font-file-count-limit");
      const fontResponse = await fetchWithValidatedRedirects(
        assetUrl,
        "font",
        fetchImpl,
        timeoutMs
      );
      const bytes = await readBoundedBytes(fontResponse, MAX_FONT_BYTES);
      const detectedMime = fontMimeFromMagic(bytes);
      if (!detectedMime) throw new Error("invalid-font-magic");
      if (!fontMimeAllowed(contentType(fontResponse), detectedMime)) {
        throw new Error("invalid-font-mime");
      }
      fontFiles += 1;
      fontBytes += bytes.length;
      if (fontBytes > MAX_TOTAL_FONT_BYTES) {
        throw new Error("total-font-size-limit");
      }
      return `url("data:${detectedMime};base64,${bytes.toString("base64")}")`;
    }
  );
  if (/url\(\s*["']?https?:/i.test(inlined)) {
    throw new Error("external-url-remained");
  }
  return { css: inlined, fontFiles, fontBytes };
}

function cachedStylesheet(
  stylesheetUrl: string,
  fetchImpl: FetchLike,
  timeoutMs: number
): { promise: Promise<CollectedStylesheet>; cacheHit: boolean } {
  const key = assertAllowedUrl(stylesheetUrl, "css").href;
  const existing = stylesheetCache.get(key);
  if (existing) return { promise: existing, cacheHit: true };
  const promise = collectStylesheetUncached(key, fetchImpl, timeoutMs).catch(
    (error) => {
      stylesheetCache.delete(key);
      throw error;
    }
  );
  stylesheetCache.set(key, promise);
  while (stylesheetCache.size > STYLESHEET_CACHE_LIMIT) {
    const oldest = stylesheetCache.keys().next().value as string | undefined;
    if (!oldest) break;
    stylesheetCache.delete(oldest);
  }
  return { promise, cacheHit: false };
}

function decodeHtmlUrl(value: string): string {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&#38;", "&")
    .replaceAll("&#x26;", "&");
}

function googleStylesheetHref(tag: string): string | undefined {
  const relMatch =
    /\brel\s*=\s*(?:(["'])(.*?)\1|([^\s>]+))/i.exec(tag);
  const rel = relMatch?.[2] ?? relMatch?.[3] ?? "";
  const hrefMatch =
    /\bhref\s*=\s*(?:(["'])(.*?)\1|([^\s>]+))/i.exec(tag);
  const href = hrefMatch?.[2] ?? hrefMatch?.[3] ?? "";
  if (!/\bstylesheet\b/i.test(rel) || !href) return undefined;
  try {
    const parsed = new URL(decodeHtmlUrl(href));
    return parsed.protocol === "https:" &&
      parsed.hostname.toLowerCase() === GOOGLE_CSS_HOST
      ? href
      : undefined;
  } catch {
    return undefined;
  }
}

function passivePreflightHtml(html: string): {
  html: string;
  stylesheetReferences: number;
} {
  let stylesheetReferences = 0;
  let passive = html.replace(/<link\b[^>]*>/gi, (tag) => {
    if (!googleStylesheetHref(tag)) return tag;
    stylesheetReferences += 1;
    return "<!-- approved Google font stylesheet -->";
  });
  passive = passive.replace(
    /@import\s+(?:url\(\s*)?(["'])(https:\/\/fonts\.googleapis\.com\/[^"']+)\1\s*\)?[^;]*;/gi,
    () => {
      stylesheetReferences += 1;
      return "/* approved Google font import */";
    }
  );
  return { html: passive, stylesheetReferences };
}

function failureReason(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === "AbortError") return "network-timeout";
    return error.message.slice(0, 80);
  }
  return "font-collection-failed";
}

/**
 * Fetches approved Google Fonts resources in the trusted server process and
 * rewrites them as validated data URLs before the HTML reaches isolated Chrome.
 * Failed Google resources are removed so the shared local fallback policy can
 * render deterministically. Chrome itself remains fully offline.
 */
export async function collectGoogleFontsForAuthoredHtml(
  html: string,
  options: GoogleFontCollectorOptions = {}
): Promise<GoogleFontCollectionResult> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000) {
    throw new Error("invalid-font-fetch-timeout");
  }
  // Do not let active/otherwise rejected HTML trigger trusted-server network
  // activity. Only approved Google stylesheet references are removed for this
  // preliminary check; the fully collected result is preflighted again later.
  const passive = passivePreflightHtml(html);
  if (
    passive.stylesheetReferences > MAX_STYLESHEET_REFERENCES ||
    !preflightAuthoredHtmlForHybrid(passive.html).ok
  ) {
    return {
      html,
      collectedStylesheets: 0,
      collectedFontFiles: 0,
      cacheHits: 0,
      failures: [],
    };
  }
  let collectedStylesheets = 0;
  let collectedFontFiles = 0;
  let collectedFontBytes = 0;
  let cacheHits = 0;
  const failures: GoogleFontCollectionFailure[] = [];

  async function inlineStylesheet(
    url: string,
    source: "link" | "import"
  ): Promise<string> {
    try {
      const checked = assertAllowedUrl(decodeHtmlUrl(url), "css").href;
      const cached = cachedStylesheet(checked, fetchImpl, timeoutMs);
      if (cached.cacheHit) cacheHits += 1;
      const collected = await cached.promise;
      if (
        collectedFontFiles + collected.fontFiles > MAX_FONT_FILES ||
        collectedFontBytes + collected.fontBytes > MAX_TOTAL_FONT_BYTES
      ) {
        throw new Error("document-font-limit");
      }
      collectedStylesheets += 1;
      collectedFontFiles += collected.fontFiles;
      collectedFontBytes += collected.fontBytes;
      return collected.css;
    } catch (error) {
      failures.push({ source, reason: failureReason(error) });
      return "";
    }
  }

  let output = await replaceAsync(
    html,
    /<link\b[^>]*>/gi,
    async (match) => {
      const tag = match[0];
      const href = googleStylesheetHref(tag);
      if (!href) return tag;
      const css = await inlineStylesheet(href, "link");
      return css
        ? `<style data-presenton-google-fonts="collected">${css}</style>`
        : "<!-- Google Fonts collection failed; local fallback retained. -->";
    }
  );

  output = await replaceAsync(
    output,
    /@import\s+(?:url\(\s*)?(["'])(https:\/\/fonts\.googleapis\.com\/[^"']+)\1\s*\)?[^;]*;/gi,
    async (match) => {
      const css = await inlineStylesheet(match[2], "import");
      return css || "/* Google Fonts collection failed; local fallback retained. */";
    }
  );

  return {
    html: output,
    collectedStylesheets,
    collectedFontFiles,
    cacheHits,
    failures,
  };
}
