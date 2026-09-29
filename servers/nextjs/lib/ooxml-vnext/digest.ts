import { sha256Hex } from "../render-service/isomorphic-sha256.ts";

/**
 * The one digest implementation for the OOXML vNext package pipeline.
 *
 * Every value here is persisted (package/relationship/closure digests,
 * inventory digests, asset sha256s, canonical body-grammar digests), so the
 * byte contract of each function is pinned by digest.test.ts and must not
 * change without versioning the digest it feeds.
 */

const encoder = new TextEncoder();

/** Lowercase hex SHA-256; strings hash as UTF-8, exactly like createHash().update(string). */
export function sha256(value: Uint8Array | string): string {
  return sha256Hex(typeof value === "string" ? encoder.encode(value) : value);
}

/** `sha256:<hex>` as the master/scene contracts spell it. */
export function prefixedSha256(value: Uint8Array | string): string {
  return `sha256:${sha256(value)}`;
}

function framedDigest(sortedRecords: readonly string[]): string {
  const chunks: Uint8Array[] = [];
  let length = 0;
  for (const record of sortedRecords) {
    const bytes = encoder.encode(record);
    for (const chunk of [encoder.encode(`${bytes.length}:`), bytes, new Uint8Array([0])]) {
      chunks.push(chunk);
      length += chunk.length;
    }
  }
  const framed = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    framed.set(chunk, offset);
    offset += chunk.length;
  }
  return sha256Hex(framed);
}

/**
 * Order-independent digest of a record set: records sorted by UTF-16 code
 * unit, each framed as `<byteLength>:<bytes>\0`. Package graph, preservation
 * and closure digests use this ordering.
 */
export function stableDigest(records: readonly string[]): string {
  return framedDigest([...records].sort());
}

/**
 * Same framing as stableDigest, but sorted with `localeCompare(_, "en")`.
 * Master inventory digests were issued with this ordering and are persisted,
 * so the two cannot be unified without re-versioning them.
 */
export function localeStableDigest(records: readonly string[]): string {
  return framedDigest(
    [...records].sort((left, right) => left.localeCompare(right, "en")),
  );
}

/** Key-sorted JSON with no whitespace; throws on values JSON cannot encode. */
export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  const encoded = JSON.stringify(value);
  if (encoded === undefined) throw new Error("Body grammar is not canonical JSON");
  return encoded;
}
