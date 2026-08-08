import { cloneLayout, isRecord, readArray, readString, type UnknownRecord } from "./core";

export function sentenceCase(value: string) {
  return value
    ? `${value.charAt(0).toUpperCase()}${value.slice(1)}`
    : value;
}

export function setElementRunsText(element: UnknownRecord, text: string) {
  const runs = readArray(element.runs).filter(isRecord);
  if (runs.length === 0) {
    element.runs = [{ text }];
    return;
  }
  element.runs = runs.map((run, index) => ({
    ...run,
    text: index === 0 ? text : "",
  }));
}

export function setRunsTextOnValue(value: unknown, text: string) {
  if (Array.isArray(value)) {
    if (value.length === 0) value.push({ text });
    value.forEach((run, index) => {
      if (isRecord(run)) run.text = index === 0 ? text : "";
    });
    return;
  }

  if (!isRecord(value)) return;
  if (!Array.isArray(value.runs)) value.runs = [{ text }];
  setElementRunsText(value, text);
}

export function cellWithText(source: unknown, text: string) {
  const cell = cloneLayout(isRecord(source) ? source : { runs: [{ text: "" }] });
  setElementRunsText(cell, text);
  return cell;
}

export function humanize(value: string) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function textRunsToString(runs: unknown) {
  return readArray(runs)
    .map((run) => (isRecord(run) ? readString(run.text) : ""))
    .join("");
}

export function textListItemsToString(items: unknown) {
  return readArray(items)
    .map((item) => textRunsToString(item))
    .filter((line) => line.trim().length > 0)
    .join("\n");
}

export function updateTextRuns(element: UnknownRecord, value: string) {
  const runs = readArray(element.runs).filter(isRecord);
  const firstRun = runs[0] ?? {};
  element.runs = [{ ...firstRun, text: value }];
}

export function updateTextListItems(element: UnknownRecord, value: string) {
  const currentItems = readArray(element.items);
  const firstItem = readArray(currentItems[0]).filter(isRecord);
  const firstRun = firstItem[0] ?? {};
  const lines = value.split(/\r?\n/);
  element.items = lines.map((line) => [{ ...firstRun, text: line }]);
}
