"use client";

import { useState } from "react";
import {
  TEMPLATE_V2_CHART_TYPES,
  TEMPLATE_V2_CHART_TYPE_LABELS,
} from "@/components/slide-editor/charts/chart-data";
import type { TemplateV2ToolbarRecord } from "@/lib/template-v2-toolbar";
import {
  buildChartContractEditPatches,
  chartContractCapabilities,
  type ChartContractEdit,
} from "@/lib/template-v2-chart-table-contract";
import {
  ToolbarButton,
  ToolbarField,
  ToolbarSelect,
  toolbarInputClass,
} from "./ToolbarPrimitives";
import type { TemplateV2ToolbarPatch } from "./types";

const numberValue = (value: unknown, fallback: number) =>
  typeof value === "number" && Number.isFinite(value) ? value : fallback;

export default function ChartToolbar({
  element,
  onPatch,
}: {
  element: TemplateV2ToolbarRecord;
  onPatch: (
    patches: TemplateV2ToolbarPatch[],
    options?: { historyKey?: string; continuous?: boolean }
  ) => void;
}) {
  const [categoryIndex, setCategoryIndex] = useState(0);
  const [seriesIndex, setSeriesIndex] = useState(0);
  const capabilities = chartContractCapabilities(element.chart_type);
  const supportsAxes = capabilities.ok && capabilities.value.axisOptions;
  const categories = Array.isArray(element.categories)
    ? element.categories.filter((value): value is string => typeof value === "string")
    : [];
  const series = Array.isArray(element.series)
    ? element.series.filter(
        (value): value is TemplateV2ToolbarRecord =>
          value !== null && typeof value === "object" && !Array.isArray(value)
      )
    : [];
  const safeCategoryIndex = Math.min(categoryIndex, Math.max(0, categories.length - 1));
  const safeSeriesIndex = Math.min(seriesIndex, Math.max(0, series.length - 1));
  const selectedSeries = series[safeSeriesIndex];
  const selectedValues = Array.isArray(selectedSeries?.values)
    ? selectedSeries.values
    : [];

  function applyContractEdit(
    edit: ChartContractEdit,
    historyKey: string,
    continuous = false
  ) {
    const result = buildChartContractEditPatches(element, edit);
    if (result.ok && result.value.length > 0) {
      onPatch(result.value, { historyKey, continuous });
    }
  }

  const patchColor = (key: "title_color" | "legend_color" | "axis_color" | "grid_color", value: string) =>
    onPatch([{ path: [key], value }], {
      historyKey: `toolbar:chart-${key}`,
      continuous: true,
    });

  return (
    <div className="flex items-center gap-2">
      <ToolbarField label="Chart type">
        <span aria-hidden="true">Type</span>
        <ToolbarSelect
          aria-label="Chart type"
          value={typeof element.chart_type === "string" ? element.chart_type : ""}
          onChange={(event) =>
            applyContractEdit(
              { kind: "chart-type", value: event.target.value },
              "toolbar:chart-type"
            )
          }
        >
          {TEMPLATE_V2_CHART_TYPES.map((chartType) => (
            <option
              key={chartType}
              value={chartType}
              disabled={
                !buildChartContractEditPatches(element, {
                  kind: "chart-type",
                  value: chartType,
                }).ok
              }
            >
              {TEMPLATE_V2_CHART_TYPE_LABELS[chartType]}
            </option>
          ))}
        </ToolbarSelect>
      </ToolbarField>
      {categories.length > 0 && (
        <>
          <ToolbarField label="Chart category">
            <span aria-hidden="true">Category</span>
            <ToolbarSelect
              aria-label="Chart category"
              value={safeCategoryIndex}
              onChange={(event) => setCategoryIndex(Number(event.target.value))}
            >
              {categories.map((label, index) => (
                <option key={index} value={index}>
                  {index + 1}: {label || "Untitled"}
                </option>
              ))}
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label="Chart category label">
            <input
              className={`${toolbarInputClass} w-28`}
              aria-label="Chart category label"
              type="text"
              value={categories[safeCategoryIndex]}
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "category-label",
                    categoryIndex: safeCategoryIndex,
                    value: event.target.value,
                  },
                  `toolbar:chart-category-${safeCategoryIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
        </>
      )}
      {series.length > 0 && (
        <>
          <ToolbarField label="Chart series">
            <span aria-hidden="true">Series</span>
            <ToolbarSelect
              aria-label="Chart series"
              value={safeSeriesIndex}
              onChange={(event) => setSeriesIndex(Number(event.target.value))}
            >
              {series.map((item, index) => (
                <option key={index} value={index}>
                  {index + 1}: {typeof item.name === "string" && item.name ? item.name : "Untitled"}
                </option>
              ))}
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label="Chart series name">
            <input
              className={`${toolbarInputClass} w-28`}
              aria-label="Chart series name"
              type="text"
              value={typeof selectedSeries?.name === "string" ? selectedSeries.name : ""}
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "series-name",
                    seriesIndex: safeSeriesIndex,
                    value: event.target.value,
                  },
                  `toolbar:chart-series-name-${safeSeriesIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
        </>
      )}
      {selectedValues.length > safeCategoryIndex &&
        typeof selectedValues[safeCategoryIndex] === "number" && (
          <ToolbarField label="Chart series value">
            <span aria-hidden="true">Value</span>
            <input
              className={`${toolbarInputClass} w-20`}
              aria-label="Chart series value"
              type="number"
              step="any"
              value={selectedValues[safeCategoryIndex] as number}
              onChange={(event) =>
                applyContractEdit(
                  {
                    kind: "series-value",
                    seriesIndex: safeSeriesIndex,
                    categoryIndex: safeCategoryIndex,
                    value: event.target.valueAsNumber,
                  },
                  `toolbar:chart-series-value-${safeSeriesIndex}-${safeCategoryIndex}`,
                  true
                )
              }
            />
          </ToolbarField>
        )}
      <ToolbarField label="Chart title color">
        <span aria-hidden="true">Title</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Chart title color"
          type="text"
          value={typeof element.title_color === "string" ? element.title_color : ""}
          placeholder="#111827"
          onChange={(event) => patchColor("title_color", event.target.value)}
        />
      </ToolbarField>
      <ToolbarButton
        aria-label="Toggle chart legend"
        aria-pressed={element.legend === true}
        onClick={() =>
          onPatch([{ path: ["legend"], value: element.legend !== true }], {
            historyKey: "toolbar:chart-legend",
          })
        }
      >
        Legend
      </ToolbarButton>
      <ToolbarField label="Chart legend color">
        <span aria-hidden="true">Legend</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label="Chart legend color"
          type="text"
          value={typeof element.legend_color === "string" ? element.legend_color : ""}
          placeholder="#374151"
          onChange={(event) => patchColor("legend_color", event.target.value)}
        />
      </ToolbarField>
      {supportsAxes && (
        <>
          <ToolbarField label="Chart axis color">
            <span aria-hidden="true">Axis</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label="Chart axis color"
              type="text"
              value={typeof element.axis_color === "string" ? element.axis_color : ""}
              placeholder="#6b7280"
              onChange={(event) => patchColor("axis_color", event.target.value)}
            />
          </ToolbarField>
          <ToolbarField label="Chart grid color">
            <span aria-hidden="true">Grid</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label="Chart grid color"
              type="text"
              value={typeof element.grid_color === "string" ? element.grid_color : ""}
              placeholder="#e5e7eb"
              onChange={(event) => patchColor("grid_color", event.target.value)}
            />
          </ToolbarField>
        </>
      )}
      <ToolbarField label="Chart opacity">
        <span aria-hidden="true">Opacity</span>
        <input
          className="w-20 accent-violet-400"
          aria-label="Chart opacity"
          type="range"
          min={0}
          max={1}
          step={0.01}
          value={numberValue(element.opacity, 1)}
          onChange={(event) =>
            onPatch([{ path: ["opacity"], value: Number(event.target.value) }], {
              historyKey: "toolbar:chart-opacity",
              continuous: true,
            })
          }
        />
      </ToolbarField>
    </div>
  );
}
