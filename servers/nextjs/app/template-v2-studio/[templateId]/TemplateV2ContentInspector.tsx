"use client";

import { useTranslations } from "next-intl";
import { useState, type ChangeEvent } from "react";

import { isJsonRecord, type JsonRecord } from "@/lib/template-v2-studio";
import { type TemplateV2RunTarget } from "@/lib/template-v2-studio-content";
import { stringValue } from "@/lib/template-v2-konva";

const FIELD_LABEL_KEYS = {
  run: "contentInspector.fields.run",
  listItemRun: "contentInspector.fields.listItemRun",
  tableHeaderAlignment: "contentInspector.fields.tableHeaderAlignment",
  tableHeaderFill: "contentInspector.fields.tableHeaderFill",
  tableHeaderRun: "contentInspector.fields.tableHeaderRun",
  tableCellAlignment: "contentInspector.fields.tableCellAlignment",
  tableCellFill: "contentInspector.fields.tableCellFill",
  tableCellRun: "contentInspector.fields.tableCellRun",
  chartTitle: "contentInspector.fields.chartTitle",
  chartCategory: "contentInspector.fields.chartCategory",
  chartSeriesName: "contentInspector.fields.chartSeriesName",
  chartSeriesValue: "contentInspector.fields.chartSeriesValue",
  assetSource: "contentInspector.fields.assetSource",
  assetFit: "contentInspector.fields.assetFit",
  horizontalFocus: "contentInspector.fields.horizontalFocus",
  verticalFocus: "contentInspector.fields.verticalFocus",
  cropScale: "contentInspector.fields.cropScale",
} as const;

interface EditableField {
  key: string;
  labelKey: keyof typeof FIELD_LABEL_KEYS;
  labelValues?: Record<string, string | number>;
  value: string;
  target: TemplateV2RunTarget;
  control: "text" | "number" | "textarea" | "fit" | "alignment" | "color";
  min?: number;
  max?: number;
}

interface TemplateV2ContentInspectorProps {
  element: JsonRecord;
  pathLabel: string;
  disabled: boolean;
  onBlur: () => void;
  onEdit: (
    target: TemplateV2RunTarget,
    text: string,
    historyKey?: string,
  ) => void;
}

function textRuns(element: JsonRecord): EditableField[] {
  if (element.type !== "text" || !Array.isArray(element.runs)) return [];
  return element.runs.flatMap((run, runIndex) =>
    isJsonRecord(run)
      ? [
          {
            key: `text-${runIndex}`,
            labelKey: "run",
            labelValues: { run: runIndex + 1 },
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
                  labelKey: "listItemRun",
                  labelValues: {
                    item: itemIndex + 1,
                    run: runIndex + 1,
                  },
                  value: stringValue(run.text, ""),
                  target: {
                    kind: "list-item" as const,
                    itemIndex,
                    runIndex,
                  },
                  control: "textarea" as const,
                } satisfies EditableField,
              ]
            : [],
        )
      : [],
  );
}

function colorInputValue(value: unknown): string {
  const color = stringValue(
    isJsonRecord(value) ? value.color : undefined,
    "",
  );
  if (/^#[0-9a-f]{6}$/i.test(color)) return color;
  const shorthand = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(color);
  return shorthand
    ? `#${shorthand[1]}${shorthand[1]}${shorthand[2]}${shorthand[2]}${shorthand[3]}${shorthand[3]}`
    : "#ffffff";
}

function tableCellStyleFields(
  cell: JsonRecord,
  key: string,
  labelKeyPrefix: "tableHeader" | "tableCell",
  labelValues: Record<string, number>,
  alignmentTarget: TemplateV2RunTarget,
  colorTarget: TemplateV2RunTarget,
): EditableField[] {
  return [
    {
      key: `${key}-alignment`,
      labelKey: `${labelKeyPrefix}Alignment`,
      labelValues,
      value: ["left", "center", "right"].includes(String(cell.alignment))
        ? String(cell.alignment)
        : "left",
      target: alignmentTarget,
      control: "alignment",
    },
    {
      key: `${key}-color`,
      labelKey: `${labelKeyPrefix}Fill`,
      labelValues,
      value: colorInputValue(cell.color),
      target: colorTarget,
      control: "color",
    },
  ];
}

function tableRuns(element: JsonRecord): EditableField[] {
  if (element.type !== "table") return [];
  const columns = Array.isArray(element.columns) ? element.columns : [];
  const headerStyles = columns.flatMap((column, columnIndex) =>
    isJsonRecord(column)
      ? tableCellStyleFields(
          column,
          `header-${columnIndex}`,
          "tableHeader",
          { column: columnIndex + 1 },
          {
            kind: "table-column-style",
            columnIndex,
            property: "alignment",
          },
          {
            kind: "table-column-style",
            columnIndex,
            property: "color",
          },
        )
      : [],
  );
  const headers = columns.flatMap((column, columnIndex) =>
    isJsonRecord(column) &&
    !isJsonRecord(column.covered_by) &&
    Array.isArray(column.runs)
      ? column.runs.flatMap((run, runIndex) =>
          isJsonRecord(run)
            ? [
                {
                  key: `header-${columnIndex}-${runIndex}`,
                  labelKey: "tableHeaderRun",
                  labelValues: {
                    column: columnIndex + 1,
                    run: runIndex + 1,
                  },
                  value: stringValue(run.text, ""),
                  target: {
                    kind: "table-column" as const,
                    columnIndex,
                    runIndex,
                  },
                  control: "textarea" as const,
                } satisfies EditableField,
              ]
            : [],
        )
      : [],
  );
  const rows = Array.isArray(element.rows) ? element.rows : [];
  const cellStyles = rows.flatMap((row, rowIndex) =>
    Array.isArray(row)
      ? row.flatMap((cell, columnIndex) =>
          isJsonRecord(cell)
            ? tableCellStyleFields(
                cell,
                `cell-${rowIndex}-${columnIndex}`,
                "tableCell",
                { row: rowIndex + 1, column: columnIndex + 1 },
                {
                  kind: "table-cell-style",
                  rowIndex,
                  columnIndex,
                  property: "alignment",
                },
                {
                  kind: "table-cell-style",
                  rowIndex,
                  columnIndex,
                  property: "color",
                },
              )
            : [],
        )
      : [],
  );
  const cells = rows.flatMap((row, rowIndex) =>
    Array.isArray(row)
      ? row.flatMap((cell, columnIndex) =>
          isJsonRecord(cell) &&
          !isJsonRecord(cell.covered_by) &&
          Array.isArray(cell.runs)
            ? cell.runs.flatMap((run, runIndex) =>
                isJsonRecord(run)
                  ? [
                      {
                        key: `cell-${rowIndex}-${columnIndex}-${runIndex}`,
                        labelKey: "tableCellRun",
                        labelValues: {
                          row: rowIndex + 1,
                          column: columnIndex + 1,
                          run: runIndex + 1,
                        },
                        value: stringValue(run.text, ""),
                        target: {
                          kind: "table-cell" as const,
                          rowIndex,
                          columnIndex,
                          runIndex,
                        },
                        control: "textarea" as const,
                      } satisfies EditableField,
                    ]
                  : [],
              )
            : [],
        )
      : [],
  );
  return [...headerStyles, ...headers, ...cellStyles, ...cells];
}

function chartFields(element: JsonRecord): EditableField[] {
  if (element.type !== "chart") return [];
  const title: EditableField = {
    key: "chart-title",
    labelKey: "chartTitle",
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
                labelKey: "chartCategory",
                labelValues: { category: categoryIndex + 1 },
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
          labelKey: "chartSeriesName",
          labelValues: { series: seriesIndex + 1 },
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
                      labelKey: "chartSeriesValue",
                      labelValues: {
                        series: seriesIndex + 1,
                        value: valueIndex + 1,
                      },
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
      labelKey: "assetSource",
      value: stringValue(element.data, ""),
      target: { kind: "asset-data" },
      control: "textarea",
    },
    {
      key: "asset-fit",
      labelKey: "assetFit",
      value: ["fill", "contain", "cover"].includes(String(element.fit))
        ? String(element.fit)
        : "fill",
      target: { kind: "asset-fit" },
      control: "fit",
    },
    {
      key: "asset-focus-x",
      labelKey: "horizontalFocus",
      value: String(
        typeof element.focus_x === "number" ? element.focus_x : 50,
      ),
      target: { kind: "asset-focus", axis: "x" },
      control: "number",
      min: 0,
      max: 100,
    },
    {
      key: "asset-focus-y",
      labelKey: "verticalFocus",
      value: String(
        typeof element.focus_y === "number" ? element.focus_y : 50,
      ),
      target: { kind: "asset-focus", axis: "y" },
      control: "number",
      min: 0,
      max: 100,
    },
    {
      key: "asset-crop-scale",
      labelKey: "cropScale",
      value: String(
        typeof element.crop_scale === "number" ? element.crop_scale : 1,
      ),
      target: { kind: "asset-crop-scale" },
      control: "number",
      min: 1,
      max: 6,
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
  const t = useTranslations("studio");
  const [transaction, setTransaction] = useState(0);
  const [draftValues, setDraftValues] = useState<Record<string, string>>({});
  const handleFocus = (key: string, value: string) => {
    setTransaction((current) => current + 1);
    setDraftValues((current) => ({ ...current, [key]: value }));
  };
  const handleEdit = (target: TemplateV2RunTarget, value: string) => {
    onEdit(target, value, `content-${transaction}`);
  };
  const handleBlur = (key: string) => {
    setDraftValues((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    onBlur();
  };
  const fields = editableFields(element);
  const series =
    element.type === "chart" && Array.isArray(element.series)
      ? element.series
      : [];
  if (fields.length === 0) {
    return (
      <p className="mt-5 rounded-lg bg-slate-950 p-3 text-sm text-slate-400">
        {t("contentInspector.noEditableFields")}
      </p>
    );
  }

  return (
    <div className="mt-5 space-y-4">
      <p className="text-sm font-medium">
        {t("contentInspector.editableContent", { path: pathLabel })}
      </p>
      {series.length > 1 ? (
        <fieldset className="space-y-2 rounded-lg border border-slate-800 p-3">
          <legend className="px-1 text-xs font-medium text-slate-400">
            {t("contentInspector.seriesOrder")}
          </legend>
          {series.map((item, seriesIndex) => (
            <div
              key={seriesIndex}
              className="flex items-center justify-between gap-2 text-xs text-slate-300"
            >
              <span className="truncate">
                {seriesIndex + 1}.{" "}
                {isJsonRecord(item)
                  ? stringValue(
                      item.name,
                      t("contentInspector.series", {
                        series: seriesIndex + 1,
                      }),
                    )
                  : t("contentInspector.series", {
                      series: seriesIndex + 1,
                    })}
              </span>
              <span className="flex shrink-0 gap-1">
                <button
                  type="button"
                  disabled={disabled || seriesIndex === 0}
                  aria-label={t("contentInspector.moveSeriesUpAria", {
                    series: seriesIndex + 1,
                  })}
                  onClick={() =>
                    onEdit(
                      { kind: "chart-series-order", seriesIndex },
                      String(seriesIndex - 1),
                    )
                  }
                  className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
                >
                  {t("contentInspector.up")}
                </button>
                <button
                  type="button"
                  disabled={disabled || seriesIndex === series.length - 1}
                  aria-label={t("contentInspector.moveSeriesDownAria", {
                    series: seriesIndex + 1,
                  })}
                  onClick={() =>
                    onEdit(
                      { kind: "chart-series-order", seriesIndex },
                      String(seriesIndex + 1),
                    )
                  }
                  className="rounded border border-slate-700 px-2 py-1 disabled:opacity-40"
                >
                  {t("contentInspector.down")}
                </button>
              </span>
            </div>
          ))}
        </fieldset>
      ) : null}
      {fields.map(
        ({
          key,
          labelKey,
          labelValues,
          value,
          target,
          control,
          min,
          max,
        }) => {
          const label = t(FIELD_LABEL_KEYS[labelKey], labelValues);
          const shared = {
            value: draftValues[key] ?? value,
            disabled,
            "aria-label": t("contentInspector.fieldContentAria", { label }),
            onFocus: () => handleFocus(key, value),
            onBlur: () => handleBlur(key),
            onChange: (
              event: ChangeEvent<
                HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
              >,
            ) => {
              const nextValue = event.target.value;
              setDraftValues((current) => ({ ...current, [key]: nextValue }));
              handleEdit(target, nextValue);
            },
            className:
              "mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-violet-400",
          };
          return (
            <label key={key} className="block text-xs text-slate-400">
              {label}
              {control === "textarea" ? (
                <textarea
                  {...shared}
                  className={`${shared.className} min-h-20`}
                />
              ) : control === "fit" || control === "alignment" ? (
                <select {...shared}>
                  {control === "fit" ? (
                    <>
                      <option value="fill">
                        {t("contentInspector.fit.fill")}
                      </option>
                      <option value="contain">
                        {t("contentInspector.fit.contain")}
                      </option>
                      <option value="cover">
                        {t("contentInspector.fit.cover")}
                      </option>
                    </>
                  ) : (
                    <>
                      <option value="left">
                        {t("contentInspector.alignment.left")}
                      </option>
                      <option value="center">
                        {t("contentInspector.alignment.center")}
                      </option>
                      <option value="right">
                        {t("contentInspector.alignment.right")}
                      </option>
                    </>
                  )}
                </select>
              ) : (
                <input
                  {...shared}
                  type={control}
                  step={control === "number" ? "any" : undefined}
                  min={min}
                  max={max}
                />
              )}
            </label>
          );
        },
      )}
      {element.type === "image" ? (
        <p className="text-xs text-slate-500">
          {t("contentInspector.assetHelp")}
        </p>
      ) : null}
      {element.type === "table" ? (
        <p className="text-xs text-slate-500">
          {t("contentInspector.tableHelp")}
        </p>
      ) : null}
    </div>
  );
}
