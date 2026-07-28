"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import {
  TEMPLATE_V2_CHART_COPILOT_LIMITS,
  TEMPLATE_V2_CHART_TYPES,
  TemplateV2ChartCopilotError,
  importTemplateV2ChartData,
  previewTemplateV2ChartCopilot,
  recommendTemplateV2Chart,
  type JsonRecord,
  type TemplateV2ChartControl,
  type TemplateV2ChartCopilotOperation,
  type TemplateV2ChartCopilotPreview,
  type TemplateV2ChartType,
} from "@/lib/template-v2-chart-copilot";

const AXIS_CHART_TYPES = new Set<TemplateV2ChartType>([
  "area",
  "bar",
  "horizontal_bar",
  "horizontal_stacked_bar",
  "line",
  "stacked_bar",
]);

const AXIS_CONTROLS: readonly TemplateV2ChartControl[] = [
  "x_axis",
  "y_axis",
  "x_axis_title",
  "y_axis_title",
  "axis_color",
  "x_axis_grid",
  "y_axis_grid",
  "grid_color",
];

const CONTROL_LABELS: ReadonlyArray<{
  control: Extract<
    TemplateV2ChartControl,
    "legend" | "x_axis" | "y_axis" | "x_axis_grid" | "y_axis_grid"
  >;
  label: string;
  axis?: boolean;
}> = [
  { control: "legend", label: "Show legend" },
  { control: "x_axis", label: "Show X axis", axis: true },
  { control: "y_axis", label: "Show Y axis", axis: true },
  { control: "x_axis_grid", label: "Show X grid", axis: true },
  { control: "y_axis_grid", label: "Show Y grid", axis: true },
];

function errorMessage(error: unknown): string {
  if (error instanceof TemplateV2ChartCopilotError) {
    return `${error.code} (${error.path})`;
  }
  return "template_v2_chart_copilot_invalid_operation";
}

function chartPreview(
  element: JsonRecord,
  operations: readonly TemplateV2ChartCopilotOperation[],
): TemplateV2ChartCopilotPreview {
  return previewTemplateV2ChartCopilot(element, operations);
}

function replaceLatestControl(
  operations: readonly TemplateV2ChartCopilotOperation[],
  operation: Extract<TemplateV2ChartCopilotOperation, { kind: "set-control" }>,
): TemplateV2ChartCopilotOperation[] {
  return [
    ...operations.filter(
      (item) =>
        item.kind !== "set-control" || item.control !== operation.control,
    ),
    operation,
  ];
}

function formatDiffValue(value: unknown): string {
  if (value === undefined) return "unset";
  const formatted = JSON.stringify(value);
  return formatted.length > 90 ? `${formatted.slice(0, 87)}...` : formatted;
}

function SeriesValueInput({
  label,
  value,
  disabled,
  onCommit,
}: {
  label: string;
  value: number;
  disabled: boolean;
  onCommit(value: number): void;
}) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);

  function commit() {
    if (!text.trim()) {
      setText(String(value));
      return;
    }
    const nextValue = Number(text);
    if (!Number.isFinite(nextValue)) {
      setText(String(value));
      return;
    }
    onCommit(nextValue);
  }

  return (
    <input
      type="number"
      aria-label={label}
      className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
      value={text}
      disabled={disabled}
      onChange={(event) => setText(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
    />
  );
}

export default function TemplateV2ChartCopilotPanel({
  element,
  disabled,
  onApply,
}: {
  element: JsonRecord;
  disabled: boolean;
  onApply(chart: JsonRecord, historyKey: string): void;
}) {
  const [operations, setOperations] = useState<
    TemplateV2ChartCopilotOperation[]
  >([]);
  const operationsRef = useRef<TemplateV2ChartCopilotOperation[]>([]);
  const [review, setReview] = useState<TemplateV2ChartCopilotPreview | null>(
    null,
  );
  const [importFormat, setImportFormat] = useState<"csv" | "tsv">("csv");
  const [importText, setImportText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const current = useMemo(() => {
    try {
      return chartPreview(element, operations).after;
    } catch {
      return null;
    }
  }, [element, operations]);

  if (element.type !== "chart") return null;

  if (!current) {
    let contractError = "template_v2_chart_copilot_invalid_chart";
    try {
      chartPreview(element, operations);
    } catch (caught) {
      contractError = errorMessage(caught);
    }
    return (
      <section
        aria-label="AI chart copilot"
        className="mt-5 rounded-xl border border-red-500/30 bg-red-950/20 p-3"
      >
        <h3 className="text-sm font-semibold text-red-100">AI chart copilot</h3>
        <p role="alert" className="mt-2 text-xs text-red-300">
          Chart editing is blocked by the strict contract: {contractError}
        </p>
      </section>
    );
  }

  const axisCompatible = AXIS_CHART_TYPES.has(current.chart_type);
  const controlsDisabled = disabled || review !== null;

  function updateOperations(
    next: TemplateV2ChartCopilotOperation[],
    nextStatus?: string,
  ) {
    try {
      chartPreview(element, next);
      operationsRef.current = next;
      setOperations(next);
      setReview(null);
      setError(null);
      setStatus(nextStatus ?? null);
    } catch (caught) {
      setError(errorMessage(caught));
      setStatus(null);
    }
  }

  function append(operation: TemplateV2ChartCopilotOperation) {
    updateOperations([...operationsRef.current, operation]);
  }

  function setControl(
    control: TemplateV2ChartControl,
    value: string | boolean | null,
  ) {
    let next = operationsRef.current;
    if (
      control === "chart_type" &&
      typeof value === "string" &&
      !AXIS_CHART_TYPES.has(value as TemplateV2ChartType)
    ) {
      for (const axisControl of AXIS_CONTROLS) {
        next = replaceLatestControl(next, {
          kind: "set-control",
          control: axisControl,
          value: null,
        });
      }
    }
    updateOperations(
      replaceLatestControl(next, { kind: "set-control", control, value }),
    );
  }

  function previewChanges() {
    try {
      const next = chartPreview(element, operationsRef.current);
      setReview(next);
      setError(null);
      setStatus(
        next.diff.length
          ? "Preview ready. No chart data has been changed yet."
          : "There are no changes to preview.",
      );
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function cancel() {
    operationsRef.current = [];
    setOperations([]);
    setReview(null);
    setError(null);
    setStatus("Draft canceled. No chart data changed.");
  }

  function apply() {
    if (!review || review.diff.length === 0) return;
    onApply(
      review.after,
      `chart-copilot-${review.after.chart_type}-${review.operations.length}`,
    );
    operationsRef.current = [];
    setOperations([]);
    setReview(null);
    setError(null);
    setStatus("Chart patch applied. Autosave and global undo remain available.");
  }

  function importData() {
    try {
      const imported = importTemplateV2ChartData({
        format: importFormat,
        text: importText,
      });
      append({ kind: "replace-data", ...imported });
      setStatus(
        `Imported ${imported.categories.length} categories and ${imported.series.length} series into the draft.`,
      );
    } catch (caught) {
      setError(errorMessage(caught));
      setStatus(null);
    }
  }

  function useRecommendation() {
    try {
      const recommendation = recommendTemplateV2Chart(current);
      setControl("chart_type", recommendation.chartType);
      setStatus(
        `Recommendation: ${recommendation.chartType} (${recommendation.reasonCode}, ${recommendation.confidence}). Review before applying.`,
      );
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <section
      aria-label="AI chart copilot"
      className="mt-5 rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-cyan-100">
            AI chart copilot
          </h3>
          <p className="mt-1 text-xs text-slate-400">
            Deterministic local draft · bounded patch only · no provider call
          </p>
        </div>
        <span className="rounded bg-slate-950 px-2 py-1 text-[10px] text-emerald-300">
          strict
        </span>
      </div>

      <fieldset className="mt-3 space-y-3" disabled={controlsDisabled}>
        <legend className="text-xs font-medium text-slate-200">
          Chart controls
        </legend>
        <label className="block text-xs text-slate-300">
          Chart type
          <select
            aria-label="Chart copilot chart type"
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2"
            value={current.chart_type}
            onChange={(event) => setControl("chart_type", event.target.value)}
          >
            {TEMPLATE_V2_CHART_TYPES.map((chartType) => (
              <option key={chartType} value={chartType}>
                {chartType}
              </option>
            ))}
          </select>
        </label>

        <label className="block text-xs text-slate-300">
          Title
          <input
            aria-label="Chart copilot title"
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2"
            value={typeof current.title === "string" ? current.title : ""}
            maxLength={TEMPLATE_V2_CHART_COPILOT_LIMITS.maxTitleCharacters}
            onChange={(event) => setControl("title", event.target.value)}
          />
        </label>

        <div className="grid grid-cols-2 gap-2">
          {CONTROL_LABELS.map(({ control, label, axis }) => (
            <label
              key={control}
              className={`flex items-center gap-2 rounded border border-slate-800 p-2 text-xs ${
                axis && !axisCompatible ? "text-slate-600" : "text-slate-300"
              }`}
            >
              <input
                type="checkbox"
                aria-label={`Chart copilot ${label}`}
                checked={current[control] === true}
                disabled={controlsDisabled || (axis && !axisCompatible)}
                onChange={(event) => setControl(control, event.target.checked)}
              />
              {label}
            </label>
          ))}
        </div>

        {!axisCompatible ? (
          <p className="rounded bg-amber-950/30 p-2 text-xs text-amber-300">
            Axis and grid controls are incompatible with {current.chart_type}{" "}
            and are fail-closed.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {(["x", "y"] as const).map((axis) => (
              <label key={axis} className="text-xs text-slate-300">
                {axis.toUpperCase()} axis title
                <input
                  aria-label={`Chart copilot ${axis.toUpperCase()} axis title`}
                  className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2"
                  value={
                    typeof current[`${axis}_axis_title`] === "string"
                      ? String(current[`${axis}_axis_title`])
                      : ""
                  }
                  onChange={(event) =>
                    setControl(`${axis}_axis_title`, event.target.value)
                  }
                />
              </label>
            ))}
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          {(
            [
              ["title_color", "Title color"],
              ["legend_color", "Legend color"],
              ["axis_color", "Axis color"],
              ["grid_color", "Grid color"],
            ] as const
          ).map(([control, label]) => {
            const incompatible =
              (control === "axis_color" || control === "grid_color") &&
              !axisCompatible;
            return (
              <label key={control} className="text-xs text-slate-300">
                {label}
                <input
                  aria-label={`Chart copilot ${label}`}
                  className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2"
                  value={
                    typeof current[control] === "string"
                      ? String(current[control])
                      : ""
                  }
                  disabled={controlsDisabled || incompatible}
                  placeholder="#334155"
                  onChange={(event) =>
                    event.target.value &&
                    setControl(control, event.target.value)
                  }
                />
              </label>
            );
          })}
        </div>

        <label className="block text-xs text-slate-300">
          Data labels
          <select
            aria-label="Chart copilot data labels"
            className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2"
            value={
              typeof current.data_labels === "string"
                ? current.data_labels
                : ""
            }
            onChange={(event) =>
              setControl("data_labels", event.target.value || null)
            }
          >
            <option value="">None</option>
            <option value="base">Base</option>
            <option value="mid">Middle</option>
            <option value="top">Top</option>
            <option value="outside">Outside</option>
          </select>
        </label>
      </fieldset>

      <fieldset
        className="mt-4 space-y-2 rounded-lg border border-slate-800 p-3"
        disabled={controlsDisabled}
      >
        <legend className="px-1 text-xs font-medium text-slate-200">
          Series
        </legend>
        {current.series.map((series, seriesIndex) => (
          <div
            key={`${seriesIndex}-${series.name}`}
            className="rounded border border-slate-800 p-2"
          >
            <div className="flex gap-1">
              <input
                aria-label={`Chart copilot series ${seriesIndex + 1} name`}
                className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
                value={series.name}
                maxLength={
                  TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeriesNameCharacters
                }
                onChange={(event) => {
                  if (event.target.value) {
                    append({
                      kind: "rename-series",
                      seriesIndex,
                      name: event.target.value,
                    });
                  }
                }}
              />
              <button
                type="button"
                aria-label={`Move chart series ${seriesIndex + 1} up`}
                disabled={controlsDisabled || seriesIndex === 0}
                className="rounded border border-slate-700 px-2 text-xs disabled:opacity-30"
                onClick={() =>
                  append({
                    kind: "move-series",
                    seriesIndex,
                    destinationIndex: seriesIndex - 1,
                  })
                }
              >
                ↑
              </button>
              <button
                type="button"
                aria-label={`Move chart series ${seriesIndex + 1} down`}
                disabled={
                  controlsDisabled || seriesIndex === current.series.length - 1
                }
                className="rounded border border-slate-700 px-2 text-xs disabled:opacity-30"
                onClick={() =>
                  append({
                    kind: "move-series",
                    seriesIndex,
                    destinationIndex: seriesIndex + 1,
                  })
                }
              >
                ↓
              </button>
              <button
                type="button"
                aria-label={`Delete chart series ${seriesIndex + 1}`}
                disabled={controlsDisabled || current.series.length === 1}
                className="rounded border border-red-800 px-2 text-xs text-red-300 disabled:opacity-30"
                onClick={() => append({ kind: "remove-series", seriesIndex })}
              >
                Delete
              </button>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-1">
              {series.values.map((value, categoryIndex) => (
                <label
                  key={categoryIndex}
                  className="text-[10px] text-slate-400"
                >
                  {current.categories[categoryIndex]}
                  <SeriesValueInput
                    label={`Chart copilot series ${seriesIndex + 1} value ${categoryIndex + 1}`}
                    value={value}
                    disabled={controlsDisabled}
                    onCommit={(nextValue) => {
                      append({
                        kind: "set-series-value",
                        seriesIndex,
                        categoryIndex,
                        value: nextValue,
                      });
                    }}
                  />
                </label>
              ))}
            </div>
          </div>
        ))}
        <button
          type="button"
          className="w-full rounded border border-cyan-700 px-2 py-2 text-xs disabled:opacity-30"
          disabled={
            controlsDisabled ||
            current.series.length >=
              TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeries ||
            current.chart_type === "pie" ||
            current.chart_type === "donut"
          }
          onClick={() =>
            append({
              kind: "add-series",
              series: {
                name: `Series ${current.series.length + 1}`,
                values: current.categories.map(() => 0),
              },
            })
          }
        >
          Add series
        </button>
      </fieldset>

      <fieldset
        className="mt-4 rounded-lg border border-slate-800 p-3"
        disabled={controlsDisabled}
      >
        <legend className="px-1 text-xs font-medium text-slate-200">
          Bounded CSV / TSV import
        </legend>
        <select
          aria-label="Chart copilot import format"
          className="mt-1 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs"
          value={importFormat}
          onChange={(event) =>
            setImportFormat(event.target.value as "csv" | "tsv")
          }
        >
          <option value="csv">CSV</option>
          <option value="tsv">TSV</option>
        </select>
        <textarea
          aria-label="Chart copilot import data"
          className="mt-2 min-h-24 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs"
          value={importText}
          maxLength={TEMPLATE_V2_CHART_COPILOT_LIMITS.maxInputBytes}
          placeholder={"Category,Revenue\nQ1,10\nQ2,12"}
          onChange={(event) => setImportText(event.target.value)}
        />
        <button
          type="button"
          className="mt-2 w-full rounded border border-slate-600 px-2 py-2 text-xs disabled:opacity-30"
          disabled={!importText}
          onClick={importData}
        >
          Validate and stage import
        </button>
      </fieldset>

      <div className="mt-4 grid grid-cols-2 gap-2">
        <button
          type="button"
          className="rounded border border-violet-600 px-3 py-2 text-xs"
          disabled={controlsDisabled}
          onClick={useRecommendation}
        >
          Stage deterministic recommendation
        </button>
        <button
          type="button"
          className="rounded bg-cyan-600 px-3 py-2 text-xs font-semibold disabled:opacity-30"
          disabled={disabled || review !== null || operations.length === 0}
          onClick={previewChanges}
        >
          Preview draft
        </button>
      </div>

      {error ? (
        <p role="alert" className="mt-3 break-words text-xs text-red-300">
          {error}
        </p>
      ) : null}
      {status ? (
        <p role="status" className="mt-3 text-xs text-emerald-300">
          {status}
        </p>
      ) : null}

      {review ? (
        <div
          aria-label="Chart copilot preview"
          className="mt-4 rounded-lg border border-cyan-600/50 bg-slate-950 p-3"
        >
          <p className="text-xs font-semibold text-cyan-100">
            Patch preview · {review.diff.length} changed field
            {review.diff.length === 1 ? "" : "s"}
          </p>
          <ul className="mt-2 space-y-1">
            {review.diff.map((item) => (
              <li key={item.path} className="text-[11px] text-slate-300">
                <strong>{item.path}</strong>: {formatDiffValue(item.before)} →{" "}
                {formatDiffValue(item.after)}
              </li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              className="flex-1 rounded bg-emerald-600 px-3 py-2 text-xs font-semibold disabled:opacity-30"
              disabled={disabled || review.diff.length === 0}
              onClick={apply}
            >
              Apply chart patch
            </button>
            <button
              type="button"
              className="rounded border border-slate-600 px-3 py-2 text-xs"
              onClick={cancel}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : operations.length ? (
        <button
          type="button"
          className="mt-2 w-full rounded border border-slate-700 px-3 py-2 text-xs"
          onClick={cancel}
        >
          Cancel draft
        </button>
      ) : null}
    </section>
  );
}
