import type { ChartKind } from "./types";

function normalizeChartKindValue(value: string | null): string {
  if (!value) return "";
  return value
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replace(/[\s-]+/g, "_")
    .replace(/_+/g, "_");
}

export function chartKindFromValue(value: string | null): ChartKind {
  const normalized = normalizeChartKindValue(value);
  if (normalized === "bubble") return "bubble";
  if (normalized === "horizontal_bar" || normalized === "bar_horizontal") {
    return "horizontal_bar";
  }
  if (
    normalized === "horizontal_stacked_bar" ||
    normalized === "stacked_horizontal_bar"
  ) {
    return "horizontal_stacked_bar";
  }
  if (
    normalized === "stacked_bar" ||
    normalized === "bar_stacked" ||
    normalized === "stacked" ||
    normalized === "stacked_column"
  ) {
    return "stacked_bar";
  }
  if (normalized === "line") return "line";
  if (normalized === "area") return "area";
  if (normalized === "pie") return "pie";
  if (normalized === "donut" || normalized === "doughnut") return "donut";
  if (normalized === "polar" || normalized === "polar_area") return "polar_area";
  if (normalized === "radar") return "radar";
  if (normalized === "scatter") return "scatter";
  return "bar";
}

export function chartJsType(chartKind: ChartKind): string {
  if (chartKind === "donut") return "doughnut";
  if (chartKind === "area") return "line";
  if (chartKind === "polar_area") return "polarArea";
  if (
    chartKind === "horizontal_bar" ||
    chartKind === "stacked_bar" ||
    chartKind === "horizontal_stacked_bar"
  ) {
    return "bar";
  }
  return chartKind;
}

export function isPieLikeChart(chartKind: ChartKind): boolean {
  return chartKind === "pie" || chartKind === "donut";
}

export function isBarChart(chartKind: ChartKind): boolean {
  return (
    chartKind === "bar" ||
    chartKind === "horizontal_bar" ||
    chartKind === "stacked_bar" ||
    chartKind === "horizontal_stacked_bar"
  );
}

export function isHorizontalChart(chartKind: ChartKind): boolean {
  return (
    chartKind === "horizontal_bar" || chartKind === "horizontal_stacked_bar"
  );
}

export function isStackedChart(chartKind: ChartKind): boolean {
  return chartKind === "stacked_bar" || chartKind === "horizontal_stacked_bar";
}
