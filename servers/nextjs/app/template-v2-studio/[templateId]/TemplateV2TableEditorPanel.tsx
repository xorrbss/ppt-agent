"use client";

import { useTranslations } from "next-intl";
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

const REASON_KEYS = {
  table_invalid_contract: "tableEditor.errors.invalidContract",
  table_operation_invalid_index: "tableEditor.errors.invalidIndex",
  table_operation_no_change: "tableEditor.errors.noChange",
  table_min_rows_exceeded: "tableEditor.errors.minRows",
  table_max_rows_exceeded: "tableEditor.errors.maxRows",
  table_min_columns_exceeded: "tableEditor.errors.minColumns",
  table_max_columns_exceeded: "tableEditor.errors.maxColumns",
  table_max_cells_exceeded: "tableEditor.errors.maxCells",
  table_import_empty: "tableEditor.errors.emptyImport",
  table_import_too_large: "tableEditor.errors.inputTooLarge",
  table_import_malformed_quotes: "tableEditor.errors.malformedQuotes",
  table_import_ragged_rows: "tableEditor.errors.raggedRows",
  table_import_cell_too_large: "tableEditor.errors.cellTooLarge",
  table_paste_out_of_bounds: "tableEditor.errors.pasteOutOfBounds",
  table_preview_stale: "tableEditor.errors.stalePreview",
  table_preview_tampered: "tableEditor.errors.tamperedPreview",
  table_chart_requires_data: "tableEditor.errors.chartRequiresData",
  table_chart_non_numeric_value: "tableEditor.errors.chartNonNumeric",
  table_chart_incompatible_type: "tableEditor.errors.chartIncompatibleType",
} as const satisfies Record<TemplateV2TableReasonCode, string>;

const OPERATION_KEYS = {
  "insert-row": "tableEditor.operations.insertRow",
  "delete-row": "tableEditor.operations.deleteRow",
  "move-row": "tableEditor.operations.moveRow",
  "insert-column": "tableEditor.operations.insertColumn",
  "delete-column": "tableEditor.operations.deleteColumn",
  "move-column": "tableEditor.operations.moveColumn",
  "promote-first-row-to-header": "tableEditor.operations.promoteHeader",
  "demote-header-to-first-row": "tableEditor.operations.demoteHeader",
  transpose: "tableEditor.operations.transpose",
  "import-tsv": "tableEditor.operations.importTsv",
  "import-csv": "tableEditor.operations.importCsv",
  "paste-delimited": "tableEditor.operations.pasteDelimited",
  "table-to-chart": "tableEditor.operations.tableToChart",
} as const;

const CHART_TYPE_KEYS = {
  bar: "tableEditor.chartTypes.bar",
  line: "tableEditor.chartTypes.line",
  area: "tableEditor.chartTypes.area",
  pie: "tableEditor.chartTypes.pie",
  donut: "tableEditor.chartTypes.donut",
} as const satisfies Record<(typeof CHART_TYPES)[number], string>;

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
  const t = useTranslations("studio");
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
      result.path
        ? t("tableEditor.errors.field", { path: result.path })
        : "",
      typeof result.limit === "number"
        ? t("tableEditor.errors.limit", { limit: result.limit })
        : "",
    ]
      .filter(Boolean)
      .join(" ");
    setPending(null);
    setStatus(null);
    const message = t(REASON_KEYS[result.reasonCode]);
    setError(
      details
        ? t("tableEditor.errors.withDetails", { message, details })
        : message,
    );
  }

  function operationLabel(operation: string) {
    if (Object.prototype.hasOwnProperty.call(OPERATION_KEYS, operation)) {
      return t(OPERATION_KEYS[operation as keyof typeof OPERATION_KEYS]);
    }
    return t("tableEditor.operations.unknown");
  }

  function chartTypeLabel(type: (typeof CHART_TYPES)[number]) {
    return t(CHART_TYPE_KEYS[type]);
  }

  function accept(preview: PendingPreview) {
    setPending(preview);
    setError(null);
    setStatus(t("tableEditor.previewReady"));
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
      t("tableEditor.changeApplied"),
    );
  }

  const shown = pending ? previewTable(pending) : element;
  const shownColumns = Array.isArray(shown.columns) ? shown.columns : [];
  const shownRows = Array.isArray(shown.rows) ? shown.rows : [];

  return (
    <section
      aria-label={t("tableEditor.ariaLabel")}
      className="mt-5 rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-cyan-100">
            {t("tableEditor.title")}
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            {t("tableEditor.description")}
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          {t("tableEditor.lossless")}
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
                  {cellText(column) ||
                    t("tableEditor.column", { column: columnIndex + 1 })}
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
          {t("tableEditor.rows")}
        </legend>
        <button
          type="button"
          onClick={() =>
            previewOperation({ type: "insert-row", index: rows.length })
          }
          className="w-full rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          {t("tableEditor.addRow")}
        </button>
        {rows.map((_, rowIndex) => (
          <div key={rowIndex} className="flex items-center gap-1 text-xs">
            <span className="mr-auto text-slate-400">
              {t("tableEditor.row", { row: rowIndex + 1 })}
            </span>
            <button
              type="button"
              aria-label={t("tableEditor.moveRowUpAria", {
                row: rowIndex + 1,
              })}
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
              {t("tableEditor.up")}
            </button>
            <button
              type="button"
              aria-label={t("tableEditor.moveRowDownAria", {
                row: rowIndex + 1,
              })}
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
              {t("tableEditor.down")}
            </button>
            <button
              type="button"
              aria-label={t("tableEditor.deleteRowAria", {
                row: rowIndex + 1,
              })}
              onClick={() =>
                previewOperation({ type: "delete-row", index: rowIndex })
              }
              className="rounded border border-red-800 px-2 py-1 text-red-200"
            >
              {t("tableEditor.delete")}
            </button>
          </div>
        ))}
      </fieldset>

      <fieldset className="mt-4 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          {t("tableEditor.columns")}
        </legend>
        <button
          type="button"
          onClick={() =>
            previewOperation({
              type: "insert-column",
              index: columns.length,
              header: t("tableEditor.column", { column: columns.length + 1 }),
            })
          }
          className="w-full rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          {t("tableEditor.addColumn")}
        </button>
        {columns.map((column, columnIndex) => (
          <div key={columnIndex} className="flex items-center gap-1 text-xs">
            <span className="mr-auto truncate text-slate-400">
              {cellText(column) ||
                t("tableEditor.column", { column: columnIndex + 1 })}
            </span>
            <button
              type="button"
              aria-label={t("tableEditor.moveColumnLeftAria", {
                column: columnIndex + 1,
              })}
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
              {t("tableEditor.left")}
            </button>
            <button
              type="button"
              aria-label={t("tableEditor.moveColumnRightAria", {
                column: columnIndex + 1,
              })}
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
              {t("tableEditor.right")}
            </button>
            <button
              type="button"
              aria-label={t("tableEditor.deleteColumnAria", {
                column: columnIndex + 1,
              })}
              onClick={() =>
                previewOperation({
                  type: "delete-column",
                  index: columnIndex,
                })
              }
              className="rounded border border-red-800 px-2 py-1 text-red-200"
            >
              {t("tableEditor.delete")}
            </button>
          </div>
        ))}
      </fieldset>

      <fieldset
        className="mt-4 grid grid-cols-1 gap-2"
        disabled={disabled || !!pending}
      >
        <legend className="text-xs font-medium text-slate-300">
          {t("tableEditor.headerAndOrientation")}
        </legend>
        <button
          type="button"
          onClick={() =>
            previewOperation({ type: "promote-first-row-to-header" })
          }
          className="rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          {t("tableEditor.promoteFirstRow")}
        </button>
        <button
          type="button"
          onClick={() =>
            previewOperation({ type: "demote-header-to-first-row" })
          }
          className="rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          {t("tableEditor.demoteHeader")}
        </button>
        <button
          type="button"
          onClick={() => previewOperation({ type: "transpose" })}
          className="rounded border border-slate-700 px-2 py-1.5 text-xs"
        >
          {t("tableEditor.transpose")}
        </button>
      </fieldset>

      <fieldset className="mt-4 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          {t("tableEditor.delimitedData")}
        </legend>
        <textarea
          aria-label={t("tableEditor.delimitedDataAria")}
          value={delimited}
          onChange={(event) => setDelimited(event.target.value)}
          placeholder={t("tableEditor.delimitedPlaceholder")}
          className="min-h-24 w-full rounded border border-slate-700 bg-slate-950 p-2 text-xs"
        />
        <label className="block text-xs text-slate-400">
          {t("tableEditor.delimiter")}
          <select
            aria-label={t("tableEditor.delimiterAria")}
            value={delimiter}
            onChange={(event) =>
              setDelimiter(event.target.value as "," | "\t" | "auto")
            }
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2"
          >
            <option value="auto">{t("tableEditor.autoDetect")}</option>
            <option value=",">{t("tableEditor.csvComma")}</option>
            <option value={"\t"}>{t("tableEditor.tsvTab")}</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={firstRowIsHeader}
            onChange={(event) => setFirstRowIsHeader(event.target.checked)}
          />
          {t("tableEditor.firstRowIsHeader")}
        </label>
        <button
          type="button"
          onClick={previewImport}
          className="w-full rounded border border-cyan-700 px-2 py-1.5 text-xs text-cyan-100"
        >
          {t("tableEditor.previewReplaceImport")}
        </button>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-slate-400">
            {t("tableEditor.startRow")}
            <input
              aria-label={t("tableEditor.pasteStartRowAria")}
              type="number"
              min={0}
              value={pasteRow}
              disabled={pasteIncludesHeader}
              onChange={(event) => setPasteRow(Number(event.target.value))}
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 p-2"
            />
          </label>
          <label className="text-xs text-slate-400">
            {t("tableEditor.startColumn")}
            <input
              aria-label={t("tableEditor.pasteStartColumnAria")}
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
          {t("tableEditor.pasteBeginsInHeader")}
        </label>
        <button
          type="button"
          onClick={previewPaste}
          className="w-full rounded border border-cyan-700 px-2 py-1.5 text-xs text-cyan-100"
        >
          {t("tableEditor.previewBoundedPaste")}
        </button>
      </fieldset>

      <fieldset className="mt-4 space-y-2" disabled={disabled || !!pending}>
        <legend className="text-xs font-medium text-slate-300">
          {t("tableEditor.convertToChart")}
        </legend>
        <select
          aria-label={t("tableEditor.chartTypeAria")}
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
              {chartTypeLabel(type)}
            </option>
          ))}
        </select>
        <input
          aria-label={t("tableEditor.chartTitleAria")}
          value={chartTitle}
          onChange={(event) => setChartTitle(event.target.value)}
          placeholder={t("tableEditor.chartTitlePlaceholder")}
          className="w-full rounded border border-slate-700 bg-slate-950 p-2 text-xs"
        />
        <button
          type="button"
          onClick={previewChart}
          className="w-full rounded border border-violet-700 px-2 py-1.5 text-xs text-violet-100"
        >
          {t("tableEditor.previewChartConversion")}
        </button>
      </fieldset>

      {split.ok && split.value ? (
        <aside className="mt-4 rounded border border-amber-700/60 bg-amber-950/20 p-2 text-xs text-amber-100">
          <p className="font-medium">{t("tableEditor.splitSuggested")}</p>
          <p className="mt-1">
            {t("tableEditor.splitSummary", {
              rows: split.value.totalRows,
              segments: split.value.segments.length,
            })}
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
          aria-label={t("tableEditor.previewAria")}
          className="mt-4 rounded-lg border border-cyan-600 bg-slate-950 p-3"
        >
          <p className="text-xs font-semibold text-cyan-100">
            {t("tableEditor.previewOperation", {
              operation: operationLabel(operationName(pending)),
            })}
          </p>
          {pending.kind === "table" ? (
            <dl className="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-300">
              <dt>{t("tableEditor.rows")}</dt>
              <dd>
                {pending.value.diff.beforeRows} →{" "}
                {pending.value.diff.afterRows}
              </dd>
              <dt>{t("tableEditor.columns")}</dt>
              <dd>
                {pending.value.diff.beforeColumns} →{" "}
                {pending.value.diff.afterColumns}
              </dd>
              <dt>{t("tableEditor.changedCells")}</dt>
              <dd>{pending.value.diff.changedCells}</dd>
            </dl>
          ) : (
            <dl className="mt-2 grid grid-cols-2 gap-1 text-xs text-slate-300">
              <dt>{t("tableEditor.chartType")}</dt>
              <dd>{chartTypeLabel(pending.value.diff.chartType)}</dd>
              <dt>{t("tableEditor.categories")}</dt>
              <dd>{pending.value.diff.categories}</dd>
              <dt>{t("tableEditor.series")}</dt>
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
              {t("tableEditor.applyPreview")}
            </button>
            <button
              type="button"
              onClick={() => {
                setPending(null);
                setError(null);
                setStatus(t("tableEditor.previewCanceled"));
              }}
              className="rounded border border-slate-600 px-3 py-2 text-xs"
            >
              {t("tableEditor.cancel")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
