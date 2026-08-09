"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { TEMPLATE_V2_CHART_TYPES } from "@/components/slide-editor/charts/chart-data";
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

const CHART_CATEGORY_LIMIT = 24;
const CHART_SERIES_LIMIT = 12;

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
  const t = useTranslations("studio");
  const [categoryIndex, setCategoryIndex] = useState(0);
  const [seriesIndex, setSeriesIndex] = useState(0);
  const capabilities = chartContractCapabilities(element.chart_type);
  const supportsAxes = capabilities.ok && capabilities.value.axisOptions;
  const dataLabelPositions = capabilities.ok
    ? capabilities.value.dataLabelPositions
    : [];
  const seriesLimit =
    capabilities.ok && !capabilities.value.multipleSeries
      ? 1
      : CHART_SERIES_LIMIT;
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
      <ToolbarField label={t("toolbar.chart.type")}>
        <span aria-hidden="true">{t("toolbar.chart.typeShort")}</span>
        <ToolbarSelect
          aria-label={t("toolbar.chart.type")}
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
              {t(`toolbar.chart.types.${chartType}`)}
            </option>
          ))}
        </ToolbarSelect>
      </ToolbarField>
      {Array.isArray(element.categories) && Array.isArray(element.series) && (
        <>
          <ToolbarField label={t("toolbar.chart.categoryCount")}>
            <span aria-hidden="true">
              {t("toolbar.chart.categoriesCount", {
                count: categories.length,
              })}
            </span>
            <ToolbarButton
              aria-label={t("toolbar.chart.removeCategory")}
              disabled={categories.length === 0}
              onClick={() =>
                applyContractEdit(
                  { kind: "category-count", value: categories.length - 1 },
                  "toolbar:chart-categories"
                )
              }
            >
              -
            </ToolbarButton>
            <ToolbarButton
              aria-label={t("toolbar.chart.addCategory")}
              disabled={categories.length >= CHART_CATEGORY_LIMIT}
              onClick={() =>
                applyContractEdit(
                  { kind: "category-count", value: categories.length + 1 },
                  "toolbar:chart-categories"
                )
              }
            >
              +
            </ToolbarButton>
          </ToolbarField>
          <ToolbarField label={t("toolbar.chart.seriesCount")}>
            <span aria-hidden="true">
              {t("toolbar.chart.seriesCountValue", { count: series.length })}
            </span>
            <ToolbarButton
              aria-label={t("toolbar.chart.removeSeries")}
              disabled={series.length === 0}
              onClick={() =>
                applyContractEdit(
                  { kind: "series-count", value: series.length - 1 },
                  "toolbar:chart-series"
                )
              }
            >
              -
            </ToolbarButton>
            <ToolbarButton
              aria-label={t("toolbar.chart.addSeries")}
              disabled={series.length >= seriesLimit}
              onClick={() =>
                applyContractEdit(
                  { kind: "series-count", value: series.length + 1 },
                  "toolbar:chart-series"
                )
              }
            >
              +
            </ToolbarButton>
          </ToolbarField>
        </>
      )}
      {dataLabelPositions.length > 0 && (
        <ToolbarField label={t("toolbar.chart.dataLabels")}>
          <span aria-hidden="true">{t("toolbar.chart.labels")}</span>
          <ToolbarSelect
            aria-label={t("toolbar.chart.dataLabels")}
            value={
              typeof element.data_labels === "string"
                ? element.data_labels
                : ""
            }
            onChange={(event) =>
              applyContractEdit(
                {
                  kind: "data-labels",
                  value: event.target.value || null,
                },
                "toolbar:chart-data-labels"
              )
            }
          >
            <option value="">{t("toolbar.common.none")}</option>
            {dataLabelPositions.map((position) => (
              <option key={position} value={position}>
                {t(`toolbar.chart.dataLabelPositions.${position}`)}
              </option>
            ))}
          </ToolbarSelect>
        </ToolbarField>
      )}
      {categories.length > 0 && (
        <>
          <ToolbarField label={t("toolbar.chart.category")}>
            <span aria-hidden="true">{t("toolbar.chart.categoryShort")}</span>
            <ToolbarSelect
              aria-label={t("toolbar.chart.category")}
              value={safeCategoryIndex}
              onChange={(event) => setCategoryIndex(Number(event.target.value))}
            >
              {categories.map((label, index) => (
                <option key={index} value={index}>
                  {index + 1}: {label || t("toolbar.common.untitled")}
                </option>
              ))}
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label={t("toolbar.chart.categoryLabel")}>
            <input
              className={`${toolbarInputClass} w-28`}
              aria-label={t("toolbar.chart.categoryLabel")}
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
          <ToolbarField label={t("toolbar.chart.series")}>
            <span aria-hidden="true">{t("toolbar.chart.seriesShort")}</span>
            <ToolbarSelect
              aria-label={t("toolbar.chart.series")}
              value={safeSeriesIndex}
              onChange={(event) => setSeriesIndex(Number(event.target.value))}
            >
              {series.map((item, index) => (
                <option key={index} value={index}>
                  {index + 1}:{" "}
                  {typeof item.name === "string" && item.name
                    ? item.name
                    : t("toolbar.common.untitled")}
                </option>
              ))}
            </ToolbarSelect>
          </ToolbarField>
          <ToolbarField label={t("toolbar.chart.seriesName")}>
            <input
              className={`${toolbarInputClass} w-28`}
              aria-label={t("toolbar.chart.seriesName")}
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
          <ToolbarField label={t("toolbar.chart.seriesValue")}>
            <span aria-hidden="true">{t("toolbar.chart.value")}</span>
            <input
              className={`${toolbarInputClass} w-20`}
              aria-label={t("toolbar.chart.seriesValue")}
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
      <ToolbarField label={t("toolbar.chart.titleColor")}>
        <span aria-hidden="true">{t("toolbar.chart.title")}</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.chart.titleColor")}
          type="text"
          value={typeof element.title_color === "string" ? element.title_color : ""}
          placeholder="#111827"
          onChange={(event) => patchColor("title_color", event.target.value)}
        />
      </ToolbarField>
      <ToolbarButton
        aria-label={t("toolbar.chart.toggleLegend")}
        aria-pressed={element.legend === true}
        onClick={() =>
          onPatch([{ path: ["legend"], value: element.legend !== true }], {
            historyKey: "toolbar:chart-legend",
          })
        }
      >
        {t("toolbar.chart.legend")}
      </ToolbarButton>
      <ToolbarField label={t("toolbar.chart.legendColor")}>
        <span aria-hidden="true">{t("toolbar.chart.legend")}</span>
        <input
          className={`${toolbarInputClass} w-24`}
          aria-label={t("toolbar.chart.legendColor")}
          type="text"
          value={typeof element.legend_color === "string" ? element.legend_color : ""}
          placeholder="#374151"
          onChange={(event) => patchColor("legend_color", event.target.value)}
        />
      </ToolbarField>
      {supportsAxes && (
        <>
          <ToolbarField label={t("toolbar.chart.axisColor")}>
            <span aria-hidden="true">{t("toolbar.chart.axis")}</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label={t("toolbar.chart.axisColor")}
              type="text"
              value={typeof element.axis_color === "string" ? element.axis_color : ""}
              placeholder="#6b7280"
              onChange={(event) => patchColor("axis_color", event.target.value)}
            />
          </ToolbarField>
          <ToolbarField label={t("toolbar.chart.gridColor")}>
            <span aria-hidden="true">{t("toolbar.chart.grid")}</span>
            <input
              className={`${toolbarInputClass} w-24`}
              aria-label={t("toolbar.chart.gridColor")}
              type="text"
              value={typeof element.grid_color === "string" ? element.grid_color : ""}
              placeholder="#e5e7eb"
              onChange={(event) => patchColor("grid_color", event.target.value)}
            />
          </ToolbarField>
        </>
      )}
      <ToolbarField label={t("toolbar.chart.opacity")}>
        <span aria-hidden="true">{t("toolbar.common.opacity")}</span>
        <input
          className="w-20 accent-violet-400"
          aria-label={t("toolbar.chart.opacity")}
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
