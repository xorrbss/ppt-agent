import type { AuthoredHybridBounds } from "./schema.ts";
import type { BrowserElementObservation } from "./observation.ts";
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";

export const AUTHORED_NATIVE_TABLE_SCHEMA_VERSION = "authored-native-table/v1" as const;
export interface AuthoredNativeTablePayloadV1 {
  readonly schemaVersion: typeof AUTHORED_NATIVE_TABLE_SCHEMA_VERSION;
  readonly rows: readonly (readonly { readonly text: string; readonly fill?: string; readonly textColor?: string; readonly bold?: boolean }[])[];
  readonly columnWidths?: readonly number[];
  readonly headerRows?: number;
  readonly fontFamily?: string;
  readonly fontSizePt?: number;
}
export interface AuthoredHybridNativeTableSource {
  readonly id: string; readonly domPath: string; readonly tagName: string;
  readonly sourceIndex: number; readonly zOrder: number; readonly cssZIndex: number | null;
  readonly bounds: AuthoredHybridBounds; readonly rotationDeg: 0; readonly opacity: 1;
  readonly parentElementId?: string | null;
  readonly classification: { readonly mode: "native"; readonly kind: "table"; readonly confidence: "safe" };
  readonly table: AuthoredNativeTablePayloadV1;
}

const COLOR = /^#[0-9a-f]{6}$/i;
const CSS_REASONS = new Set(["unsupported-transform", "unsupported-opacity", "unsupported-filter", "unsupported-blend-mode", "unsupported-clipping"]);
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
export function parseAuthoredNativeTablePayload(value: unknown): AuthoredNativeTablePayloadV1 | null {
  if (!record(value) || (value.schemaVersion !== undefined && value.schemaVersion !== AUTHORED_NATIVE_TABLE_SCHEMA_VERSION) || !Array.isArray(value.rows) || value.rows.length < 1 || value.rows.length > 100) return null;
  const width = Array.isArray(value.rows[0]) ? value.rows[0].length : 0;
  if (width < 1 || width > 20) return null;
  const rows = value.rows.map((row) => {
    if (!Array.isArray(row) || row.length !== width) return null;
    return row.map((cell) => {
      if (!record(cell) || typeof cell.text !== "string" || [...cell.text].length > 10_000) return null;
      if (cell.fill !== undefined && (typeof cell.fill !== "string" || !COLOR.test(cell.fill))) return null;
      if (cell.textColor !== undefined && (typeof cell.textColor !== "string" || !COLOR.test(cell.textColor))) return null;
      if (cell.bold !== undefined && typeof cell.bold !== "boolean") return null;
      return { text: cell.text, ...(cell.fill ? { fill: cell.fill } : {}), ...(cell.textColor ? { textColor: cell.textColor } : {}), ...(cell.bold !== undefined ? { bold: cell.bold } : {}) };
    });
  });
  if (rows.some((row) => row === null || row.some((cell) => cell === null))) return null;
  const columnWidths = value.columnWidths === undefined ? undefined : Array.isArray(value.columnWidths) && value.columnWidths.length === width && value.columnWidths.every((item) => typeof item === "number" && Number.isFinite(item) && item > 0) ? value.columnWidths as number[] : null;
  const headerRows = value.headerRows === undefined ? 0 : Number.isSafeInteger(value.headerRows) && (value.headerRows as number) >= 0 && (value.headerRows as number) <= value.rows.length ? value.headerRows as number : null;
  const fontFamily = value.fontFamily === undefined ? undefined : typeof value.fontFamily === "string" && value.fontFamily.length <= 128 ? value.fontFamily : null;
  const fontSizePt = value.fontSizePt === undefined ? undefined : typeof value.fontSizePt === "number" && value.fontSizePt >= 6 && value.fontSizePt <= 72 ? value.fontSizePt : null;
  if (columnWidths === null || headerRows === null || fontFamily === null || fontSizePt === null) return null;
  return Object.freeze({ schemaVersion: AUTHORED_NATIVE_TABLE_SCHEMA_VERSION, rows: rows as AuthoredNativeTablePayloadV1["rows"], ...(columnWidths ? { columnWidths } : {}), ...(headerRows ? { headerRows } : {}), ...(fontFamily ? { fontFamily } : {}), ...(fontSizePt ? { fontSizePt } : {}) });
}

export function prepareDeclaredNativeTables(declared: readonly DeclaredSemanticElementForObservation[], observed: readonly BrowserElementObservation[]) {
  const byId = new Map(observed.map((element, zOrder) => [element.id, { element, zOrder }]));
  const tables: AuthoredHybridNativeTableSource[] = [];
  const rejections = new Map<string, "unsupported-native-table" | "unsupported-table-css">();
  for (const item of declared) {
    if (item.kind !== "table") continue;
    const found = byId.get(item.elementId); const actual = found?.element; const table = parseAuthoredNativeTablePayload(item.payload);
    if (!table || !actual || actual.candidateKind !== "complex" || !actual.fallbackReasons.includes("complex-table") || (item.requiredFeatures ?? []).some((f) => !["table", "table-v1", "cell-text", "cell-fill"].includes(f))) { rejections.set(item.elementId, "unsupported-native-table"); continue; }
    if (Math.abs(actual.rotationDeg) > .001 || actual.opacity < .999 || actual.fallbackReasons.some((r) => CSS_REASONS.has(r))) { rejections.set(item.elementId, "unsupported-table-css"); continue; }
    tables.push({ id: actual.id, domPath: actual.domPath, tagName: actual.tagName, sourceIndex: actual.sourceIndex, zOrder: found!.zOrder, cssZIndex: actual.cssZIndex, bounds: { px: actual.boundsPx, inches: { x: actual.boundsPx.x / 96, y: actual.boundsPx.y / 96, width: actual.boundsPx.width / 96, height: actual.boundsPx.height / 96 } }, rotationDeg: 0, opacity: 1, parentElementId: actual.parentElementId ?? null, classification: { mode: "native", kind: "table", confidence: "safe" }, table });
  }
  return { tables, rejections };
}

function esc(value: string) { return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
const emu = (px: number) => Math.max(1, Math.round(px * 9525));
export function serializeNativeTableFrame(source: AuthoredHybridNativeTableSource, nonVisualId: number): string {
  const { table } = source; const cols = table.rows[0].length; const weights = table.columnWidths ?? Array(cols).fill(1); const total = weights.reduce((a, b) => a + b, 0);
  const grid = weights.map((w) => `<a:gridCol w="${Math.round(emu(source.bounds.px.width) * w / total)}"/>`).join("");
  const rowHeight = Math.round(emu(source.bounds.px.height) / table.rows.length);
  const rows = table.rows.map((row, ri) => `<a:tr h="${rowHeight}">${row.map((cell) => { const color = (cell.textColor ?? "#202020").slice(1).toUpperCase(); const fill = cell.fill ? `<a:solidFill><a:srgbClr val="${cell.fill.slice(1).toUpperCase()}"/></a:solidFill>` : "<a:noFill/>"; return `<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="${Math.round((table.fontSizePt ?? 10) * 100)}"${cell.bold || ri < (table.headerRows ?? 0) ? ' b="1"' : ""}><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:latin typeface="${esc(table.fontFamily ?? "Arial")}"/><a:ea typeface="${esc(table.fontFamily ?? "Arial")}"/></a:rPr><a:t>${esc(cell.text)}</a:t></a:r><a:endParaRPr/></a:p></a:txBody><a:tcPr>${fill}</a:tcPr></a:tc>`; }).join("")}</a:tr>`).join("");
  return `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${nonVisualId}" name="Presenton hybrid table ${esc(source.id)}" descr="presenton-authored-native-table"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="${Math.round(source.bounds.px.x * 9525)}" y="${Math.round(source.bounds.px.y * 9525)}"/><a:ext cx="${emu(source.bounds.px.width)}" cy="${emu(source.bounds.px.height)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="${(table.headerRows ?? 0) > 0 ? 1 : 0}"/><a:tblGrid>${grid}</a:tblGrid>${rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>`;
}
