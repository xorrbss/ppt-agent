/**
 * Bounded transport and the hash-addressed disk cache behind the collector.
 *
 * Reads are size-capped before a body is buffered, content types and WOFF2
 * magic are enforced, and cached objects are re-verified against their digest
 * so a tampered cache entry fails closed rather than being served as trusted.
 */

import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

import {
  type GoogleFontsResourceKind,
  validateResourceUrl,
} from "./google-fonts-url-policy.ts";

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

export interface NormalizedGoogleFontsCollectorOptions {
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

export async function fetchValidatedResource(
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


/**
 * Resolve caller options against the transport limits enforced above, so the
 * defaults live next to the code that applies them.
 */
export function normalizeGoogleFontsCollectorOptions(
  options: GoogleFontsCollectorOptions
): NormalizedGoogleFontsCollectorOptions {
  if (!path.isAbsolute(options.cacheDirectory)) {
    throw new Error("google font cache directory must be absolute");
  }
  return {
    cacheDirectory: path.resolve(options.cacheDirectory),
    fetchImpl: options.fetchImpl ?? fetch,
    timeoutMs: positiveInteger(options.timeoutMs, DEFAULT_TIMEOUT_MS, "timeoutMs"),
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
