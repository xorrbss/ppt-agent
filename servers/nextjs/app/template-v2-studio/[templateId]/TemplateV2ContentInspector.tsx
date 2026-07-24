"use client";

import { useRef } from "react";

import { isJsonRecord, type JsonRecord } from "@/lib/template-v2-studio";
import { type TemplateV2RunTarget } from "@/lib/template-v2-studio-content";
import { stringValue } from "@/lib/template-v2-konva";

interface EditableRun {
  key: string;
  label: string;
  run: JsonRecord;
  target: TemplateV2RunTarget;
}

interface TemplateV2ContentInspectorProps {
  element: JsonRecord;
  pathLabel: string;
  disabled: boolean;
  onBlur: () => void;
  onEdit: (
    target: TemplateV2RunTarget,
    text: string,
    historyKey: string,
  ) => void;
}

function textRuns(element: JsonRecord): EditableRun[] {
  if (element.type !== "text" || !Array.isArray(element.runs)) return [];
  return element.runs.flatMap((run, runIndex) =>
    isJsonRecord(run)
      ? [
          {
            key: `text-${runIndex}`,
            label: `Run ${runIndex + 1}`,
            run,
            target: { kind: "text" as const, runIndex },
          },
        ]
      : [],
  );
}

function listRuns(element: JsonRecord): EditableRun[] {
  if (element.type !== "text-list" || !Array.isArray(element.items)) return [];
  return element.items.flatMap((item, itemIndex) =>
    Array.isArray(item)
      ? item.flatMap((run, runIndex) =>
          isJsonRecord(run)
            ? [
                {
                  key: `item-${itemIndex}-${runIndex}`,
                  label: `Item ${itemIndex + 1} · Run ${runIndex + 1}`,
                  run,
                  target: {
                    kind: "list-item" as const,
                    itemIndex,
                    runIndex,
                  },
                },
              ]
            : [],
        )
      : [],
  );
}

function tableRuns(element: JsonRecord): EditableRun[] {
  if (element.type !== "table") return [];
  const columns = Array.isArray(element.columns) ? element.columns : [];
  const headers = columns.flatMap((column, columnIndex) =>
    isJsonRecord(column) && Array.isArray(column.runs)
      ? column.runs.flatMap((run, runIndex) =>
          isJsonRecord(run)
            ? [
                {
                  key: `header-${columnIndex}-${runIndex}`,
                  label: `Header ${columnIndex + 1} · Run ${runIndex + 1}`,
                  run,
                  target: {
                    kind: "table-column" as const,
                    columnIndex,
                    runIndex,
                  },
                },
              ]
            : [],
        )
      : [],
  );
  const rows = Array.isArray(element.rows) ? element.rows : [];
  const cells = rows.flatMap((row, rowIndex) =>
    Array.isArray(row)
      ? row.flatMap((cell, columnIndex) =>
          isJsonRecord(cell) && Array.isArray(cell.runs)
            ? cell.runs.flatMap((run, runIndex) =>
                isJsonRecord(run)
                  ? [
                      {
                        key: `cell-${rowIndex}-${columnIndex}-${runIndex}`,
                        label: `Row ${rowIndex + 1}, cell ${columnIndex + 1} · Run ${runIndex + 1}`,
                        run,
                        target: {
                          kind: "table-cell" as const,
                          rowIndex,
                          columnIndex,
                          runIndex,
                        },
                      },
                    ]
                  : [],
              )
            : [],
        )
      : [],
  );
  return [...headers, ...cells];
}

function editableRuns(element: JsonRecord): EditableRun[] {
  return [...textRuns(element), ...listRuns(element), ...tableRuns(element)];
}

export default function TemplateV2ContentInspector({
  element,
  pathLabel,
  disabled,
  onBlur,
  onEdit,
}: TemplateV2ContentInspectorProps) {
  const transactionRef = useRef(0);
  const runs = editableRuns(element);
  if (runs.length === 0) {
    return (
      <p className="mt-5 rounded-lg bg-slate-950 p-3 text-sm text-slate-400">
        This element has no editable text, list item, or table cell runs.
        Unsupported fields remain lossless.
      </p>
    );
  }

  return (
    <div className="mt-5 space-y-4">
      <p className="text-sm font-medium">Content runs · {pathLabel}</p>
      {runs.map(({ key, label, run, target }) => (
        <label key={key} className="block text-xs text-slate-400">
          {label}
          <textarea
            value={stringValue(run.text, "")}
            disabled={disabled}
            onFocus={() => {
              transactionRef.current += 1;
            }}
            onBlur={onBlur}
            onChange={(event) =>
              onEdit(
                target,
                event.target.value,
                `content-${transactionRef.current}`,
              )
            }
            className="mt-1 min-h-20 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-400"
          />
        </label>
      ))}
    </div>
  );
}
