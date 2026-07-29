"use client";

import { useState } from "react";
import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  buildTableContractEditPatches,
  type TableContractEdit,
  validateTableContract,
} from "@/lib/template-v2-chart-table-contract";
import {
  ToolbarButton,
  ToolbarField,
  ToolbarSelect,
  toolbarInputClass,
} from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

const record = (value: unknown): TemplateV2ToolbarRecord | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as TemplateV2ToolbarRecord)
    : null;

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

const integerValue = (value: unknown, fallback: number) =>
  Number.isInteger(value) ? (value as number) : fallback;

function cellColor(cell: TemplateV2ToolbarRecord): string {
  const color = record(cell.color);
  return color
    ? typeof color.color === "string"
      ? color.color
      : ""
    : typeof cell.color === "string"
      ? cell.color
      : "";
}

function cellColorPatch(
  root: "columns" | "rows",
  row: number | null,
  column: number,
  cell: TemplateV2ToolbarRecord,
  value: string
): TemplateV2ToolbarPatch {
  const path: Array<string | number> =
    root === "columns" ? ["columns", column] : ["rows", row as number, column];
  return {
    path: record(cell.color) ? [...path, "color", "color"] : [...path, "color"],
    value,
  };
}

export default function TableToolbar({
  element,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  const [targetIndex, setTargetIndex] = useState(0);
  const validated = validateTableContract(element);
  const contractElement = validated.ok ? validated.value : null;
  const columns = (contractElement?.columns ?? []) as TemplateV2ToolbarRecord[];
  const rows = (contractElement?.rows ?? []) as TemplateV2ToolbarRecord[][];
  const headerColor = columns[0] ? cellColor(columns[0]) : "";
  const bodyColor = rows[0]?.[0] ? cellColor(rows[0][0]) : "";
  const textTargets = [
    ...columns.flatMap((cell, columnIndex) =>
      Array.isArray(cell.runs)
        ? cell.runs.flatMap((run, runIndex) => {
            const value = record(run);
            return value && typeof value.text === "string"
              ? [{
                  edit: {
                    kind: "run-text" as const,
                    section: "header" as const,
                    columnIndex,
                    runIndex,
                    value: value.text,
                  },
                  label: `Header ${columnIndex + 1}, run ${runIndex + 1}`,
                  text: value.text,
                  cell,
                }]
              : [];
          })
        : []
    ),
    ...rows.flatMap((row, rowIndex) =>
      row.flatMap((cell, columnIndex) =>
        Array.isArray(cell.runs)
          ? cell.runs.flatMap((run, runIndex) => {
              const value = record(run);
              return value && typeof value.text === "string"
                ? [{
                    edit: {
                      kind: "run-text" as const,
                      section: "body" as const,
                      rowIndex,
                      columnIndex,
                      runIndex,
                      value: value.text,
                    },
                    label: `Row ${rowIndex + 1}, column ${columnIndex + 1}, run ${runIndex + 1}`,
                    text: value.text,
                    cell,
                  }]
                : [];
            })
          : []
      )
    ),
  ];
  const safeTargetIndex = Math.min(targetIndex, Math.max(0, textTargets.length - 1));
  const selectedTarget = textTargets[safeTargetIndex];
  const selectedFont = record(selectedTarget?.cell.font);
  const selectedRowIndex =
    selectedTarget && "rowIndex" in selectedTarget.edit
      ? selectedTarget.edit.rowIndex
      : undefined;
  const minRows = integerValue(element.min_rows, rows.length);
  const maxRows = integerValue(element.max_rows, rows.length);
  const minColumns = Math.max(
    1,
    integerValue(element.min_columns, columns.length)
  );
  const maxColumns = integerValue(element.max_columns, columns.length);

  function applyContractEdit(
    edit: TableContractEdit,
    historyKey: string,
    continuous = false
  ) {
    const result = buildTableContractEditPatches(element, edit);
    if (result.ok && result.value.length > 0) {
      onPatch(result.value, { historyKey, continuous });
    }
  }

  function patchHeader(value: string) {
    onPatch(
      columns.map((cell, column) => cellColorPatch("columns", null, column, cell, value)),
      { historyKey: "toolbar:table-header-fill", continuous: true }
    );
  }

  function patchBody(value: string) {
    onPatch(
      rows.flatMap((row, rowIndex) =>
        row.map((cell, column) => cellColorPatch("rows", rowIndex, column, cell, value))
      ),
      { historyKey: "toolbar:table-body-fill", continuous: true }
    );
  }

  return (
    <div className="flex items-center gap-2">
      {selectedTarget && (
        <>
          <ToolbarField label="Table text target">
            <span aria-hidden="true">Cell</span>
            <ToolbarSelect
              aria-label="Table text target"
              value={safeTargetIndex}
              onChange={(event) => setTargetIndex(Number(event.target.value))}
            >
              {textTargets.map((target, index) => (
                <option key={`${target.label}-${index}`} value={index}>
                  {target.label}
                </option>
              ))}
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label="Table cell text">
            <input
              className={`${toolbarInputClass} w-32`}
              aria-label="Table cell text"
              type="text"
              value={selectedTarget.text}
              onChange={(event) =>
                applyContractEdit(
                  { ...selectedTarget.edit, value: event.target.value },
                  `toolbar:table-text-${safeTargetIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
          <ToolbarField label="Table cell font size">
            <span aria-hidden="true">Size</span>
            <input
              className={`${toolbarInputClass} w-16`}
              aria-label="Table cell font size"
              type="number"
              step="any"
              value={numberValue(selectedFont?.size, 18)}
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "cell-font",
                    section: selectedTarget.edit.section,
                    rowIndex: selectedRowIndex,
                    columnIndex: selectedTarget.edit.columnIndex,
                    field: "size",
                    value: event.target.valueAsNumber,
                  },
                  `toolbar:table-font-size-${safeTargetIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
          <ToolbarField label="Table cell font family">
            <span aria-hidden="true">Font</span>
            <input
              className={`${toolbarInputClass} w-28`}
              aria-label="Table cell font family"
              type="text"
              value={
                typeof selectedFont?.family === "string"
                  ? selectedFont.family
                  : ""
              }
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "cell-font",
                    section: selectedTarget.edit.section,
                    rowIndex: selectedRowIndex,
                    columnIndex: selectedTarget.edit.columnIndex,
                    field: "family",
                    value: event.target.value,
                  },
                  `toolbar:table-font-family-${safeTargetIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
          <ToolbarField label="Table cell font color">
            <span aria-hidden="true">Text</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label="Table cell font color"
              type="text"
              value={
                typeof selectedFont?.color === "string"
                  ? selectedFont.color
                  : ""
              }
              placeholder="#111827"
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "cell-font",
                    section: selectedTarget.edit.section,
                    rowIndex: selectedRowIndex,
                    columnIndex: selectedTarget.edit.columnIndex,
                    field: "color",
                    value: event.target.value,
                  },
                  `toolbar:table-font-color-${safeTargetIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
          {(["bold", "italic", "underline"] as const).map((field) => (
            <ToolbarButton
              key={field}
              aria-label={`Toggle table cell ${field}`}
              aria-pressed={selectedFont?.[field] === true}
              onClick={() =>
                applyContractEdit(
                  {
                    kind: "cell-font",
                    section: selectedTarget.edit.section,
                    rowIndex: selectedRowIndex,
                    columnIndex: selectedTarget.edit.columnIndex,
                    field,
                    value: selectedFont?.[field] !== true,
                  },
                  `toolbar:table-font-${field}-${safeTargetIndex}`
                )
              }
            >
              {field === "bold" ? "B" : field === "italic" ? "I" : "U"}
            </ToolbarButton>
          ))}
        </>
      )}
      <ToolbarField label="Table body row count">
        <span aria-hidden="true">Rows {rows.length}</span>
        <ToolbarButton
          aria-label="Remove table row"
          disabled={rows.length <= minRows}
          onClick={() =>
            applyContractEdit(
              { kind: "row-count", value: rows.length - 1 },
              "toolbar:table-rows"
            )
          }
        >
          −
        </ToolbarButton>
        <ToolbarButton
          aria-label="Add table row"
          disabled={rows.length >= maxRows}
          onClick={() =>
            applyContractEdit(
              { kind: "row-count", value: rows.length + 1 },
              "toolbar:table-rows"
            )
          }
        >
          +
        </ToolbarButton>
      </ToolbarField>
      <ToolbarField label="Table column count">
        <span aria-hidden="true">Columns {columns.length}</span>
        <ToolbarButton
          aria-label="Remove table column"
          disabled={columns.length <= minColumns}
          onClick={() =>
            applyContractEdit(
              { kind: "column-count", value: columns.length - 1 },
              "toolbar:table-columns"
            )
          }
        >
          −
        </ToolbarButton>
        <ToolbarButton
          aria-label="Add table column"
          disabled={columns.length >= maxColumns}
          onClick={() =>
            applyContractEdit(
              { kind: "column-count", value: columns.length + 1 },
              "toolbar:table-columns"
            )
          }
        >
          +
        </ToolbarButton>
      </ToolbarField>
      <ToolbarField label="Table header fill color">
        <span aria-hidden="true">Header</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Table header fill color"
          type="text"
          value={headerColor}
          placeholder="#f1f5f9"
          onChange={(event) => patchHeader(event.target.value)}
        />
      </ToolbarField>
      <ToolbarField label="Table body fill color">
        <span aria-hidden="true">Body</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Table body fill color"
          type="text"
          value={bodyColor}
          placeholder="#ffffff"
          onChange={(event) => patchBody(event.target.value)}
        />
      </ToolbarField>
      <ToolbarField label="Table opacity">
        <span aria-hidden="true">Opacity</span>
        <input
          className="w-20 accent-violet-400"
          aria-label="Table opacity"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numberValue(element.opacity, 1)}
          onChange={(event) =>
            onPatch([{ path: ["opacity"], value: Number(event.target.value) }], {
              historyKey: "toolbar:table-opacity",
              continuous: true,
            })
          }
        />
      </ToolbarField>
    </div>
  );
}
