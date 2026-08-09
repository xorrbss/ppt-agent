import { CHART_FONT_FAMILY, withAlpha } from "./chart-colors";
import {
  isHorizontalChart,
  isPieLikeChart,
  isStackedChart,
} from "./chart-kinds";
import type { ChartKind, JsonRecord } from "./types";

export function chartScales({
  axisColor,
  chartKind,
  fontSize,
  gridColor,
  xAxis,
  xAxisGrid,
  xAxisTitle,
  yAxis,
  yAxisGrid,
  yAxisTitle,
}: {
  axisColor: string;
  chartKind: ChartKind;
  fontSize: number;
  gridColor: string;
  xAxis: boolean;
  xAxisGrid: boolean;
  xAxisTitle: string;
  yAxis: boolean;
  yAxisGrid: boolean;
  yAxisTitle: string;
}): JsonRecord | undefined {
  if (isPieLikeChart(chartKind) || chartKind === "polar_area") return undefined;

  if (chartKind === "radar") {
    return {
      r: {
        angleLines: {
          color: withAlpha(gridColor, xAxisGrid ? 0.35 : 0),
          display: xAxisGrid,
        },
        beginAtZero: true,
        grid: {
          color: withAlpha(gridColor, yAxisGrid ? 0.35 : 0),
          display: yAxisGrid,
        },
        pointLabels: {
          color: axisColor,
          display: xAxis,
          font: { family: CHART_FONT_FAMILY, size: fontSize, weight: 600 },
        },
        ticks: {
          backdropColor: "transparent",
          color: axisColor,
          display: yAxis,
          font: {
            family: CHART_FONT_FAMILY,
            size: Math.max(8, fontSize - 1),
          },
          presentonFormat: true,
        },
      },
    };
  }

  const horizontal = isHorizontalChart(chartKind);
  const stacked = isStackedChart(chartKind);
  const showCategoryGrid = horizontal ? xAxisGrid : yAxisGrid;
  const showLinearGrid = horizontal ? yAxisGrid : xAxisGrid;
  const showCategoryAxis = horizontal ? yAxis : xAxis;
  const showLinearAxis = horizontal ? xAxis : yAxis;
  const categoryAxis = {
    display: showCategoryAxis || showCategoryGrid,
    border: { color: axisColor, display: showCategoryAxis },
    grid: {
      color: withAlpha(gridColor, showCategoryGrid ? 0.25 : 0),
      display: showCategoryGrid,
      drawTicks: showCategoryAxis,
    },
    stacked,
    ticks: {
      color: axisColor,
      display: showCategoryAxis,
      font: { family: CHART_FONT_FAMILY, size: fontSize, weight: 600 },
      maxRotation: 0,
      autoSkip: true,
    },
    title: {
      color: axisColor,
      display: showCategoryAxis && Boolean(horizontal ? yAxisTitle : xAxisTitle),
      font: { family: CHART_FONT_FAMILY, size: fontSize, weight: 700 },
      text: horizontal ? yAxisTitle : xAxisTitle,
    },
    type: "category",
  };
  const linearAxis = {
    beginAtZero: true,
    display: showLinearAxis || showLinearGrid,
    border: { color: axisColor, display: showLinearAxis },
    grace: "8%",
    grid: {
      color: withAlpha(gridColor, showLinearGrid ? 0.35 : 0),
      display: showLinearGrid,
      drawTicks: showLinearAxis,
    },
    stacked,
    ticks: {
      color: axisColor,
      display: showLinearAxis,
      font: {
        family: CHART_FONT_FAMILY,
        size: Math.max(8, fontSize - 2),
        weight: 600,
      },
      presentonFormat: true,
    },
    title: {
      color: axisColor,
      display: showLinearAxis && Boolean(horizontal ? xAxisTitle : yAxisTitle),
      font: { family: CHART_FONT_FAMILY, size: fontSize, weight: 700 },
      text: horizontal ? xAxisTitle : yAxisTitle,
    },
    type: "linear",
  };

  if (chartKind === "scatter" || chartKind === "bubble") {
    return {
      x: {
        ...linearAxis,
        display: xAxis || yAxisGrid,
        border: { ...linearAxis.border, display: xAxis },
        grid: {
          color: withAlpha(gridColor, yAxisGrid ? 0.35 : 0),
          display: yAxisGrid,
          drawTicks: xAxis,
        },
        ticks: { ...linearAxis.ticks, display: xAxis },
        title: {
          ...linearAxis.title,
          display: xAxis && Boolean(xAxisTitle),
          text: xAxisTitle,
        },
      },
      y: {
        ...linearAxis,
        display: yAxis || xAxisGrid,
        border: { ...linearAxis.border, display: yAxis },
        grid: {
          color: withAlpha(gridColor, xAxisGrid ? 0.35 : 0),
          display: xAxisGrid,
          drawTicks: yAxis,
        },
        ticks: { ...linearAxis.ticks, display: yAxis },
        title: {
          ...linearAxis.title,
          display: yAxis && Boolean(yAxisTitle),
          text: yAxisTitle,
        },
      },
    };
  }

  return horizontal ? { x: linearAxis, y: categoryAxis } : { x: categoryAxis, y: linearAxis };
}
