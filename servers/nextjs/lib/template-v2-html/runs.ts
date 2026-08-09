import { normalizeRawTextMarkdownElement } from "@/components/slide-editor/text/template-v2-text";
import type { JsonRecord } from "./types";
import { readArray, readRecord, readStringValue } from "./values";

function readRuns(item: JsonRecord): JsonRecord[] {
  const runs = readArray(item.runs).map(readRecord);
  return runs.length ? runs : [{ text: readStringValue(item.text) }];
}

export function normalizedRunsForHtml(item: JsonRecord, fallbackFont: unknown): JsonRecord[] {
  const runs = readRuns(item);
  return normalizeRunsForHtml(
    runs,
    Object.prototype.hasOwnProperty.call(item, "text")
      ? item.text
      : joinedRunText(runs),
    item.font ?? fallbackFont,
  );
}

export function normalizedListRunsForHtml(value: unknown, fallbackFont: unknown): JsonRecord[] {
  const runs = readListRuns(value);
  const record = readRecord(value);
  return normalizeRunsForHtml(
    runs,
    Object.prototype.hasOwnProperty.call(record, "text")
      ? record.text
      : joinedRunText(runs),
    record.font ?? fallbackFont,
  );
}

export function normalizeRunsForHtml(
  runs: JsonRecord[],
  text: unknown,
  fallbackFont: unknown,
): JsonRecord[] {
  const normalized = normalizeRawTextMarkdownElement({
    type: "text",
    font: fallbackFont,
    text: readStringValue(text),
    runs,
  }).runs;

  return normalized.map((run) => ({
    text: run.text,
    font: run.font,
  }));
}

export function joinedRunText(runs: JsonRecord[]): string {
  return runs.map((run) => readStringValue(run.text)).join("");
}

function readListRuns(value: unknown): JsonRecord[] {
  if (Array.isArray(value)) return value.map(readRecord);
  const record = readRecord(value);
  const runs = readArray(record.runs).map(readRecord);
  if (runs.length) return runs;
  if (Object.prototype.hasOwnProperty.call(record, "text")) {
    return [{ text: readStringValue(record.text), font: record.font }];
  }
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return [{ text: readStringValue(value) }];
  }
  return [];
}
