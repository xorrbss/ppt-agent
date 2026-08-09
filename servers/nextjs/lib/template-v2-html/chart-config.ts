import { markdownToPlainChartText } from "@/components/slide-editor/charts/chart-data";
import {
  CHART_FONT_FAMILY,
  DEFAULT_CHART_COLORS,
  safeChartColor,
} from "./chart-colors";
import { chartDatasets, normalizeChartData } from "./chart-data";
import {
  chartJsType,
  chartKindFromValue,
  isHorizontalChart,
  isPieLikeChart,
} from "./chart-kinds";
import { chartScales } from "./chart-scales";
import { TEMPLATE_V2_DATA_LABEL_POSITIONS } from "./template-v2-contract.generated";
import type { DataLabelPosition, JsonRecord } from "./types";
import { clamp, readOptionalBoolean, readRecord, readString } from "./values";

const DATA_LABEL_POSITIONS = new Set<string>(TEMPLATE_V2_DATA_LABEL_POSITIONS);

export function chartConfig(item: JsonRecord, height: number): JsonRecord {
  const chartKind = chartKindFromValue(readString(item.chartType ?? item.chart_type));
  const data = normalizeChartData(item, chartKind);
  const primaryColor = safeChartColor(readString(item.color), DEFAULT_CHART_COLORS[0]);
  const colors = data.colors.length > 0 ? data.colors : [primaryColor];
  const axisColor = safeChartColor(
    readString(item.axisColor ?? item.axis_color),
    "#98A2B3"
  );
  const gridColor = safeChartColor(
    readString(item.gridColor ?? item.grid_color),
    axisColor
  );
  const textColor = safeChartColor(
    readString(item.textColor ?? item.text_color ?? item.labelColor ?? item.label_color),
    "#475467"
  );
  const titleColor =
    safeChartColor(readString(item.titleColor ?? item.title_color), "#344054");
  const legendColor = safeChartColor(
    readString(item.legendColor ?? item.legend_color),
    textColor
  );
  const title = markdownToPlainChartText(readString(item.title) ?? "");
  const fontSize = clamp(height * 0.033, 9, 18);
  const titleFontSize = clamp(height * 0.044, 11, 26);
  const valueFontSize = clamp(height * 0.029, 8, 15);
  const autoShowLegend =
    isPieLikeChart(chartKind) ||
    data.series.length > 1 ||
    Boolean(data.series[0]?.name && data.series[0].name !== "Series 1");
  const showLegend = readOptionalBoolean(
    item.legend ?? item.showLegend,
    autoShowLegend
  );
  const dataLabelPosition = readDataLabelPosition(
    Object.prototype.hasOwnProperty.call(item, "data_labels")
      ? item.data_labels
      : item.dataLabels
  );
  const dataLabels = dataLabelPosition != null;
  const xAxisGrid = readOptionalBoolean(
    item.x_axis_grid ?? item.xAxisGrid ?? item.grid,
    true
  );
  const yAxisGrid = readOptionalBoolean(
    item.y_axis_grid ?? item.yAxisGrid ?? item.grid,
    true
  );
  const xAxis = readOptionalBoolean(item.x_axis ?? item.xAxis, true);
  const yAxis = readOptionalBoolean(item.y_axis ?? item.yAxis, true);
  const xAxisTitle = markdownToPlainChartText(
    readString(
      Object.prototype.hasOwnProperty.call(item, "x_axis_title")
        ? item.x_axis_title
        : item.xAxisTitle
    ) ?? ""
  );
  const yAxisTitle = markdownToPlainChartText(
    readString(
      Object.prototype.hasOwnProperty.call(item, "y_axis_title")
        ? item.y_axis_title
        : item.yAxisTitle
    ) ?? ""
  );
  const config: JsonRecord = {
    type: chartJsType(chartKind),
    data: {
      labels: data.categories,
      datasets: chartDatasets(chartKind, { ...data, colors }),
    },
    options: {
      color: textColor,
      font: {
        family: CHART_FONT_FAMILY,
      },
      indexAxis: isHorizontalChart(chartKind) ? "y" : "x",
      layout: {
        padding: isPieLikeChart(chartKind)
          ? { top: 16, right: 20, bottom: 12, left: 20 }
          : { top: 12, right: 22, bottom: 8, left: 12 },
      },
      responsive: false,
      maintainAspectRatio: false,
      animation: false,
      normalized: true,
      plugins: {
        legend: {
          display: showLegend,
          position: "bottom",
          labels: {
            boxWidth: Math.max(8, fontSize * 0.8),
            boxHeight: Math.max(8, fontSize * 0.8),
            color: legendColor,
            font: { family: CHART_FONT_FAMILY, size: fontSize, weight: 600 },
            padding: Math.max(8, fontSize),
            usePointStyle: true,
          },
        },
        title: {
          display: Boolean(title),
          text: title.split(/\r?\n/).filter(Boolean),
          color: titleColor,
          font: {
            family: CHART_FONT_FAMILY,
            size: titleFontSize,
            weight: "700",
          },
          padding: {
            bottom: Math.max(16, titleFontSize * 0.8),
            top: 0,
          },
        },
        tooltip: { enabled: false },
        // Keep the upstream ChartDataLabels contract available in exported
        // documents while the local renderer preserves its collision-aware
        // label placement behavior.
        datalabels: { display: false },
        presentonDataLabels: {
          enabled: dataLabels,
          color: textColor,
          fontFamily: CHART_FONT_FAMILY,
          fontSize: valueFontSize,
          horizontal: isHorizontalChart(chartKind),
          position: dataLabelPosition ?? "top",
        },
      },
    },
  };

  if (chartKind === "donut") {
    readRecord(config.options).cutout = "58%";
  } else if (chartKind === "pie") {
    readRecord(config.options).cutout = "0%";
  } else {
    readRecord(config.options).scales = chartScales({
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
    });
  }

  return config;
}

function readDataLabelPosition(value: unknown): DataLabelPosition | null {
  if (value === true) return "top";
  if (value === false || value == null) return null;
  const text = readString(value);
  return text && DATA_LABEL_POSITIONS.has(text)
    ? (text as DataLabelPosition)
    : null;
}
