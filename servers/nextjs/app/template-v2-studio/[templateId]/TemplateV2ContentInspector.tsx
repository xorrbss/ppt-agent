"use client";

import { useState, type ChangeEvent } from "react";

import { isJsonRecord, type JsonRecord } from "@/lib/template-v2-studio";
import { type TemplateV2RunTarget } from "@/lib/template-v2-studio-content";
import { stringValue } from "@/lib/template-v2-konva";

interface EditableField {
  key: string;
  label: string;
  value: string;
  target: TemplateV2RunTarget;
  control: "text" | "number" | "textarea" | "fit";
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

function textRuns(element: JsonRecord): EditableField[] {
  if (element.type !== "text" || !Array.isArray(element.runs)) return [];
  return element.runs.flatMap((run, runIndex) =>
    isJsonRecord(run)
      ? [
          {
            key: `text-${runIndex}`,
            label: `Run ${runIndex + 1}`,
            value: stringValue(run.text, ""),
            target: { kind: "text" as const, runIndex },
            control: "textarea" as const,
          },
        ]
      : [],
  );
}

function listRuns(element: JsonRecord): EditableField[] {
  if (element.type !== "text-list" || !Array.isArray(element.items)) return [];
  return element.items.flatMap((item, itemIndex) =>
    Array.isArray(item)
      ? item.flatMap((run, runIndex) =>
          isJsonRecord(run)
            ? [
                {
                  key: `item-${itemIndex}-${runIndex}`,
                  label: `Item ${itemIndex + 1} · Run ${runIndex + 1}`,
                  value: stringValue(run.text, ""),
                  target: {
                    kind: "list-item" as const,
                    itemIndex,
                    runIndex,
                  },
                  control: "textarea" as const,
                },
              ]
            : [],
        )
      : [],
  );
}

function tableRuns(element: JsonRecord): EditableField[] {
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
                  value: stringValue(run.text, ""),
                  target: {
                    kind: "table-column" as const,
                    columnIndex,
                    runIndex,
                  },
                  control: "textarea" as const,
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
                        value: stringValue(run.text, ""),
                        target: {
                          kind: "table-cell" as const,
                          rowIndex,
                          columnIndex,
                          runIndex,
                        },
                        control: "textarea" as const,
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

function chartFields(element: JsonRecord): EditableField[] {
  if (element.type !== "chart") return [];
  const title: EditableField = {
    key: "chart-title",
    label: "Chart title",
    value: stringValue(element.title, ""),
    target: { kind: "chart-title" },
    control: "text",
  };
  const categories: EditableField[] = Array.isArray(element.categories)
    ? element.categories.flatMap((category, categoryIndex) =>
        typeof category === "string"
          ? [
              {
                key: `chart-category-${categoryIndex}`,
                label: `Category ${categoryIndex + 1}`,
                value: category,
                target: {
                  kind: "chart-category" as const,
                  categoryIndex,
                },
                control: "text" as const,
              },
            ]
          : [],
      )
    : [];
  const series: EditableField[] = Array.isArray(element.series)
    ? element.series.flatMap((item, seriesIndex) => {
        if (!isJsonRecord(item)) return [];
        const name: EditableField = {
          key: `chart-series-${seriesIndex}-name`,
          label: `Series ${seriesIndex + 1} name`,
          value: stringValue(item.name, ""),
          target: { kind: "chart-series-name", seriesIndex },
          control: "text",
        };
        const values: EditableField[] = Array.isArray(item.values)
          ? item.values.flatMap((value, valueIndex) =>
              typeof value === "number"
                ? [
                    {
                      key: `chart-series-${seriesIndex}-value-${valueIndex}`,
                      label: `Series ${seriesIndex + 1}, value ${valueIndex + 1}`,
                      value: String(value),
                      target: {
                        kind: "chart-series-value" as const,
                        seriesIndex,
                        valueIndex,
                      },
                      control: "number" as const,
                    },
                  ]
                : [],
            )
          : [];
        return [name, ...values];
      })
    : [];
  return [title, ...categories, ...series];
}

function assetFields(element: JsonRecord): EditableField[] {
  if (element.type !== "image") return [];
  return [
    {
      key: "asset-data",
      label: "Asset source",
      value: stringValue(element.data, ""),
      target: { kind: "asset-data" },
      control: "textarea",
    },
    {
      key: "asset-fit",
      label: "Asset fit",
      value: ["fill", "contain", "cover"].includes(String(element.fit))
        ? String(element.fit)
        : "fill",
      target: { kind: "asset-fit" },
      control: "fit",
    },
  ];
}

function editableFields(element: JsonRecord): EditableField[] {
  return [
    ...textRuns(element),
    ...listRuns(element),
    ...tableRuns(element),
    ...chartFields(element),
    ...assetFields(element),
  ];
}

export default function TemplateV2ContentInspector({
  element,
  pathLabel,
  disabled,
  onBlur,
  onEdit,
}: TemplateV2ContentInspectorProps) {
  const [transaction, setTransaction] = useState(0);
  const handleFocus = () => {
    setTransaction((current) => current + 1);
  };
  const handleEdit = (target: TemplateV2RunTarget, value: string) => {
    onEdit(target, value, `content-${transaction}`);
  };
  const fields = editableFields(element);
  if (fields.length === 0) {
    return (
      <p className="mt-5 rounded-lg bg-slate-950 p-3 text-sm text-slate-400">
        This element has no editable text, table, chart, or asset fields.
        Unsupported fields remain lossless.
      </p>
    );
  }

  return (
    <div className="mt-5 space-y-4">
      <p className="text-sm font-medium">Editable content · {pathLabel}</p>
      {fields.map(({ key, label, value, target, control }) => {
        const shared = {
          value,
          disabled,
          onFocus: handleFocus,
          onBlur,
          onChange: (
            event: ChangeEvent<
              HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
            >,
          ) => handleEdit(target, event.target.value),
          className:
            "mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-400",
        };
        return (
          <label key={key} className="block text-xs text-slate-400">
            {label}
            {control === "textarea" ? (
              <textarea {...shared} className={`${shared.className} min-h-20`} />
            ) : control === "fit" ? (
              <select {...shared}>
                <option value="fill">Fill</option>
                <option value="contain">Contain</option>
                <option value="cover">Cover</option>
              </select>
            ) : (
              <input
                {...shared}
                type={control}
                step={control === "number" ? "any" : undefined}
              />
            )}
          </label>
        );
      })}
      {element.type === "image" ? (
        <p className="text-xs text-slate-500">
          Asset sources must be an app-relative path or an inline image data URI.
          Remote URLs are rejected.
        </p>
      ) : null}
    </div>
  );
}
