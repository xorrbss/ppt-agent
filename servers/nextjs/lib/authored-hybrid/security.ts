// Collected webfonts are base64-inlined before the document reaches Chrome.
// The collector caps decoded font data at 24 MiB; 40 MiB covers base64
// expansion while preserving a hard upper bound on temporary HTML allocation.
const MAX_AUTHORED_HTML_BYTES = 40 * 1024 * 1024;
const MAX_DATA_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_DATA_FONT_BYTES = 8 * 1024 * 1024;

const DATA_IMAGE_PATTERN =
  /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})$/i;
const DATA_FONT_PATTERN =
  /^data:font\/(woff2|woff|otf|ttf);base64,([A-Za-z0-9+/]*={0,2})$/i;

export interface HybridHtmlPreflightResult {
  ok: boolean;
  reason?: string;
}

function decodedBase64Length(value: string): number {
  if (!value || value.length % 4 !== 0) return -1;
  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  return (value.length / 4) * 3 - padding;
}

/** Read a fetch response without first allowing an unbounded text allocation. */
export async function readBoundedResponseText(
  response: Response,
  maxBytes: number
): Promise<string> {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) {
    throw new Error("response byte limit is invalid");
  }
  const declaredLength = response.headers.get("content-length");
  if (declaredLength !== null) {
    const parsedLength = Number(declaredLength);
    if (
      !Number.isSafeInteger(parsedLength) ||
      parsedLength < 0 ||
      parsedLength > maxBytes
    ) {
      throw new Error("response exceeded the hybrid size limit");
    }
  }
  if (!response.body) return "";

  const reader = response.body.getReader();
  const chunks: Buffer[] = [];
  let totalBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      totalBytes += value.byteLength;
      if (totalBytes > maxBytes) {
        await reader.cancel().catch(() => {});
        throw new Error("response exceeded the hybrid size limit");
      }
      chunks.push(Buffer.from(value));
    }
  } finally {
    reader.releaseLock();
  }
  return Buffer.concat(chunks, totalBytes).toString("utf8");
}

/** Concatenate every CSS region (inline `<style>` blocks + `style=` attribute
 * values) so CSS-only guards run against style content instead of the whole
 * document — plain-text backslashes ("C:\\Users") or the word "expression"
 * in prose must not trip a CSS escape/expression check. */
function extractCssRegions(html: string): string {
  const regions: string[] = [];
  for (const match of html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)) {
    regions.push(match[1]);
  }
  for (const match of html.matchAll(
    /\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')/gi
  )) {
    regions.push(match[1] ?? match[2] ?? "");
  }
  return regions.join("\n");
}

/**
 * H1 renders a temporary HTML file in Chrome. Hybrid mode accepts only the
 * static, self-contained subset produced by the authored pipeline. Anything
 * active or network-bearing falls back to the already-rendered fidelity slide.
 */
export function preflightAuthoredHtmlForHybrid(
  html: string
): HybridHtmlPreflightResult {
  if (!html.trim()) return { ok: false, reason: "empty-html" };
  if (Buffer.byteLength(html, "utf8") > MAX_AUTHORED_HTML_BYTES) {
    return { ok: false, reason: "html-too-large" };
  }

  // Structural markup that is unsafe wherever it appears.
  const forbiddenMarkup = [
    /<\s*script\b/i,
    /<\s*(?:iframe|frame|frameset|object|embed|applet)\b/i,
    /<\s*(?:form|input|button|textarea|select)\b/i,
    /<\s*link\b/i,
    /<\s*base\b/i,
    /<\s*foreignObject\b/i,
    /<\s*meta\b[^>]*http-equiv\s*=\s*["']?refresh\b/i,
    /(?:\s|\/)(?:on[a-z0-9_-]+|srcdoc)\s*=/i,
    /\b(?:srcset|imagesrcset|ping)\s*=/i,
  ];
  if (forbiddenMarkup.some((pattern) => pattern.test(html))) {
    return { ok: false, reason: "active-or-external-html" };
  }

  // Dangerous URL protocols only matter inside a URL context — an attribute
  // value (after `=` or a quote) or a CSS url(). Anchoring to that context
  // avoids rejecting ordinary prose like "Profile:" or "Dockerfile:".
  if (/["'=(]\s*(?:javascript|vbscript|file|blob|ftp):/i.test(html)) {
    return { ok: false, reason: "active-or-external-html" };
  }

  // CSS-only constructs (escape obfuscation, remote/vector fetches, IE
  // expressions) are checked against style regions only.
  const css = extractCssRegions(html);
  const forbiddenCss = [
    /@import\b/i,
    /\b(?:-webkit-)?image-set\s*\(/i,
    /\\(?:[0-9a-f]{1,6}[ \t\r\n\f]?|[^\r\n\f0-9a-f])/i,
    /expression\s*\(/i,
  ];
  if (forbiddenCss.some((pattern) => pattern.test(css))) {
    return { ok: false, reason: "active-or-external-html" };
  }

  // Every CSS url() must be an embedded safe raster or a magic-validated font
  // collected by the server. Static SVG markup itself remains eligible for the
  // backplate, but SVG data URLs are intentionally not.
  const cssUrlPattern = /url\(\s*(["']?)(.*?)\1\s*\)/gis;
  for (const match of html.matchAll(cssUrlPattern)) {
    const value = match[2].trim();
    // Same-document SVG paint servers and markers (for example
    // marker-end:url(#arrow)) do not load external content.
    if (/^#[A-Za-z_][A-Za-z0-9_.:-]*$/.test(value)) continue;
    const result = value.toLowerCase().startsWith("data:font/")
      ? validateHybridDataFontUrl(value)
      : validateHybridDataImageUrl(value);
    if (!result.ok) return { ok: false, reason: "unsafe-css-url" };
  }

  const urlAttributePattern =
    /\b(src|href|poster|action|formaction|xlink:href)\s*=\s*(?:(["'])(.*?)\2|([^\s>]+))/gis;
  for (const match of html.matchAll(urlAttributePattern)) {
    const attribute = match[1].toLowerCase();
    const value = (match[3] ?? match[4] ?? "").trim();
    if (attribute.endsWith("href") && value.startsWith("#")) continue;
    if (attribute !== "src") {
      return { ok: false, reason: "unsafe-url-attribute" };
    }
    const result = validateHybridDataImageUrl(value);
    if (!result.ok) return { ok: false, reason: "unsafe-image-source" };
  }

  return { ok: true };
}

export type HybridDataImageResult =
  | { ok: true; mime: "png" | "jpeg" | "webp"; bytes: Buffer }
  | { ok: false; reason: string };

export function validateHybridDataImageUrl(
  source: string
): HybridDataImageResult {
  const match = DATA_IMAGE_PATTERN.exec(source.trim());
  if (!match) return { ok: false, reason: "unsupported-image-url" };
  const length = decodedBase64Length(match[2]);
  if (length < 0 || length > MAX_DATA_IMAGE_BYTES) {
    return { ok: false, reason: "invalid-image-size" };
  }
  const bytes = Buffer.from(match[2], "base64");
  if (bytes.length !== length) return { ok: false, reason: "invalid-base64" };

  const mime = match[1].toLowerCase() as "png" | "jpeg" | "webp";
  const magicMatches =
    (mime === "png" &&
      bytes.length >= 8 &&
      bytes.subarray(0, 8).equals(
        Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
      )) ||
    (mime === "jpeg" &&
      bytes.length >= 3 &&
      bytes[0] === 0xff &&
      bytes[1] === 0xd8 &&
      bytes[2] === 0xff) ||
    (mime === "webp" &&
      bytes.length >= 12 &&
      bytes.toString("ascii", 0, 4) === "RIFF" &&
      bytes.toString("ascii", 8, 12) === "WEBP");
  return magicMatches
    ? { ok: true, mime, bytes }
    : { ok: false, reason: "image-mime-mismatch" };
}

export type HybridDataFontResult =
  | {
      ok: true;
      mime: "woff2" | "woff" | "otf" | "ttf";
      bytes: Buffer;
    }
  | { ok: false; reason: string };

export function validateHybridDataFontUrl(
  source: string
): HybridDataFontResult {
  const match = DATA_FONT_PATTERN.exec(source.trim());
  if (!match) return { ok: false, reason: "unsupported-font-url" };
  const length = decodedBase64Length(match[2]);
  if (length < 0 || length > MAX_DATA_FONT_BYTES) {
    return { ok: false, reason: "invalid-font-size" };
  }
  const bytes = Buffer.from(match[2], "base64");
  if (bytes.length !== length) return { ok: false, reason: "invalid-base64" };

  const mime = match[1].toLowerCase() as "woff2" | "woff" | "otf" | "ttf";
  const magicMatches =
    (mime === "woff2" &&
      bytes.length >= 4 &&
      bytes.toString("ascii", 0, 4) === "wOF2") ||
    (mime === "woff" &&
      bytes.length >= 4 &&
      bytes.toString("ascii", 0, 4) === "wOFF") ||
    (mime === "otf" &&
      bytes.length >= 4 &&
      bytes.toString("ascii", 0, 4) === "OTTO") ||
    (mime === "ttf" &&
      bytes.length >= 4 &&
      bytes[0] === 0x00 &&
      bytes[1] === 0x01 &&
      bytes[2] === 0x00 &&
      bytes[3] === 0x00);
  return magicMatches
    ? { ok: true, mime, bytes }
    : { ok: false, reason: "font-mime-mismatch" };
}
