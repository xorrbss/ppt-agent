import { createHash } from "node:crypto";

function xmlUnescape(value: string): string {
  return value
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'")
    .replaceAll("&gt;", ">")
    .replaceAll("&lt;", "<")
    .replaceAll("&amp;", "&");
}

export function attributes(source: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const match of source.matchAll(/([\w:.-]+)="([^"]*)"/g)) {
    result[match[1]] = xmlUnescape(match[2]);
  }
  return result;
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right, "en"))
        .map(([key, child]) => [key, stableValue(child)])
    );
  }
  return value;
}

export function stableJson(value: unknown): string {
  return JSON.stringify(stableValue(value));
}

export function digest(value: unknown): string {
  const payload = typeof value === "string" ? value : stableJson(value);
  return `sha256:${createHash("sha256").update(payload).digest("hex")}`;
}

export function requirePart(parts: ReadonlyMap<string, Uint8Array>, partName: string): string {
  const value = parts.get(partName);
  if (!value) throw new Error(`Deck master read-back is missing ${partName}`);
  return Buffer.from(value).toString("utf8");
}
