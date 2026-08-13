/** Bounded data-URL and response-body primitives for authored rendering. */

export const AUTHORED_RENDER_LIMITS = Object.freeze({
  maxHtmlBytes: 2 * 1024 * 1024,
  maxNodes: 5_000,
  maxDomDepth: 64,
  maxAttributesPerElement: 64,
  maxTextNodeBytes: 512 * 1024,
  maxDataImageBytes: 8 * 1024 * 1024,
  maxAggregateAssetBytes: 24 * 1024 * 1024,
  maxImagePixels: 16_777_216,
  maxImageDimension: 8_192,
  maxDataFontBytes: 8 * 1024 * 1024,
  maxAggregateFontBytes: 16 * 1024 * 1024,
  maxFontFaces: 16,
  maxFontFamilies: 32,
});

const DATA_IMAGE_PATTERN =
  /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]*={0,2})$/i;
const DATA_FONT_PATTERN =
  /^data:font\/(woff2|woff|otf|ttf);base64,([A-Za-z0-9+/]*={0,2})$/i;

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

export type HybridDataImageResult =
  | { ok: true; mime: "png" | "jpeg" | "webp"; bytes: Buffer }
  | { ok: false; reason: string };

export function validateHybridDataImageUrl(
  source: string
): HybridDataImageResult {
  const match = DATA_IMAGE_PATTERN.exec(source.trim());
  if (!match) return { ok: false, reason: "unsupported-image-url" };
  const length = decodedBase64Length(match[2]);
  if (length < 0 || length > AUTHORED_RENDER_LIMITS.maxDataImageBytes) {
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
  if (length < 0 || length > AUTHORED_RENDER_LIMITS.maxDataFontBytes) {
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
