import type { JsonRecord } from "./types";

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function readOptionalBoolean(value: unknown, fallback: boolean): boolean {
  return value == null ? fallback : readBoolean(value);
}

export function readRecord(value: unknown): JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

export function readRecordOrNull(value: unknown): JsonRecord | null {
  const record = readRecord(value);
  return Object.keys(record).length ? record : null;
}

export function hasOwn(record: JsonRecord, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

export function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function readStringValueOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export function readStringValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

export function readNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function readBoolean(value: unknown): boolean {
  return value === true || value === "true" || value === "1";
}
