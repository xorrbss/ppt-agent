"use client";

import { useMemo, useState } from "react";

import {
  applyTemplateV2TablePreview,
  applyTemplateV2TableToChartPreview,
  previewTemplateV2DelimitedImport,
  previewTemplateV2DelimitedPaste,
  previewTemplateV2TableOperation,
  previewTemplateV2TableToChart,
  suggestTemplateV2LongTableSplit,
  type TemplateV2TableChartPreview,
  type TemplateV2TableOperation,
  type TemplateV2TablePreview,
  type TemplateV2TableReasonCode,
  type TemplateV2TableRecord,
} from "@/lib/template-v2-table-operations";

type PendingPreview =
  | { kind: "table"; value: TemplateV2TablePreview }
  | { kind: "chart"; value: TemplateV2TableChartPreview };

export interface TemplateV2TableEditorMutation {
  expectedRevision: number;
  idempotencyKey: string;
  historyKey: string;
  operation: string;
  beforeDigest: string;
  afterDigest: string;
}

interface TemplateV2TableEditorPanelProps {
  element: TemplateV2TableRecord;
  revision: number;
  disabled: boolean;
  onApply(
    replacement: TemplateV2TableRecord,
    mutation: TemplateV2TableEditorMutation,
  ): void;
}

const CHART_TYPES = ["bar", "line", "area", "pie", "donut"] as const;

const REASON_MESSAGES: Record<TemplateV2TableReasonCode, string> = {
  table_invalid_contract: "The selected element is not a valid bounded table.",
  table_operation_invalid_index: "That row or column is no longer available.",
  table_operation_no_change: "The requested operation would not change the table.",
  table_min_rows_exceeded: "The table must retain its minimum number of rows.",
  table_max_rows_exceeded: "The table has reached its maximum number of rows.",
  table_min_columns_exceeded:
    "The table must retain its minimum number of columns.",
  table_max_columns_exceeded:
    "The table has reached its maximum number of columns.",
  table_max_cells_exceeded: "The table would exceed the bounded cell limit.",
  table_import_empty: "Paste CSV or TSV data before creating a preview.",
  table_import_too_large: "The delimited input exceeds the bounded byte limit.",
  table_import_malformed_quotes:
    "The delimited input contains malformed quoted cells.",
  table_import_ragged_rows:
    "Every imported row must have the same number of cells.",
  table_import_cell_too_large: "An imported cell exceeds the character limit.",
  table_paste_out_of_bounds:
    "The pasted range does not fit inside the current table.",
  table_preview_stale:
    "The table changed after this preview. Create a fresh preview.",
  table_preview_tampered:
    "The preview digest is invalid. Create a fresh preview.",
  table_chart_requires_data:
    "Table-to-chart needs a category column and numeric data.",
  table_chart_non_numeric_value:
    "Every chart value cell must contain a finite number.",
  table_chart_incompatible_type:
    "Pie and donut charts require exactly one numeric series.",
};

function isRecord(value: unknown): value is TemplateV2TableRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cellText(value: unknown): string {
  if (!isRecord(value) || !Array.isArray(value.runs)) return "";
  return value.runs
    .map((run) =>
      isRecord(run) && typeof run.text === "string" ? run.text : "",
    )
    .join("");
}

function previewTable(preview: PendingPreview): TemplateV2TableRecord {
  return preview.kind === "table" ? preview.value.after : preview.value.chart;
}

function operationName(preview: PendingPreview): string {
  return preview.kind === "table"
    ? preview.value.diff.operation
    : preview.value.diff.operation;
}

function digestToken(digest: string): string {
  return digest.replace(/[^a-zA-Z0-9_-]/g, "-");
}

export default function TemplateV2TableEditorPanel({
  element,
  revision,
  disabled,
  onApply,
}: TemplateV2TableEditorPanelProps) {
  const [pending, setPending] = useState<PendingPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [delimited, setDelimited] = useState("");
  const [delimiter, setDelimiter] = useState<"," | "\t" | "auto">("auto");
  const [firstRowIsHeader, setFirstRowIsHeader] = useState(true);
  const [pasteRow, setPasteRow] = useState(0);
  const [pasteColumn, setPasteColumn] = useState(0);
  const [pasteIncludesHeader, setPasteIncludesHeader] = useState(false);
  const [chartType, setChartType] =
    useState<(typeof CHART_TYPES)[number]>("bar");
  const [chartTitle, setChartTitle] = useState("");

  const columns = Array.isArray(element.columns) ? element.columns : [];
  const rows = Array.isArray(element.rows) ? element.rows : [];
  const split = useMemo(
    () => suggestTemplateV2LongTableSplit(element, 12),
    [element],
  );

  if (element.type !== "table") return null;

  function fail(
    result: {
      ok: false;
      reasonCode: TemplateV2TableReasonCode;
      path?: string;
      limit?: number;
    },
  ) {
    const details = [
      result.path ? `Field: ${result.path}.` : "",
      typeof result.limit === "number" ? `Limit: ${result.limit}.` : "",
    ]
      .filter(Boolean)
      .join(" ");
    setPending(null);
    setStatus(null);
    setError(
      `${REASON_MESSAGES[result.reasonCode]}${details ? ` ${details}` : ""}`,
    );
  }

  function accept(preview: PendingPreview) {
    setPending(preview);
    setError(null);
    setStatus("Preview ready. No template data has changed.");
  }

  function previewOperation(operation: TemplateV2TableOperation) {
    const result = previewTemplateV2TableOperation(element, operation);
    if (!result.ok) return fail(result);
    accept({ kind: "table", value: result.value });
  }

  function previewImport() {
    const result = previewTemplateV2DelimitedImport(element, delimited, {
      delimiter,
      firstRowIsHeader,
    });
    if (!result.ok) return fail(result);
    accept({ kind: "table", value: result.value });
  }

  function previewPaste() {
    const result = previewTemplateV2DelimitedPaste(element, delimited, {
      delimiter,
      startRow: pasteIncludesHeader ? -1 : pasteRow,
      startColumn: pasteColumn,
      includeHeader: pasteIncludesHeader,
    });
    if (!result.ok) return fail(result);
    accept({ kind: "table", value: result.value });
  }

  function previewChart() {
    const result = previewTemplateV2TableToChart(element, {
      chartType,
      title: chartTitle,
    });
    if (!result.ok) return fail(result);
    accept({ kind: "chart", value: result.value });
  }

  function applyPending() {
    if (!pending) return;
    const result =
      pending.kind === "table"
        ? applyTemplateV2TablePreview(element, pending.value)
        : applyTemplateV2TableToChartPreview(element, pending.value);
    if (!result.ok) return fail(result);
    const operation = operationName(pending);
    const beforeDigest = pending.value.beforeDigest;
    const afterDigest = pending.value.afterDigest;
    const idempotencyKey = [
      "table-editor",
      revision,
      operation,
      digestToken(beforeDigest),
      digestToken(afterDigest),
    ].join("-");
    onApply(result.value, {
      expectedRevision: revision,
      idempotencyKey,
      historyKey: idempotencyKey,
      operation,
      beforeDigest,
      afterDigest,
    });
    setPending(null);
    setError(null);
    setStatus(
      "Change applied locally. Autosave, revision CAS, undo, and redo remain authoritative.",
    );
  }

  const shown = pending ? previewTable(pending) : element;
  const shownColumns = Array.isArray(shown.columns) ? shown.columns : [];
  const shownRows = Array.isArray(shown.rows) ? shown.rows : [];

  return (
    <section
      aria-label="Table structure editor"
      className="mt-5 rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-cyan-100">
            Table structure
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            Bounded patches only · preview before apply
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          lossless
        </span>
      </div>

      <div className="mt-3 max-h-64 overflow-auto rounded border border-slate-800">
        <table className="w-full border-collapse text-xs">
          <thead className="bg-slate-950 text-slate-300">
            <tr>
              <th className="border-b border-r border-slate-800 p-2">#</th>
              {shownColumns.map((column, columnIndex) => (
                <th
                  key={columnIndex}
                  className="min-w-24 border-b border-r border-slate-800 p-2 text-left"
                >
                  {cellText(column) || `Column ${columnIndex + 1}`}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shownRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <th className="border-b border-r border-slate-800 p-2 text-slate-500">
                  {rowIndex + 1}
                </th>
                {Array.isArray(row)
                  ? row.map((cell, columnIndex) => (
                      <td
                        key={columnIndex}
                        className="border-b border-r border-slate-800 p-2 text-slate-300"
                      >
                        {cellText(cell)}
                      </td>
                    ))
                  : null}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <fieldset className="mt-3 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          Rows
        </legend>
        <button
          type="button"
          onClick={() =>
            previewOperation({ type: "insert-row", index: rows.length })
          }
          className="w-full rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          Add row
        </button>
        {rows.map((_, rowIndex) => (
          <div key={rowIndex} className="flex items-center gap-1 text-xs">
            <span className="mr-auto text-slate-400">Row {rowIndex + 1}</span>
            <button
              type="button"
              aria-label={`Move row ${rowIndex + 1} up`}
              disabled={rowIndex === 0}
              onClick={() =>
                previewOperation({
                  type: "move-row",
                  from: rowIndex,
                  to: rowIndex - 1,
                })
              }
              className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
            >
              Up
            </button>
            <button
              type="button"
              aria-label={`Move row ${rowIndex + 1} down`}
              disabled={rowIndex === rows.length - 1}
              onClick={() =>
                previewOperation({
                  type: "move-row",
                  from: rowIndex,
                  to: rowIndex + 1,
                })
              }
              className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
            >
              Down
            </button>
            <button
              type="button"
              aria-label={`Delete row ${rowIndex + 1}`}
              onClick={() =>
                previewOperation({ type: "delete-row", index: rowIndex })
              }
              className="rounded border border-red-800 px-2 py-1 text-red-200"
            >
              Delete
            </button>
          </div>
        ))}
      </fieldset>

      <fieldset className="mt-4 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          Columns
        </legend>
        <button
          type="button"
          onClick={() =>
            previewOperation({
              type: "insert-column",
              index: columns.length,
              header: `Column ${columns.length + 1}`,
            })
          }
          className="w-full rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          Add column
        </button>
        {columns.map((column, columnIndex) => (
          <div key={columnIndex} className="flex items-center gap-1 text-xs">
            <span className="mr-auto truncate text-slate-400">
              {cellText(column) || `Column ${columnIndex + 1}`}
            </span>
            <button
              type="button"
              aria-label={`Move column ${columnIndex + 1} left`}
              disabled={columnIndex === 0}
              onClick={() =>
                previewOperation({
                  type: "move-column",
                  from: columnIndex,
                  to: columnIndex - 1,
                })
              }
              className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
            >
              Left
            </button>
            <button
              type="button"
              aria-label={`Move column ${columnIndex + 1} right`}
              disabled={columnIndex === columns.length - 1}
              onClick={() =>
                previewOperation({
                  type: "move-column",
                  from: columnIndex,
                  to: columnIndex + 1,
                })
              }
              className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
            >
              Right
            </button>
            <button
              type="button"
              aria-label={`Delete column ${columnIndex + 1}`}
              onClick={() =>
                previewOperation({
                  type: "delete-column",
                  index: columnIndex,
                })
              }
              className="rounded border border-red-800 px-2 py-1 text-red-200"
            >
              Delete
            </button>
          </div>
        ))}
      </fieldset>

      <fieldset
        className="mt-4 grid grid-cols-1 gap-2"
        disabled={disabled || !!pending}
      >
        <legend className="text-xs font-medium text-slate-300">
          Header and orientation
        </legend>
        <button
          type="button"
          onClick={() =>
            previewOperation({ type: "promote-first-row-to-header" })
          }
          className="rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          Promote first row to header
        </button>
        <button
          type="button"
          onClick={() =>
            previewOperation({ type: "demote-header-to-first-row" })
          }
          className="rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          Demote header to first row
        </button>
        <button
          type="button"
          onClick={() => previewOperation({ type: "transpose" })}
          className="rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          Transpose
        </button>
      </fieldset>

      <fieldset className="mt-4 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          CSV / TSV
        </legend>
        <textarea
          aria-label="Delimited table data"
          value={delimited}
          onChange={(event) => setDelimited(event.target.value)}
          placeholder={"Quarter,Revenue\nQ1,42"}
          className="min-h-24 w-full rounded border border-slate-700 bg-slate-950 p-2 text-xs"
        />
        <label className="block text-xs text-slate-400">
          Delimiter
          <select
            aria-label="Delimited data delimiter"
            value={delimiter}
            onChange={(event) =>
              setDelimiter(event.target.value as "," | "\t" | "auto")
            }
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2"
          >
            <option value="auto">Auto-detect</option>
            <option value=",">CSV comma</option>
            <option value={"\t"}>TSV tab</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={firstRowIsHeader}
            onChange={(event) => setFirstRowIsHeader(event.target.checked)}
          />
          First imported row is the header
        </label>
        <button
          type="button"
          onClick={previewImport}
          className="w-full rounded border border-cyan-700 px-2 py-1.5 text-xs text-cyan-100"
        >
          Preview replace import
        </button>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-slate-400">
            Start row
            <input
              aria-label="Paste start row"
              type="number"
              min={0}
              value={pasteRow}
              disabled={pasteIncludesHeader}
              onChange={(event) => setPasteRow(Number(event.target.value))}
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2"
            />
          </label>
          <label className="text-xs text-slate-400">
            Start column
            <input
              aria-label="Paste start column"
              type="number"
              min={0}
              value={pasteColumn}
              onChange={(event) => setPasteColumn(Number(event.target.value))}
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2"
            />
          </label>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={pasteIncludesHeader}
            onChange={(event) => setPasteIncludesHeader(event.target.checked)}
          />
          Paste begins in header row
        </label>
        <button
          type="button"
          onClick={previewPaste}
          className="w-full rounded border border-cyan-700 px-2 py-1.5 text-xs text-cyan-100"
        >
          Preview bounded paste
        </button>
      </fieldset>

      <fieldset className="mt-4 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          Convert table to chart
        </legend>
        <select
          aria-label="Table chart type"
          value={chartType}
          onChange={(event) =>
            setChartType(
              event.target.value as (typeof CHART_TYPES)[number],
            )
          }
          className="w-full rounded border border-slate-700 bg-slate-950 p-2 text-xs"
        >
          {CHART_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
        <input
          aria-label="Table chart title"
          value={chartTitle}
          onChange={(event) => setChartTitle(event.target.value)}
          placeholder="Optional chart title"
          className="w-full rounded border border-slate-700 bg-slate-950 p-2 text-xs"
        />
        <button
          type="button"
          onClick={previewChart}
          className="w-full rounded border border-violet-700 px-2 py-1.5 text-xs text-violet-100"
        >
          Preview chart conversion
        </button>
      </fieldset>

      {split.ok && split.value ? (
        <aside className="mt-4 rounded border border-amber-700/60 bg-amber-950/20 p-2 text-xs text-amber-100">
          <p className="font-medium">Long table split suggested</p>
          <p className="mt-1">
            {split.value.totalRows} rows · {split.value.segments.length} slide
            segments · repeat the header on every segment. This suggestion does
            not create or clone slides.
          </p>
        </aside>
      ) : null}

      {error ? (
        <p role="alert" className="mt-3 text-xs text-red-300">
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" className="mt-3 text-xs text-emerald-300">
          {status}
        </p>
      ) : null}

      {pending ? (
        <div
          aria-label="Table change preview"
          className="mt-4 rounded-lg border border-cyan-600 bg-slate-950 p-3"
        >
          <p className="text-xs font-semibold text-cyan-100">
            Preview · {operationName(pending)}
          </p>
          {pending.kind === "table" ? (
            <dl className="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-300">
              <dt>Rows</dt>
              <dd>
                {pending.value.diff.beforeRows} →{" "}
                {pending.value.diff.afterRows}
              </dd>
              <dt>Columns</dt>
              <dd>
                {pending.value.diff.beforeColumns} →{" "}
                {pending.value.diff.afterColumns}
              </dd>
              <dt>Changed cells</dt>
              <dd>{pending.value.diff.changedCells}</dd>
            </dl>
          ) : (
            <dl className="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-300">
              <dt>Chart type</dt>
              <dd>{pending.value.diff.chartType}</dd>
              <dt>Categories</dt>
              <dd>{pending.value.diff.categories}</dd>
              <dt>Series</dt>
              <dd>{pending.value.diff.series}</dd>
            </dl>
          )}
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              disabled={disabled}
              onClick={applyPending}
              className="flex-1 rounded bg-emerald-600 px-3 py-2 text-xs font-semibold disabled:opacity-40"
            >
              Apply preview
            </button>
            <button
              type="button"
              onClick={() => {
                setPending(null);
                setError(null);
                setStatus("Preview canceled. No template data changed.");
              }}
              className="rounded border border-slate-600 px-3 py-2 text-xs"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
