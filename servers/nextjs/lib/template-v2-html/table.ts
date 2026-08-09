import {
  colorWithOpacity,
  cssNumber,
  escapeCssColor,
  escapeHtml,
  horizontalAlign,
  textAlign,
} from "./css";
import {
  joinedRunText,
  normalizedRunsForHtml,
  normalizeRunsForHtml,
} from "./runs";
import { fontStyle, frameStyle, transformStyle } from "./style";
import type { JsonRecord, RenderMode } from "./types";
import {
  clamp,
  readArray,
  readBoolean,
  readNumber,
  readRecord,
  readRecordOrNull,
  readString,
  readStringValue,
} from "./values";

export function renderTable(item: JsonRecord, mode: RenderMode): string {
  const rows = tableRows(item);
  if (!rows.length) {
    return `<div style="${frameStyle(
      item,
      mode
    )}${transformStyle(item)}overflow:hidden"></div>`;
  }

  const rowCount = Math.max(1, rows.length);
  const colCount = Math.max(1, ...rows.map((row) => row.length));
  const tableFont = tableBaseFont(item);
  const cells = rows
    .flatMap((row, rowIndex) =>
      Array.from({ length: colCount }, (_, colIndex) => {
        const cell = readRecord(row[colIndex]);
        if (readRecordOrNull(cell.covered_by)) return "";
        const isHeader = rowIndex === 0;
        const rowspan =
          Number.isInteger(cell.rowspan) && Number(cell.rowspan) > 0
            ? Number(cell.rowspan)
            : 1;
        const colspan =
          Number.isInteger(cell.colspan) && Number(cell.colspan) > 0
            ? Number(cell.colspan)
            : 1;
        return `<div style="${tableCellStyle(
          cell,
          isHeader,
          tableFont
        )}grid-column:${colIndex + 1} / span ${colspan};grid-row:${
          rowIndex + 1
        } / span ${rowspan};">${cellText(cell, tableFont, isHeader)}</div>`;
      })
    )
    .join("");
  return `<div style="${frameStyle(
    item,
    mode
  )}${transformStyle(item)}display:grid;grid-template-columns:repeat(${colCount},minmax(0,1fr));grid-template-rows:repeat(${rowCount},minmax(0,1fr));overflow:hidden">${cells}</div>`;
}

function tableRows(item: JsonRecord): unknown[][] {
  const columns = readArray(item.columns);
  const bodyRows = readArray(item.rows).map(readArray);
  return (columns.length ? [columns, ...bodyRows] : bodyRows).filter((row) =>
    Array.isArray(row)
  );
}

function tableBaseFont(item: JsonRecord): JsonRecord {
  return {
    family: "Arial",
    size: 18,
    color: "#111827",
    line_height: 1.15,
    wrap: "word",
    ...readRecord(item.font),
  };
}

function tableCellStyle(
  cellValue: unknown,
  header: boolean,
  tableFont: JsonRecord
): string {
  const cell = readRecord(cellValue);
  const cellFont = tableCellFont(cellValue, tableFont);
  const alignment =
    readString(cell.alignment) ??
    readString(readRecord(cell.alignment).horizontal) ??
    readString(readRecord(readRecord(cell.text).alignment).horizontal);
  const fill = readRecord(cell.color ?? cell.fill);
  const stroke = readRecord(cell.stroke);
  const fillColor = readString(fill.color);
  const background = fillColor
    ? colorWithOpacity(fillColor, readNumber(fill.opacity))
    : "transparent";
  const forceHeaderBold = header && !tableCellHasExplicitBold(cellValue);
  let style = `${fontStyle(cellFont, {
    includeTextDecoration: false,
  })}display:flex;align-items:center;justify-content:${horizontalAlign(
    alignment
  )};border:${cssNumber(
    readNumber(stroke.width) ?? 1
  )}px solid ${escapeCssColor(
    colorWithOpacity(readString(stroke.color) ?? "#D1D5DB", readNumber(stroke.opacity))
  )};min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:${textAlign(
    alignment
  )};vertical-align:middle;white-space:pre-wrap;word-break:break-word;`;
  if (forceHeaderBold && !readBoolean(cellFont.bold)) style += "font-weight:700;";
  style += `background:${escapeCssColor(background)};`;
  return style;
}

function tableCellFont(cellValue: unknown, tableFont: JsonRecord): JsonRecord {
  if (typeof cellValue === "string" || typeof cellValue === "number") {
    return tableFont;
  }

  const cell = readRecord(cellValue);
  const firstRun = readRecord(readArray(cell.runs)[0]);
  const text = readRecord(cell.text);
  return {
    ...tableFont,
    ...readRecord(cell.font),
    ...(Object.keys(firstRun).length ? readRecord(firstRun.font) : {}),
    ...(Object.keys(text).length ? readRecord(text.font) : {}),
  };
}

function tableCellHasExplicitBold(cellValue: unknown): boolean {
  if (typeof cellValue === "string" || typeof cellValue === "number") {
    return false;
  }

  const cell = readRecord(cellValue);
  const firstRun = readRecord(readArray(cell.runs)[0]);
  const text = readRecord(cell.text);
  return (
    Object.prototype.hasOwnProperty.call(readRecord(cell.font), "bold") ||
    Object.prototype.hasOwnProperty.call(readRecord(firstRun.font), "bold") ||
    Object.prototype.hasOwnProperty.call(readRecord(text.font), "bold")
  );
}

function cellText(
  cellValue: unknown,
  tableFont: JsonRecord,
  header: boolean
): string {
  if (typeof cellValue === "string" || typeof cellValue === "number") {
    return escapeHtml(readStringValue(cellValue));
  }

  const cell = readRecord(cellValue);
  const fill = readRecord(cell.color ?? cell.fill);
  const fillColor = readString(fill.color)
    ? colorWithOpacity(readString(fill.color) ?? "", readNumber(fill.opacity))
    : null;
  const forceHeaderBold = header && !tableCellHasExplicitBold(cellValue);
  const headerFontPatch = forceHeaderBold ? { bold: true } : {};
  const directRuns = readArray(cell.runs).map(readRecord);
  if (directRuns.length) {
    const runs = normalizeRunsForHtml(
      directRuns,
      Object.prototype.hasOwnProperty.call(cell, "text")
        ? cell.text
        : joinedRunText(directRuns),
      cell.font,
    );
    return runs
      .map((run) => {
        const runFont = readableTableFont(
          {
            ...tableFont,
            ...readRecord(cell.font),
            ...readRecord(run.font),
            ...headerFontPatch,
          },
          fillColor,
          header
        );
        return `<span style="${fontStyle(runFont)}">${escapeHtml(
          readStringValue(run.text)
        )}</span>`;
      })
      .join("");
  }

  const text = cell.text;
  if (typeof text === "string") {
    return `<span style="${fontStyle(
      readableTableFont(
        { ...tableFont, ...readRecord(cell.font), ...headerFontPatch },
        fillColor,
        header
      )
    )}">${escapeHtml(text)}</span>`;
  }
  const textRecord = readRecord(text);
  const textRuns = normalizedRunsForHtml(textRecord, textRecord.font);
  if (textRuns.length) {
    return textRuns
      .map((run) => {
        const runFont = readableTableFont(
          {
            ...tableFont,
            ...readRecord(cell.font),
            ...readRecord(textRecord.font),
            ...readRecord(run.font),
            ...headerFontPatch,
          },
          fillColor,
          header
        );
        return `<span style="${fontStyle(runFont)}">${escapeHtml(
          readStringValue(run.text)
        )}</span>`;
      })
      .join("");
  }
  return `<span style="${fontStyle(
    readableTableFont(
      { ...tableFont, ...readRecord(cell.font), ...headerFontPatch },
      fillColor,
      header
    )
  )}">${escapeHtml(readStringValue(textRecord.text))}</span>`;
}

function readableTableFont(
  font: JsonRecord,
  fillColor: string | null,
  header: boolean
): JsonRecord {
  if (header) return font;
  return {
    ...font,
    color: readableTableTextColor(readString(font.color), fillColor),
  };
}

function readableTableTextColor(
  color: string | null,
  fill: string | null
): string {
  const textColor = normalizeReadableColor(color) ?? "#111827";
  const textLuminance = colorLuminance(textColor);
  const fillLuminance = colorLuminance(fill);
  if (textLuminance == null || fillLuminance == null) return textColor;

  const lighter = Math.max(textLuminance, fillLuminance);
  const darker = Math.min(textLuminance, fillLuminance);
  const contrast = (lighter + 0.05) / (darker + 0.05);
  if (contrast >= 3) return textColor;
  return fillLuminance > 0.5 ? "#111827" : "#FFFFFF";
}

function colorLuminance(color: string | null): number | null {
  const rgb = parseRgbColor(color);
  if (!rgb) return null;
  const [r, g, b] = rgb.map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.03928
      ? normalized / 12.92
      : Math.pow((normalized + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function parseRgbColor(color: string | null): [number, number, number] | null {
  const value = normalizeReadableColor(color);
  if (!value) return null;

  const hex = value.startsWith("#") ? value.slice(1) : value;
  if (/^[0-9a-fA-F]{3}$/.test(hex)) {
    return [
      Number.parseInt(hex[0] + hex[0], 16),
      Number.parseInt(hex[1] + hex[1], 16),
      Number.parseInt(hex[2] + hex[2], 16),
    ];
  }
  if (/^[0-9a-fA-F]{6}$/.test(hex)) {
    return [
      Number.parseInt(hex.slice(0, 2), 16),
      Number.parseInt(hex.slice(2, 4), 16),
      Number.parseInt(hex.slice(4, 6), 16),
    ];
  }

  const rgb = value.match(
    /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i
  );
  if (!rgb) return null;
  return [
    clamp(Number(rgb[1]), 0, 255),
    clamp(Number(rgb[2]), 0, 255),
    clamp(Number(rgb[3]), 0, 255),
  ];
}

function normalizeReadableColor(color: string | null): string | null {
  if (!color) return null;
  const value = color.trim();
  if (!value) return null;
  return value.startsWith("#") || value.startsWith("rgb") ? value : `#${value}`;
}
