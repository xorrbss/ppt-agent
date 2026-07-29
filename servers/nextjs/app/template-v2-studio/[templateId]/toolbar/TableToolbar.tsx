"use client";

import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import { ToolbarField, toolbarInputClass } from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

const record = (value: unknown): TemplateV2ToolbarRecord | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as TemplateV2ToolbarRecord)
    : null;

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

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
  const columns = element.columns as TemplateV2ToolbarRecord[];
  const rows = element.rows as TemplateV2ToolbarRecord[][];
  const headerColor = cellColor(columns[0]);
  const bodyColor = cellColor(rows[0][0]);

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
