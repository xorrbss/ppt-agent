export type AdaptedTableCellReference = {
  row: number;
  column: number;
};

export type AdaptedTableCellSpanMetadata = {
  rowspan?: number;
  colspan?: number;
  covered_by?: AdaptedTableCellReference;
};

type UnknownRecord = Record<string, unknown>;

function asRecord(value: unknown): UnknownRecord | null {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value)
    ? (value as UnknownRecord)
    : null;
}

function readInteger(value: unknown, minimum: number): number | null {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) {
    return null;
  }
  return value;
}

function hasOwn(record: UnknownRecord, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

/**
 * Preserve the native Template V2 span contract while adapting cells for the
 * legacy slide editor. The backend owns rectangle/overlap validation; this
 * boundary still rejects malformed individual values instead of coercing them.
 */
export function adaptTemplateV2TableCellSpanMetadata(
  value: unknown,
): AdaptedTableCellSpanMetadata {
  const record = asRecord(value);
  if (!record) return {};

  const rowspan = readInteger(record.rowspan, 1);
  const colspan = readInteger(record.colspan, 1);
  if (hasOwn(record, "rowspan") && rowspan == null) {
    throw new Error("template_v2_table_rowspan_invalid");
  }
  if (hasOwn(record, "colspan") && colspan == null) {
    throw new Error("template_v2_table_colspan_invalid");
  }

  const coveredByRecord = asRecord(record.covered_by);
  if (
    hasOwn(record, "covered_by") &&
    record.covered_by != null &&
    !coveredByRecord
  ) {
    throw new Error("template_v2_table_covered_by_invalid");
  }
  const coveredRow = coveredByRecord
    ? readInteger(coveredByRecord.row, 0)
    : null;
  const coveredColumn = coveredByRecord
    ? readInteger(coveredByRecord.column, 0)
    : null;
  if (
    coveredByRecord &&
    (coveredRow == null ||
      coveredColumn == null ||
      !hasOwn(coveredByRecord, "row") ||
      !hasOwn(coveredByRecord, "column"))
  ) {
    throw new Error("template_v2_table_covered_by_invalid");
  }
  const covered_by =
    coveredRow != null && coveredColumn != null
      ? { row: coveredRow, column: coveredColumn }
      : undefined;
  if (covered_by && ((rowspan ?? 1) !== 1 || (colspan ?? 1) !== 1)) {
    throw new Error("template_v2_table_covered_cell_span_invalid");
  }

  return {
    ...(rowspan != null ? { rowspan } : {}),
    ...(colspan != null ? { colspan } : {}),
    ...(covered_by ? { covered_by } : {}),
  };
}
