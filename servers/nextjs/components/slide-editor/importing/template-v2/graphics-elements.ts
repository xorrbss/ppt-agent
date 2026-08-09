/**
 * Stage 3 (map to schema) — the data-driven graphics: vectors, charts and
 * infographics. Split out of `elements.ts` because their coercion rules are
 * self-contained and considerably denser than the box-model elements.
 */
import { chartDataFromSeriesWithColors } from "@/components/slide-editor/charts/chart-data";
import type {
  ChartSeries,
  SlideElement,
  VectorCurve,
} from "@/components/slide-editor/types";
import {
  asRecord,
  clampOptional,
  clampOptionalInteger,
  readArray,
  readBoolean,
  readColor,
  readDataLabelPosition,
  readEnum,
  readNumber,
  readRawNumber,
  readRecord,
  readString,
  readValue,
  round,
  truncateString,
  type AdaptedPosition,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";
import { baseElement } from "@/components/slide-editor/importing/template-v2/element-base";
import {
  adaptFill,
  adaptShadow,
  adaptStroke,
  hasVisiblePaint,
} from "@/components/slide-editor/importing/template-v2/styles";
import {
  adaptChartCategories,
  adaptChartSeries,
} from "@/components/slide-editor/importing/template-v2/content";
import { TEMPLATE_V2_CHART_TYPES } from "../../../../lib/template-v2-html/template-v2-contract.generated.ts";

export function adaptVector(raw: UnknownRecord): SlideElement | null {
  const base = baseElement(raw);
  const fill = adaptFill(readRecord(raw, "fill"));
  const stroke = adaptStroke(readRecord(raw, "stroke"));
  const points = adaptVectorPoints(raw);
  if (!hasVisiblePaint(fill, stroke, base.opacity)) return null;
  if (points.length < 2) return null;

  const { position, size, decorative, ...pointBase } = base;
  void position;
  void size;
  void decorative;

  const knownFields = new Set([
    "type",
    "name",
    "position",
    "size",
    "decorative",
    "rotation",
    "opacity",
    "shadow",
    "component_id",
    "component_instance_id",
    "component_description",
    "component_slot",
    "design_variables",
    "layout",
    "shape",
    "points",
    "closed",
    "corner_radii",
    "cornerRadii",
    "curve",
    "fill",
    "stroke",
  ]);
  const unknownFields = Object.fromEntries(
    Object.entries(raw).filter(([key]) => !knownFields.has(key)),
  );

  return {
    ...unknownFields,
    ...pointBase,
    type: "vector",
    shape: readEnum(raw, ["polygon", "ellipse"], "shape") ?? undefined,
    points,
    closed: adaptVectorClosed(raw, points),
    corner_radii: adaptCornerRadii(raw),
    curve: adaptVectorCurve(raw) ?? adaptImplicitVectorCurve(raw),
    fill,
    stroke,
    shadow: adaptShadow(readRecord(raw, "shadow")),
  };
}

function adaptVectorPoints(raw: UnknownRecord): AdaptedPosition[] {
  return readArray(raw, "points")
    .map((value) => {
      const point = asRecord(value);
      if (!point) return null;
      const x = readRawNumber(point.x);
      const y = readRawNumber(point.y);
      return x != null && y != null
        ? { ...point, x: round(x), y: round(y) }
        : null;
    })
    .filter((point): point is AdaptedPosition => point != null);
}

function adaptVectorClosed(
  raw: UnknownRecord,
  points: AdaptedPosition[],
): boolean {
  const closed = readValue(raw, "closed");
  if (typeof closed === "boolean") return closed;
  if (typeof closed === "string") {
    const normalized = closed.trim().toLowerCase();
    if (normalized === "false" || normalized === "0") return false;
    if (normalized === "true" || normalized === "1") return true;
  }
  return points.length > 2;
}

function adaptVectorCurve(raw: UnknownRecord): VectorCurve | null {
  const curve = readRecord(raw, "curve");
  if (!curve) return null;
  const rawType = readString(curve.type)?.trim().toLowerCase();
  if (rawType !== "smooth") return null;
  return {
    ...curve,
    type: "smooth",
    tension: clampOptional(readRawNumber(curve.tension), 0, 1),
    segments: clampOptionalInteger(readRawNumber(curve.segments), 1, 96),
  };
}

function adaptImplicitVectorCurve(raw: UnknownRecord): VectorCurve | null {
  void raw;
  return null;
}

function adaptCornerRadii(raw: UnknownRecord): number[] | null {
  const rawRadii = raw.corner_radii ?? raw.cornerRadii;
  if (!Array.isArray(rawRadii)) return null;
  return rawRadii
    .map(readRawNumber)
    .filter((value): value is number => value != null)
    .map((value) => Math.max(0, value));
}

export function adaptChart(raw: UnknownRecord): SlideElement {
  const categories = adaptChartCategories(readArray(raw, "categories"));
  const series = readArray(raw, "series")
    .map(adaptChartSeries)
    .filter((item): item is ChartSeries => item != null);
  const chartType =
    readEnum(raw, TEMPLATE_V2_CHART_TYPES, "chart_type") ??
    "bar";
  const supportedSeries =
    chartType === "pie" || chartType === "donut"
      ? series.slice(0, 1)
      : series;
  const colors = readArray(raw, "colors")
    .map(readColor)
    .filter((item): item is string => Boolean(item))
    .slice(0, 12);
  const color = colors[0] ?? null;
  const data = chartDataFromSeriesWithColors(
    categories,
    supportedSeries,
    colors,
    supportedSeries.length <= 1,
  ).slice(0, 8);
  const dataLabels = readDataLabelPosition(
    raw,
    Object.prototype.hasOwnProperty.call(raw, "data_labels")
      ? "data_labels"
      : "dataLabels",
  );

  return {
    ...baseElement(raw),
    type: "chart",
    chart_type: chartType,
    data: data.length > 0 ? data : [{ label: "Data", value: 0 }],
    title: truncateString(readString(raw.title) ?? "", 80) || null,
    title_color: readColor(readValue(raw, "title_color") ?? raw.titleColor),
    legend_color: readColor(readValue(raw, "legend_color") ?? raw.legendColor),
    color,
    axis_color: readColor(readValue(raw, "axis_color")),
    grid_color: readColor(readValue(raw, "grid_color")),
    data_labels: dataLabels,
    legend: readBoolean(raw, "legend"),
    colors,
    x_axis: readBoolean(raw, "x_axis"),
    y_axis: readBoolean(raw, "y_axis"),
    x_axis_grid: readBoolean(raw, "x_axis_grid"),
    y_axis_grid: readBoolean(raw, "y_axis_grid"),
    x_axis_title:
      truncateString(
        readString(readValue(raw, "x_axis_title")) ?? "",
        80,
      ) || null,
    y_axis_title:
      truncateString(
        readString(readValue(raw, "y_axis_title")) ?? "",
        80,
      ) || null,
    categories,
    series: supportedSeries,
    source: truncateString(readString(raw.source) ?? "", 120) || null,
  };
}

export function adaptInfographic(raw: UnknownRecord): SlideElement {
  const data = readRecord(raw, "data") ?? {};
  const minValue =
    readNumber(data, "min_value") ??
    0;
  const rawMaxValue =
    readNumber(data, "max_value") ??
    100;
  const maxValue =
    rawMaxValue === minValue ? minValue + 1 : rawMaxValue;
  const colors = readArray(raw, "colors")
    .map(readColor)
    .filter((color): color is string => Boolean(color));

  return {
    ...baseElement(raw),
    type: "infographic",
    data: {
      type:
        readEnum(data, ["progress_bar", "gauge"], "type") ??
        "gauge",
      min_value: minValue,
      max_value: maxValue,
      value: readNumber(data, "value") ?? minValue,
    },
    colors,
  };
}
