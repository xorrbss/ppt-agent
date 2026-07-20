const MAX_AUTHORED_HTML_BYTES = 2 * 1024 * 1024;
const MAX_DATA_IMAGE_BYTES = 8 * 1024 * 1024;

const DATA_IMAGE_PATTERN =
  /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})$/i;

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
    /@import\b/i,
    /\b(?:-webkit-)?image-set\s*\(/i,
    /\\(?:[0-9a-f]{1,6}[ \t\r\n\f]?|[^\r\n\f0-9a-f])/i,
    /(?:javascript|vbscript|file|blob|ftp):/i,
    /expression\s*\(/i,
  ];
  if (forbiddenMarkup.some((pattern) => pattern.test(html))) {
    return { ok: false, reason: "active-or-external-html" };
  }

  // Every CSS url() must be an embedded safe raster. Static SVG markup itself
  // remains eligible for the backplate, but SVG data URLs are intentionally not.
  const cssUrlPattern = /url\(\s*(["']?)(.*?)\1\s*\)/gis;
  for (const match of html.matchAll(cssUrlPattern)) {
    const result = validateHybridDataImageUrl(match[2].trim());
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
