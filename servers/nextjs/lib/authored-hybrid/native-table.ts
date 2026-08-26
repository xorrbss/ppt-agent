import type { AuthoredHybridBounds } from "./schema.ts";
import type { BrowserElementObservation } from "./observation.ts";
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";

export const AUTHORED_NATIVE_TABLE_SCHEMA_VERSION = "authored-native-table/v1" as const;
export const AUTHORED_NATIVE_TABLE_FONT_SIZE_PRECISION_PT = 0.001 as const;
type HorizontalAlignment = "left" | "center" | "right" | "justify";
type VerticalAlignment = "top" | "middle" | "bottom";
type BorderEdge = "top" | "right" | "bottom" | "left";

export interface AuthoredNativeTableBorderV1 { readonly color: string; readonly widthPt: number; }
export interface AuthoredNativeTableCellStyleV1 {
  readonly fill?: string;
  readonly borders?: Readonly<Partial<Record<BorderEdge, AuthoredNativeTableBorderV1>>>;
  readonly horizontalAlign?: HorizontalAlignment;
  readonly verticalAlign?: VerticalAlignment;
}
export interface AuthoredNativeTablePayloadV1 {
  readonly schemaVersion: typeof AUTHORED_NATIVE_TABLE_SCHEMA_VERSION;
  readonly rows: readonly (readonly {
    readonly text: string; readonly fill?: string; readonly textColor?: string;
    readonly bold?: boolean; readonly fontFamily?: string; readonly fontSizePt?: number;
  }[])[];
  readonly columnWidths?: readonly number[];
  readonly rowHeightsPt?: readonly number[];
  readonly mergedCells?: readonly { readonly row: number; readonly column: number; readonly rowSpan: number; readonly columnSpan: number; }[];
  readonly cellStyles?: readonly (readonly AuthoredNativeTableCellStyleV1[])[];
  readonly headerRows?: number; readonly fontFamily?: string; readonly fontSizePt?: number;
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
const TABLE_FEATURES = new Set(["table", "table-v1", "cell-text", "cell-fill", "cell-font", "cell-border", "cell-alignment", "merged-cells", "row-height"]);
const BORDER_EDGES: readonly BorderEdge[] = ["top", "right", "bottom", "left"];
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function onlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean { const keys = new Set(allowed); return Object.keys(value).every((key) => keys.has(key)); }
function normalizedColor(value: unknown): string | undefined | null { return value == null ? undefined : typeof value === "string" && COLOR.test(value) ? `#${value.slice(1).toUpperCase()}` : null; }
function fontFamilyValue(value: unknown): string | undefined | null { return value == null ? undefined : typeof value === "string" && value.trim().length > 0 && value.length <= 128 ? value.trim() : null; }
function canonicalFontSizePt(value: number): number { return Math.round(value / AUTHORED_NATIVE_TABLE_FONT_SIZE_PRECISION_PT) * AUTHORED_NATIVE_TABLE_FONT_SIZE_PRECISION_PT; }
function fontSizeValue(value: unknown): number | undefined | null { return value == null ? undefined : typeof value === "number" && Number.isFinite(value) && value >= 6 && value <= 72 ? canonicalFontSizePt(value) : null; }
function positiveVector(value: unknown, count: number): readonly number[] | undefined | null {
  if (value == null) return undefined;
  return Array.isArray(value) && value.length === count && value.every((item) => typeof item === "number" && Number.isFinite(item) && item > 0) ? Object.freeze(value as number[]) : null;
}
function parseCellStyle(value: unknown): AuthoredNativeTableCellStyleV1 | null {
  if (!record(value) || !onlyKeys(value, ["fill", "borders", "horizontalAlign", "verticalAlign"])) return null;
  const fill = normalizedColor(value.fill); const horizontal = value.horizontalAlign; const vertical = value.verticalAlign;
  if (fill === null || (horizontal !== undefined && !["left", "center", "right", "justify"].includes(String(horizontal))) || (vertical !== undefined && !["top", "middle", "bottom"].includes(String(vertical)))) return null;
  let borders: Partial<Record<BorderEdge, AuthoredNativeTableBorderV1>> | undefined;
  if (value.borders !== undefined) {
    if (!record(value.borders) || !onlyKeys(value.borders, BORDER_EDGES)) return null;
    borders = {};
    for (const edge of BORDER_EDGES) {
      const border = value.borders[edge]; if (border === undefined) continue;
      if (!record(border) || !onlyKeys(border, ["color", "widthPt"])) return null;
      const color = normalizedColor(border.color);
      if (typeof color !== "string" || typeof border.widthPt !== "number" || !Number.isFinite(border.widthPt) || border.widthPt <= 0 || border.widthPt > 20) return null;
      borders[edge] = Object.freeze({ color, widthPt: border.widthPt });
    }
  }
  return Object.freeze({ ...(fill ? { fill } : {}), ...(borders ? { borders: Object.freeze(borders) } : {}), ...(horizontal ? { horizontalAlign: horizontal as HorizontalAlignment } : {}), ...(vertical ? { verticalAlign: vertical as VerticalAlignment } : {}) });
}

export function parseAuthoredNativeTablePayload(value: unknown): AuthoredNativeTablePayloadV1 | null {
  if (!record(value) || !onlyKeys(value, ["schemaVersion", "rows", "columnWidths", "rowHeightsPt", "mergedCells", "cellStyles", "headerRows", "fontFamily", "fontSizePt"]) || (value.schemaVersion !== undefined && value.schemaVersion !== AUTHORED_NATIVE_TABLE_SCHEMA_VERSION) || !Array.isArray(value.rows) || value.rows.length < 1 || value.rows.length > 100) return null;
  const width = Array.isArray(value.rows[0]) ? value.rows[0].length : 0; if (width < 1 || width > 20) return null;
  const rows = value.rows.map((row) => {
    if (!Array.isArray(row) || row.length !== width) return null;
    return row.map((cell) => {
      if (!record(cell) || !onlyKeys(cell, ["text", "fill", "textColor", "bold", "fontFamily", "fontSizePt"]) || typeof cell.text !== "string" || [...cell.text].length > 10_000) return null;
      const fill = normalizedColor(cell.fill); const textColor = normalizedColor(cell.textColor); const fontFamily = fontFamilyValue(cell.fontFamily); const fontSizePt = fontSizeValue(cell.fontSizePt);
      if (fill === null || textColor === null || fontFamily === null || fontSizePt === null || (cell.bold != null && typeof cell.bold !== "boolean")) return null;
      return Object.freeze({ text: cell.text, ...(fill ? { fill } : {}), ...(textColor ? { textColor } : {}), ...(cell.bold != null ? { bold: cell.bold } : {}), ...(fontFamily ? { fontFamily } : {}), ...(fontSizePt !== undefined ? { fontSizePt } : {}) });
    });
  });
  if (rows.some((row) => row === null || row.some((cell) => cell === null))) return null;
  const columnWidths = positiveVector(value.columnWidths, width); const rowHeightsPt = positiveVector(value.rowHeightsPt, rows.length);
  const headerRows = value.headerRows == null ? 0 : Number.isSafeInteger(value.headerRows) && (value.headerRows as number) >= 0 && (value.headerRows as number) <= rows.length ? value.headerRows as number : null;
  const fontFamily = fontFamilyValue(value.fontFamily); const fontSizePt = fontSizeValue(value.fontSizePt);
  if (columnWidths === null || rowHeightsPt === null || headerRows === null || fontFamily === null || fontSizePt === null) return null;
  let cellStyles: readonly (readonly AuthoredNativeTableCellStyleV1[])[] | undefined;
  if (value.cellStyles !== undefined) {
    if (!Array.isArray(value.cellStyles) || value.cellStyles.length !== rows.length) return null;
    const parsed = value.cellStyles.map((row) => { if (!Array.isArray(row) || row.length !== width) return null; const styles = row.map(parseCellStyle); return styles.some((style) => style === null) ? null : Object.freeze(styles as AuthoredNativeTableCellStyleV1[]); });
    if (parsed.some((row) => row === null)) return null;
    cellStyles = Object.freeze(parsed as (readonly AuthoredNativeTableCellStyleV1[])[]);
  }
  const mergedCells: { row: number; column: number; rowSpan: number; columnSpan: number; }[] = []; const occupied = new Set<string>();
  if (value.mergedCells !== undefined) {
    if (!Array.isArray(value.mergedCells)) return null;
    for (const item of value.mergedCells) {
      if (!record(item) || !onlyKeys(item, ["row", "column", "rowSpan", "columnSpan"])) return null;
      const { row, column, rowSpan, columnSpan } = item;
      if (!Number.isSafeInteger(row) || !Number.isSafeInteger(column) || !Number.isSafeInteger(rowSpan) || !Number.isSafeInteger(columnSpan) || (row as number) < 0 || (column as number) < 0 || (rowSpan as number) < 1 || (columnSpan as number) < 1 || (row as number) + (rowSpan as number) > rows.length || (column as number) + (columnSpan as number) > width || ((rowSpan as number) === 1 && (columnSpan as number) === 1)) return null;
      for (let r = row as number; r < (row as number) + (rowSpan as number); r += 1) for (let c = column as number; c < (column as number) + (columnSpan as number); c += 1) { const key = `${r}:${c}`; if (occupied.has(key) || ((r !== row || c !== column) && rows[r]![c]!.text !== "")) return null; occupied.add(key); }
      mergedCells.push({ row: row as number, column: column as number, rowSpan: rowSpan as number, columnSpan: columnSpan as number });
    }
  }
  return Object.freeze({ schemaVersion: AUTHORED_NATIVE_TABLE_SCHEMA_VERSION, rows: Object.freeze(rows as AuthoredNativeTablePayloadV1["rows"]), ...(columnWidths ? { columnWidths } : {}), ...(rowHeightsPt ? { rowHeightsPt } : {}), ...(mergedCells.length ? { mergedCells: Object.freeze(mergedCells) } : {}), ...(cellStyles ? { cellStyles } : {}), ...(headerRows ? { headerRows } : {}), ...(fontFamily ? { fontFamily } : {}), ...(fontSizePt !== undefined ? { fontSizePt } : {}) });
}

function canonicalCellEvidence(table: AuthoredNativeTablePayloadV1) {
  return { rows: table.rows.map((row, rowIndex) => row.map((cell, columnIndex) => { const style = table.cellStyles?.[rowIndex]?.[columnIndex]; const fill = style?.fill ?? cell.fill; return { text: cell.text.replace(/\r\n?/g, "\n").trim(), ...(fill ? { fill: fill.toUpperCase() } : {}), textColor: (cell.textColor ?? "#202020").toUpperCase(), bold: Boolean(cell.bold) || rowIndex < (table.headerRows ?? 0), fontFamily: (cell.fontFamily ?? table.fontFamily ?? "Arial").normalize("NFC").trim().toLowerCase(), fontSizePt: canonicalFontSizePt(cell.fontSizePt ?? table.fontSizePt ?? 10) }; })) };
}
function canonicalBrowserEvidence(evidence: BrowserElementObservation["tableEvidence"]) {
  if (!evidence || !Array.isArray(evidence.rows)) return null;
  for (const row of evidence.rows) for (const cell of row) if (!record(cell) || typeof cell.text !== "string" || (cell.fill !== undefined && (typeof cell.fill !== "string" || !COLOR.test(cell.fill))) || typeof cell.textColor !== "string" || !COLOR.test(cell.textColor) || typeof cell.bold !== "boolean" || typeof cell.fontFamily !== "string" || !cell.fontFamily.trim() || typeof cell.fontSizePt !== "number" || fontSizeValue(cell.fontSizePt) === null) return null;
  return { rows: evidence.rows.map((row) => row.map((cell) => ({ ...cell, ...(cell.fill ? { fill: cell.fill.toUpperCase() } : {}), textColor: cell.textColor.toUpperCase(), fontFamily: cell.fontFamily.normalize("NFC").trim().toLowerCase(), fontSizePt: canonicalFontSizePt(cell.fontSizePt) }))) };
}
export function prepareDeclaredNativeTables(declared: readonly DeclaredSemanticElementForObservation[], observed: readonly BrowserElementObservation[]) {
  const byId = new Map(observed.map((element, zOrder) => [element.id, { element, zOrder }])); const tables: AuthoredHybridNativeTableSource[] = []; const rejections = new Map<string, "unsupported-native-table" | "unsupported-table-css" | "table-observation-mismatch">();
  for (const item of declared) {
    if (item.kind !== "table") continue; const found = byId.get(item.elementId); const actual = found?.element; const table = parseAuthoredNativeTablePayload(item.payload);
    if (!table || !actual || actual.candidateKind !== "complex" || (item.requiredFeatures ?? []).some((feature) => !TABLE_FEATURES.has(feature))) { rejections.set(item.elementId, "unsupported-native-table"); continue; }
    if (Math.abs(actual.rotationDeg) > .001 || actual.opacity < .999 || actual.fallbackReasons.length !== 1 || actual.fallbackReasons[0] !== "complex-table") { rejections.set(item.elementId, "unsupported-table-css"); continue; }
    if (JSON.stringify(canonicalBrowserEvidence(actual.tableEvidence)) !== JSON.stringify(canonicalCellEvidence(table))) { rejections.set(item.elementId, "table-observation-mismatch"); continue; }
    tables.push({ id: actual.id, domPath: actual.domPath, tagName: actual.tagName, sourceIndex: actual.sourceIndex, zOrder: found!.zOrder, cssZIndex: actual.cssZIndex, bounds: { px: actual.boundsPx, inches: { x: actual.boundsPx.x / 96, y: actual.boundsPx.y / 96, width: actual.boundsPx.width / 96, height: actual.boundsPx.height / 96 } }, rotationDeg: 0, opacity: 1, parentElementId: actual.parentElementId ?? null, classification: { mode: "native", kind: "table", confidence: "safe" }, table });
  }
  return { tables, rejections };
}

function esc(value: string) { return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;"); }
const emu = (px: number) => Math.max(1, Math.round(px * 9525)); const pointEmu = (pt: number) => Math.max(1, Math.round(pt * 12700));
function borderXml(borders: AuthoredNativeTableCellStyleV1["borders"]): string {
  const names: Record<BorderEdge, string> = { top: "lnT", right: "lnR", bottom: "lnB", left: "lnL" };
  // DrawingML CT_TableCellProperties is sequence-sensitive. PowerPoint/Open XML
  // require lnL, lnR, lnT, lnB; semantic input order must never leak into OOXML.
  const ooxmlOrder: readonly BorderEdge[] = ["left", "right", "top", "bottom"];
  return ooxmlOrder.map((edge) => {
    const border = borders?.[edge];
    return border
      ? `<a:${names[edge]} w="${pointEmu(border.widthPt)}"><a:solidFill><a:srgbClr val="${border.color.slice(1)}"/></a:solidFill><a:prstDash val="solid"/></a:${names[edge]}>`
      : `<a:${names[edge]}><a:noFill/></a:${names[edge]}>`;
  }).join("");
}

function mergedPerimeterBorders(
  table: AuthoredNativeTablePayloadV1,
  merge: NonNullable<AuthoredNativeTablePayloadV1["mergedCells"]>[number],
  row: number,
  column: number,
): AuthoredNativeTableCellStyleV1["borders"] {
  const anchorBorders = table.cellStyles?.[merge.row]?.[merge.column]?.borders;
  if (row === merge.row && column === merge.column) return anchorBorders;
  const lastRow = merge.row + merge.rowSpan - 1;
  const lastColumn = merge.column + merge.columnSpan - 1;
  return {
    ...(row === merge.row && anchorBorders?.top ? { top: anchorBorders.top } : {}),
    ...(column === lastColumn && anchorBorders?.right ? { right: anchorBorders.right } : {}),
    ...(row === lastRow && anchorBorders?.bottom ? { bottom: anchorBorders.bottom } : {}),
    ...(column === merge.column && anchorBorders?.left ? { left: anchorBorders.left } : {}),
  };
}

function mirroredMergedNeighborBorders(
  table: AuthoredNativeTablePayloadV1,
  row: number,
  column: number,
  borders: AuthoredNativeTableCellStyleV1["borders"],
): AuthoredNativeTableCellStyleV1["borders"] {
  const resolved = { ...borders };
  for (const merge of table.mergedCells ?? []) {
    const anchorBorders = table.cellStyles?.[merge.row]?.[merge.column]?.borders;
    const lastRow = merge.row + merge.rowSpan - 1;
    const lastColumn = merge.column + merge.columnSpan - 1;
    if (row === merge.row + merge.rowSpan && column >= merge.column && column <= lastColumn && anchorBorders?.bottom) resolved.top = anchorBorders.bottom;
    if (row + 1 === merge.row && column >= merge.column && column <= lastColumn && anchorBorders?.top) resolved.bottom = anchorBorders.top;
    if (column === merge.column + merge.columnSpan && row >= merge.row && row <= lastRow && anchorBorders?.right) resolved.left = anchorBorders.right;
    if (column + 1 === merge.column && row >= merge.row && row <= lastRow && anchorBorders?.left) resolved.right = anchorBorders.left;
  }
  return resolved;
}

export function serializeNativeTableFrame(source: AuthoredHybridNativeTableSource, nonVisualId: number, alternativeText?: string): string {
  const { table } = source;
  const columns = table.rows[0]!.length;
  const weights = table.columnWidths ?? Array(columns).fill(1);
  const total = weights.reduce((sum, value) => sum + value, 0);
  const grid = weights.map((weight) => `<a:gridCol w="${Math.round(emu(source.bounds.px.width) * weight / total)}"/>`).join("");
  const equalRowHeight = Math.round(emu(source.bounds.px.height) / table.rows.length);
  const merges = new Map((table.mergedCells ?? []).map((merge) => [`${merge.row}:${merge.column}`, merge]));
  const mergeByCell = new Map<string, NonNullable<AuthoredNativeTablePayloadV1["mergedCells"]>[number]>();
  const coverage = new Map<string, { hMerge: boolean; vMerge: boolean }>();
  for (const merge of table.mergedCells ?? []) {
    for (let row = merge.row; row < merge.row + merge.rowSpan; row += 1) {
      for (let column = merge.column; column < merge.column + merge.columnSpan; column += 1) {
        const key = `${row}:${column}`;
        mergeByCell.set(key, merge);
        if (row !== merge.row || column !== merge.column) {
          coverage.set(key, { hMerge: column > merge.column, vMerge: row > merge.row });
        }
      }
    }
  }
  const rows = table.rows.map((row, rowIndex) => {
    const height = table.rowHeightsPt ? pointEmu(table.rowHeightsPt[rowIndex]!) : equalRowHeight;
    const cells = row.map((cell, columnIndex) => {
      const key = `${rowIndex}:${columnIndex}`;
      const merge = merges.get(key);
      const containingMerge = mergeByCell.get(key);
      const covered = coverage.get(key);
      const style = containingMerge
        ? table.cellStyles?.[containingMerge.row]?.[containingMerge.column] ?? {}
        : table.cellStyles?.[rowIndex]?.[columnIndex] ?? {};
      const color = (cell.textColor ?? "#202020").slice(1).toUpperCase();
      const fillColor = style.fill ?? cell.fill;
      const fill = fillColor ? `<a:solidFill><a:srgbClr val="${fillColor.slice(1).toUpperCase()}"/></a:solidFill>` : "<a:noFill/>";
      const family = cell.fontFamily ?? table.fontFamily ?? "Arial";
      const size = canonicalFontSizePt(cell.fontSizePt ?? table.fontSizePt ?? 10);
      const alignment = { left: "l", center: "ctr", right: "r", justify: "just" }[style.horizontalAlign ?? "left"];
      const anchor = { top: "t", middle: "ctr", bottom: "b" }[style.verticalAlign ?? "middle"];
      const baseBorders = containingMerge
        ? mergedPerimeterBorders(table, containingMerge, rowIndex, columnIndex)
        : style.borders;
      const borders = mirroredMergedNeighborBorders(table, rowIndex, columnIndex, baseBorders);
      const mergeAttrs = [
        merge?.columnSpan && merge.columnSpan > 1 ? `gridSpan="${merge.columnSpan}"` : "",
        merge?.rowSpan && merge.rowSpan > 1 ? `rowSpan="${merge.rowSpan}"` : "",
        covered?.hMerge ? 'hMerge="1"' : "",
        covered?.vMerge ? 'vMerge="1"' : "",
      ].filter(Boolean).join(" ");
      return `<a:tc${mergeAttrs ? ` ${mergeAttrs}` : ""}><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:pPr algn="${alignment}"/><a:r><a:rPr lang="en-US" sz="${Math.round(size * 100)}"${cell.bold || rowIndex < (table.headerRows ?? 0) ? ' b="1"' : ""}><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:latin typeface="${esc(family)}"/><a:ea typeface="${esc(family)}"/><a:cs typeface="${esc(family)}"/></a:rPr><a:t>${esc(cell.text)}</a:t></a:r><a:endParaRPr/></a:p></a:txBody><a:tcPr anchor="${anchor}">${borderXml(borders)}${fill}</a:tcPr></a:tc>`;
    }).join("");
    return `<a:tr h="${height}">${cells}</a:tr>`;
  }).join("");
  return `<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="${nonVisualId}" name="Presenton hybrid table ${esc(source.id)}" descr="${esc(alternativeText ?? "presenton-authored-native-table")}"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr><p:xfrm><a:off x="${Math.round(source.bounds.px.x * 9525)}" y="${Math.round(source.bounds.px.y * 9525)}"/><a:ext cx="${emu(source.bounds.px.width)}" cy="${emu(source.bounds.px.height)}"/></p:xfrm><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr firstRow="${(table.headerRows ?? 0) > 0 ? 1 : 0}"/><a:tblGrid>${grid}</a:tblGrid>${rows}</a:tbl></a:graphicData></a:graphic></p:graphicFrame>`;
}
