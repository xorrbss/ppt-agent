/**
 * Stage 0 — value readers. Total-function accessors that coerce arbitrary JSON
 * from a template v2 payload into the primitives the later stages assume.
 */
import type {
  DataLabelPosition,
  LayoutAlignment,
} from "@/components/slide-editor/types";
import { TEMPLATE_V2_DATA_LABEL_POSITIONS } from "../../../../lib/template-v2-html/template-v2-contract.generated.ts";

export const MIN_ELEMENT_SIZE = 1;
export const MAX_BORDER_RADIUS = 128;

export type UnknownRecord = Record<string, unknown>;
export type AdaptedPosition = { x: number; y: number };
export type AdaptedSize = { width: number; height: number };

const LAYOUT_ALIGNMENT_VALUES = new Set([
  "flex-start",
  "flex-end",
  "center",
  "stretch",
]);
const DATA_LABEL_POSITIONS = new Set<string>(TEMPLATE_V2_DATA_LABEL_POSITIONS);

export function readValue(record: UnknownRecord, key: string) {
  return record[key];
}

export function readRecord(
  record: UnknownRecord | null | undefined,
  key: string,
) {
  return asRecord(record ? readValue(record, key) : null);
}

export function readArray(record: UnknownRecord, key: string) {
  const value = readValue(record, key);
  return Array.isArray(value) ? value : [];
}

export function readNumber(record: UnknownRecord, key: string) {
  return readRawNumber(readValue(record, key));
}

export function readRawNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function readBoolean(record: UnknownRecord, key: string) {
  const value = readValue(record, key);
  return typeof value === "boolean" ? value : null;
}

export function readDataLabelPosition(
  record: UnknownRecord,
  key: string,
): DataLabelPosition | null {
  const value = readValue(record, key);
  if (value === true) return "top";
  if (value === false || value == null) return null;
  const text = readString(value);
  return text && DATA_LABEL_POSITIONS.has(text)
    ? (text as DataLabelPosition)
    : null;
}

export function readDecorative(record: UnknownRecord): boolean | null {
  return readBoolean(record, "decorative");
}

export function readEnum<const T extends readonly string[]>(
  record: UnknownRecord,
  values: T,
  key: string,
): T[number] | null {
  const value = readString(readValue(record, key));
  return value && (values as readonly string[]).includes(value)
    ? (value as T[number])
    : null;
}

export function readLayoutAlignment(
  record: UnknownRecord,
  key: string,
): LayoutAlignment | null {
  const value = readString(readValue(record, key));
  return value && LAYOUT_ALIGNMENT_VALUES.has(value)
    ? (value as LayoutAlignment)
    : null;
}

export function readString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function readColor(value: unknown): string | null {
  const color = readString(value)?.trim();
  if (!color) return null;
  return /^#?[0-9A-Fa-f]{6}$/.test(color) ? color : null;
}

export function asRecord(value: unknown): UnknownRecord | null {
  return isRecord(value) ? value : null;
}

export function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function stripNullish<T extends UnknownRecord>(value: T): T {
  return Object.fromEntries(
    Object.entries(value).filter(
      ([, item]) =>
        item !== null &&
        item !== undefined &&
        (!Array.isArray(item) || item.length > 0),
    ),
  ) as T;
}

export function positiveInteger(value: number | null, fallback: number) {
  return Math.max(1, Math.trunc(value ?? fallback));
}

export function positiveIntegerOrNull(value: number | null) {
  return value == null ? null : positiveInteger(value, 1);
}

export function clampInteger(value: number | null, min: number, max: number) {
  return value == null ? null : Math.trunc(clamp(value, min, max));
}

export function clampOptional(value: number | null, min: number, max: number) {
  return value == null ? null : clamp(value, min, max);
}

export function clampOptionalInteger(
  value: number | null,
  min: number,
  max: number,
) {
  return value == null ? null : clamp(Math.round(value), min, max);
}

export function truncateString(value: string, maxLength: number) {
  return value.length > maxLength ? value.slice(0, maxLength) : value;
}

export function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function round(value: number) {
  return Math.round(value * 10000) / 10000;
}
