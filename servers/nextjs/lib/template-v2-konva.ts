import { isJsonRecord, type ElementGeometry, type JsonRecord } from "./template-v2-studio.ts";

export const TEMPLATE_V2_SLIDE_WIDTH = 1280;
export const TEMPLATE_V2_SLIDE_HEIGHT = 720;
export const MIN_TEMPLATE_V2_ZOOM = 0.25;
export const MAX_TEMPLATE_V2_ZOOM = 4;

export interface ViewportTransform {
  scale: number;
  x: number;
  y: number;
}

export interface ElementCapabilities {
  move: boolean;
  resize: boolean;
  rotate: boolean;
}

export interface TemplateV2TextRunStyle {
  fontSize: number;
  fontFamily: string;
  fontStyle: string;
  fill: string;
  opacity: number;
  lineHeight: number;
  letterSpacing: number;
  textDecoration: string;
}

export interface TemplateV2TextRun extends TemplateV2TextRunStyle {
  index: number;
  text: string;
}

export type TemplateV2TextLayout =
  | {
      mode: "runs";
      runs: Array<TemplateV2TextRun & { x: number; width: number }>;
    }
  | {
      mode: "fallback";
      reason: "no-runs" | "multiline" | "overflow";
      text: string;
      style: TemplateV2TextRunStyle;
    };

export function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

export function stringValue(value: unknown, fallback: string): string {
  return typeof value === "string" ? value : fallback;
}

export function elementPosition(element: JsonRecord) {
  const value = isJsonRecord(element.position) ? element.position : {};
  return { x: numberValue(value.x, 0), y: numberValue(value.y, 0) };
}

export function elementSize(
  element: JsonRecord,
  fallback = { width: 240, height: 80 }
) {
  const value = isJsonRecord(element.size) ? element.size : {};
  return {
    width: Math.max(1, numberValue(value.width, fallback.width)),
    height: Math.max(1, numberValue(value.height, fallback.height)),
  };
}

export function elementRotation(element: JsonRecord): number {
  return element.type === "group" ? 0 : numberValue(element.rotation, 0);
}

export function elementCapabilities(element: JsonRecord): ElementCapabilities {
  if (element.type === "group") {
    return { move: true, resize: false, rotate: false };
  }
  if (element.type === "vector") {
    return { move: true, resize: false, rotate: false };
  }
  if (
    [
      "text",
      "container",
      "image",
      "text-list",
      "table",
      "infographic",
      "chart",
      "flex",
      "grid",
    ].includes(String(element.type))
  ) {
    return { move: true, resize: true, rotate: true };
  }
  return { move: false, resize: false, rotate: false };
}

export function roundTemplateV2Value(value: number): number {
  return Math.round(value * 100) / 100;
}

export function normalizeRotation(value: number): number {
  const normalized = ((value % 360) + 360) % 360;
  return roundTemplateV2Value(normalized);
}

export function normalizeElementGeometry(
  element: JsonRecord,
  value: {
    x: number;
    y: number;
    width?: number;
    height?: number;
    scaleX?: number;
    scaleY?: number;
    rotation?: number;
  }
): ElementGeometry {
  const capabilities = elementCapabilities(element);
  const geometry: ElementGeometry = {
    x: roundTemplateV2Value(value.x),
    y: roundTemplateV2Value(value.y),
  };
  if (
    capabilities.resize &&
    value.width !== undefined &&
    value.height !== undefined
  ) {
    geometry.width = roundTemplateV2Value(
      Math.max(8, value.width * (value.scaleX ?? 1))
    );
    geometry.height = roundTemplateV2Value(
      Math.max(8, value.height * (value.scaleY ?? 1))
    );
  }
  if (capabilities.rotate && value.rotation !== undefined) {
    geometry.rotation = normalizeRotation(value.rotation);
  }
  return geometry;
}

export function fitTemplateV2Viewport(
  width: number,
  height: number,
  padding = 24
): ViewportTransform {
  const usableWidth = Math.max(1, width - padding * 2);
  const usableHeight = Math.max(1, height - padding * 2);
  const scale = Math.min(
    1,
    usableWidth / TEMPLATE_V2_SLIDE_WIDTH,
    usableHeight / TEMPLATE_V2_SLIDE_HEIGHT
  );
  return {
    scale,
    x: (width - TEMPLATE_V2_SLIDE_WIDTH * scale) / 2,
    y: (height - TEMPLATE_V2_SLIDE_HEIGHT * scale) / 2,
  };
}

export function preserveTemplateV2ViewportOnResize(
  viewport: ViewportTransform,
  previous: { width: number; height: number },
  next: { width: number; height: number }
): ViewportTransform {
  if (
    viewport.scale <= 0 ||
    previous.width <= 0 ||
    previous.height <= 0 ||
    next.width <= 0 ||
    next.height <= 0
  ) {
    return fitTemplateV2Viewport(next.width, next.height);
  }
  const logicalCenter = {
    x: (previous.width / 2 - viewport.x) / viewport.scale,
    y: (previous.height / 2 - viewport.y) / viewport.scale,
  };
  return {
    scale: viewport.scale,
    x: next.width / 2 - logicalCenter.x * viewport.scale,
    y: next.height / 2 - logicalCenter.y * viewport.scale,
  };
}

export function zoomTemplateV2Viewport(
  viewport: ViewportTransform,
  pointer: { x: number; y: number },
  requestedScale: number
): ViewportTransform {
  const scale = Math.min(
    MAX_TEMPLATE_V2_ZOOM,
    Math.max(MIN_TEMPLATE_V2_ZOOM, requestedScale)
  );
  const logical = {
    x: (pointer.x - viewport.x) / viewport.scale,
    y: (pointer.y - viewport.y) / viewport.scale,
  };
  return {
    scale,
    x: pointer.x - logical.x * scale,
    y: pointer.y - logical.y * scale,
  };
}

export function pathKey(path: Array<string | number>): string {
  return path.map((part) => `${typeof part}:${String(part)}`).join("/");
}

function textFont(element: JsonRecord, run?: JsonRecord): TemplateV2TextRunStyle {
  const elementFont = isJsonRecord(element.font) ? element.font : {};
  const runFont = run && isJsonRecord(run.font) ? run.font : {};
  const fill = isJsonRecord(element.fill) ? element.fill : {};
  const inheritedNumber = (key: string, fallback: number) =>
    numberValue(runFont[key], numberValue(elementFont[key], fallback));
  const inheritedString = (key: string, fallback: string) =>
    stringValue(runFont[key], stringValue(elementFont[key], fallback));
  const inheritedBoolean = (key: string) =>
    typeof runFont[key] === "boolean"
      ? runFont[key] === true
      : elementFont[key] === true;
  const styles = [
    inheritedBoolean("bold") ? "bold" : "",
    inheritedBoolean("italic") ? "italic" : "",
  ].filter(Boolean);
  return {
    fontSize: Math.max(6, inheritedNumber("size", 28)),
    fontFamily: inheritedString("family", "Arial"),
    fontStyle: styles.join(" ") || "normal",
    fill: inheritedString("color", stringValue(fill.color, "#0f172a")),
    opacity: inheritedNumber(
      "opacity",
      numberValue(fill.opacity, numberValue(element.opacity, 1))
    ),
    lineHeight: inheritedNumber("line_height", 1.2),
    letterSpacing: inheritedNumber("letter_spacing", 0),
    textDecoration: inheritedBoolean("underline") ? "underline" : "",
  };
}

export function layoutTemplateV2TextRuns(
  element: JsonRecord,
  availableWidth: number,
  measureText: (run: TemplateV2TextRun) => number
): TemplateV2TextLayout {
  const rawRuns = Array.isArray(element.runs)
    ? element.runs.filter(isJsonRecord)
    : [];
  const runs = rawRuns.map((run, index): TemplateV2TextRun => ({
    index,
    text: stringValue(run.text, ""),
    ...textFont(element, run),
  }));
  const text = runs.map((run) => run.text).join("");
  const fallback = (
    reason: Extract<TemplateV2TextLayout, { mode: "fallback" }>["reason"]
  ): TemplateV2TextLayout => ({
    mode: "fallback",
    reason,
    text,
    style: textFont(element),
  });

  if (runs.length === 0) return fallback("no-runs");
  if (runs.some((run) => /[\r\n]/.test(run.text))) {
    return fallback("multiline");
  }

  let x = 0;
  const positioned = runs.map((run) => {
    const measured = measureText(run);
    const width = Number.isFinite(measured) ? Math.max(0, measured) : 0;
    const positionedRun = { ...run, x, width };
    x += width;
    return positionedRun;
  });
  if (x > Math.max(0, availableWidth)) return fallback("overflow");
  return { mode: "runs", runs: positioned };
}

export type TemplateV2ListMarker = "disc" | "number" | "none";

export interface TemplateV2ListItem {
  index: number;
  markerLabel: string;
  text: string;
  style: TemplateV2TextRunStyle;
}

export interface TemplateV2ListLayout {
  marker: TemplateV2ListMarker;
  lineHeightPx: number;
  items: TemplateV2ListItem[];
}

// Read-only preview layout for a `text-list` element. Each item's runs are
// concatenated into one line (per-run styling inside a list item is deferred to
// the inline text editor) and styled from the first run merged over the element
// font, matching the export renderer's marker semantics.
export function layoutTemplateV2List(element: JsonRecord): TemplateV2ListLayout {
  const marker: TemplateV2ListMarker =
    element.marker === "number"
      ? "number"
      : element.marker === "none"
        ? "none"
        : "disc";
  const rawItems = Array.isArray(element.items) ? element.items : [];
  const items = rawItems.map((runs, index): TemplateV2ListItem => {
    const runList = Array.isArray(runs) ? runs.filter(isJsonRecord) : [];
    const text = runList.map((run) => stringValue(run.text, "")).join("");
    const markerLabel =
      marker === "number" ? `${index + 1}.` : marker === "disc" ? "•" : "";
    return { index, markerLabel, text, style: textFont(element, runList[0]) };
  });
  const baseStyle = textFont(element);
  return {
    marker,
    lineHeightPx: baseStyle.fontSize * baseStyle.lineHeight,
    items,
  };
}

export interface TemplateV2TableCell {
  key: string;
  x: number;
  y: number;
  width: number;
  height: number;
  background: string | null;
  text: string;
  align: "left" | "center" | "right";
  header: boolean;
  style: TemplateV2TextRunStyle;
}

export interface TemplateV2TableLayout {
  cells: TemplateV2TableCell[];
}

function tableCellText(cell: JsonRecord): string {
  return Array.isArray(cell.runs)
    ? cell.runs
        .filter(isJsonRecord)
        .map((run) => stringValue(run.text, ""))
        .join("")
    : "";
}

function tableCellAlign(cell: JsonRecord): "left" | "center" | "right" {
  return cell.alignment === "center" || cell.alignment === "right"
    ? cell.alignment
    : "left";
}

// Read-only preview layout for a `table` element. Columns share the element
// width evenly (matching the export renderer's fixed table layout) and the
// header plus body rows share the element height evenly.
export function layoutTemplateV2Table(
  element: JsonRecord,
  width: number,
  height: number
): TemplateV2TableLayout {
  const columns = Array.isArray(element.columns)
    ? element.columns.filter(isJsonRecord)
    : [];
  const rows = Array.isArray(element.rows) ? element.rows : [];
  const columnCount = Math.max(
    columns.length,
    ...rows.map((row) => (Array.isArray(row) ? row.length : 0)),
    1
  );
  const rowCount = (columns.length > 0 ? 1 : 0) + rows.length;
  const columnWidth = width / columnCount;
  const rowHeight = rowCount > 0 ? height / rowCount : height;
  const cells: TemplateV2TableCell[] = [];
  const pushCell = (
    cell: JsonRecord,
    column: number,
    row: number,
    header: boolean,
    key: string
  ) => {
    const color = isJsonRecord(cell.color)
      ? stringValue(cell.color.color, "")
      : stringValue(cell.color, "");
    cells.push({
      key,
      x: column * columnWidth,
      y: row * rowHeight,
      width: columnWidth,
      height: rowHeight,
      background: color || null,
      text: tableCellText(cell),
      align: tableCellAlign(cell),
      header,
      style: textFont(element, cell),
    });
  };
  columns.forEach((cell, column) =>
    pushCell(cell, column, 0, true, `head-${column}`)
  );
  const bodyOffset = columns.length > 0 ? 1 : 0;
  rows.forEach((row, rowIndex) => {
    if (!Array.isArray(row)) return;
    row.filter(isJsonRecord).forEach((cell, column) =>
      pushCell(cell, column, rowIndex + bodyOffset, false, `${rowIndex}-${column}`)
    );
  });
  return { cells };
}

export interface TemplateV2InfographicView {
  type: "progress_bar" | "gauge";
  ratio: number;
  label: string;
  colors: [string, string];
}

// Read-only preview data for an `infographic` element. Ratio is derived from the
// raw data the same way the export render plan derives it; colors fall back to
// the renderer's defaults when the element does not pin them.
export function templateV2InfographicView(
  element: JsonRecord
): TemplateV2InfographicView | null {
  const data = isJsonRecord(element.data) ? element.data : null;
  const type = data ? String(data.type) : "";
  if (type !== "progress_bar" && type !== "gauge") return null;
  const value = numberValue(data?.value, 0);
  const min = numberValue(data?.min_value, 0);
  const max = numberValue(data?.max_value, 100);
  const ratio = max === min ? 0 : Math.min(1, Math.max(0, (value - min) / (max - min)));
  const palette = isJsonRecord(data?.colors) ? data?.colors : {};
  const colors: [string, string] = [
    stringValue(palette.fill, "#2563eb"),
    stringValue(palette.track, "#e5e7eb"),
  ];
  return { type, ratio, label: `${Math.round(ratio * 100)}%`, colors };
}
