"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
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
  return color && typeof color.color === "string" ? color.color : "";
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
  const t = useTranslations("studio");
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
                  label: t("toolbar.table.headerTarget", {
                    column: columnIndex + 1,
                    run: runIndex + 1,
                  }),
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
                    label: t("toolbar.table.bodyTarget", {
                      row: rowIndex + 1,
                      column: columnIndex + 1,
                      run: runIndex + 1,
                    }),
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
    applyContractEdit(
      { kind: "section-fill", section: "header", value },
      "toolbar:table-header-fill",
      true
    );
  }

  function patchBody(value: string) {
    applyContractEdit(
      { kind: "section-fill", section: "body", value },
      "toolbar:table-body-fill",
      true
    );
  }

  return (
    <div className="flex items-center gap-2">
      {selectedTarget && (
        <>
          <ToolbarField label={t("toolbar.table.textTarget")}>
            <span aria-hidden="true">{t("toolbar.table.cell")}</span>
            <ToolbarSelect
              aria-label={t("toolbar.table.textTarget")}
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
          <ToolbarField label={t("toolbar.table.cellText")}>
            <input
              className={`${toolbarInputClass} w-32`}
              aria-label={t("toolbar.table.cellText")}
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
          <ToolbarField label={t("toolbar.table.cellFontSize")}>
            <span aria-hidden="true">{t("toolbar.table.size")}</span>
            <input
              className={`${toolbarInputClass} w-16`}
              aria-label={t("toolbar.table.cellFontSize")}
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
          <ToolbarField label={t("toolbar.table.cellFontFamily")}>
            <span aria-hidden="true">{t("toolbar.table.font")}</span>
            <input
              className={`${toolbarInputClass} w-28`}
              aria-label={t("toolbar.table.cellFontFamily")}
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
          <ToolbarField label={t("toolbar.table.cellAlignment")}>
            <span aria-hidden="true">{t("toolbar.table.align")}</span>
            <ToolbarSelect
              aria-label={t("toolbar.table.cellAlignment")}
              value={
                selectedTarget.cell.alignment === "center" ||
                selectedTarget.cell.alignment === "right"
                  ? selectedTarget.cell.alignment
                  : "left"
              }
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "cell-alignment",
                    section: selectedTarget.edit.section,
                    rowIndex: selectedRowIndex,
                    columnIndex: selectedTarget.edit.columnIndex,
                    value: event.target.value,
                  },
                  `toolbar:table-alignment-${safeTargetIndex}`
                )
              }
            >
              <option value="left">{t("toolbar.common.left")}</option>
              <option value="center">{t("toolbar.common.center")}</option>
              <option value="right">{t("toolbar.common.right")}</option>
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label={t("toolbar.table.cellFontColor")}>
            <span aria-hidden="true">{t("toolbar.table.text")}</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label={t("toolbar.table.cellFontColor")}
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
              aria-label={t(`toolbar.table.toggleStyles.${field}`)}
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
              {t(`toolbar.table.styleShort.${field}`)}
            </ToolbarButton>
          ))}
        </>
      )}
      <ToolbarField label={t("toolbar.table.bodyRowCount")}>
        <span aria-hidden="true">
          {t("toolbar.table.rowsCount", { count: rows.length })}
        </span>
        <ToolbarButton
          aria-label={t("toolbar.table.removeRow")}
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
          aria-label={t("toolbar.table.addRow")}
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
      <ToolbarField label={t("toolbar.table.columnCount")}>
        <span aria-hidden="true">
          {t("toolbar.table.columnsCount", { count: columns.length })}
        </span>
        <ToolbarButton
          aria-label={t("toolbar.table.removeColumn")}
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
          aria-label={t("toolbar.table.addColumn")}
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
      <ToolbarField label={t("toolbar.table.headerFillColor")}>
        <span aria-hidden="true">{t("toolbar.table.header")}</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.table.headerFillColor")}
          type="text"
          value={headerColor}
          placeholder="#f1f5f9"
          onChange={(event) => patchHeader(event.target.value)}
        />
      </ToolbarField>
      <ToolbarField label={t("toolbar.table.bodyFillColor")}>
        <span aria-hidden="true">{t("toolbar.table.body")}</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.table.bodyFillColor")}
          type="text"
          value={bodyColor}
          placeholder="#ffffff"
          onChange={(event) => patchBody(event.target.value)}
        />
      </ToolbarField>
      <ToolbarField label={t("toolbar.table.opacity")}>
        <span aria-hidden="true">{t("toolbar.common.opacity")}</span>
        <input
          className="w-20 accent-violet-400"
          aria-label={t("toolbar.table.opacity")}
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
