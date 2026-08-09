import {
  categoryColors,
  DEFAULT_CHART_COLORS,
  emptyChartSeries,
  normalizeChartColor,
  readColorArray,
  seriesColor,
  withAlpha,
} from "./chart-colors";
import {
  isBarChart,
  isHorizontalChart,
  isPieLikeChart,
  isStackedChart,
} from "./chart-kinds";
import type {
  ChartKind,
  ChartPointData,
  JsonRecord,
  NormalizedChartData,
} from "./types";
import {
  readArray,
  readNumber,
  readRecord,
  readString,
  readStringValue,
} from "./values";

export function chartDatasets(chartKind: ChartKind, data: NormalizedChartData): JsonRecord[] {
  if (chartKind === "pie" || chartKind === "donut") {
    const series = data.series[0];
    if (!series) return [];
    return [
      {
        label: series.name,
        data: series.values.map((value) => Math.max(0, value)),
        backgroundColor: series.values.map(
          (_, index) =>
            data.colors[index % data.colors.length] ??
            DEFAULT_CHART_COLORS[index % DEFAULT_CHART_COLORS.length]
        ),
        borderColor: "#FFFFFF",
        borderWidth: 1,
        hoverOffset: 0,
      },
    ];
  }

  if (chartKind === "polar_area") {
    const series = data.series.length ? data.series : [emptyChartSeries()];
    return series.map((seriesItem) => {
      const colors =
        data.series.length === 1
          ? categoryColors(seriesItem, data.colors)
          : seriesItem.values.map(() => seriesColor(seriesItem, data));
      return {
        label: seriesItem.name,
        data: seriesItem.values,
        backgroundColor: colors.map((color) => withAlpha(color, 0.78)),
        borderColor: colors,
        borderWidth: 1,
      };
    });
  }

  if (chartKind === "scatter" || chartKind === "bubble") {
    return data.series.map((seriesItem) => {
      const colors =
        data.series.length === 1
          ? categoryColors(seriesItem, data.colors)
          : [seriesColor(seriesItem, data)];
      return {
        label: seriesItem.name,
        data:
          chartKind === "bubble"
            ? seriesItem.points.map((point) => ({ ...point, r: point.r ?? 6 }))
            : seriesItem.points.map(({ x, y }) => ({ x, y })),
        backgroundColor: colors.map((color) => withAlpha(color, 0.78)),
        borderColor: colors,
        borderWidth: 2,
        pointRadius: chartKind === "scatter" ? 4 : undefined,
        pointHoverRadius: 4,
      };
    });
  }

  const lineLike = chartKind === "line" || chartKind === "area";

  return data.series.map((series, index) => {
    const color = seriesColor(series, data, index);
    const perCategoryColors =
      data.series.length === 1 && data.colors.length
        ? categoryColors(series, data.colors)
        : null;
    const barChart = isBarChart(chartKind);
    const stackedBarChart = isStackedChart(chartKind);
    const dataset: JsonRecord = {
      label: series.name,
      data: series.values,
      backgroundColor:
        chartKind === "area"
          ? withAlpha(color, 0.24)
          : lineLike
            ? color
            : perCategoryColors ?? color,
      borderColor: color,
      borderWidth: lineLike ? 3 : 0,
      borderRadius: barChart && stackedBarChart ? 7 : undefined,
      borderSkipped: barChart ? (stackedBarChart ? "start" : false) : undefined,
      fill: chartKind === "area",
      maxBarThickness: 62,
      presentonBarRadius:
        barChart && !stackedBarChart
          ? { horizontal: isHorizontalChart(chartKind), radius: 7 }
          : undefined,
      pointBackgroundColor: perCategoryColors ?? color,
      pointBorderColor: "#FFFFFF",
      pointBorderWidth: lineLike ? 1.5 : 0,
      pointRadius: lineLike ? 3.5 : 0,
      tension: lineLike ? 0.35 : 0,
    };

    return dataset;
  });
}

export function normalizeChartData(
  item: JsonRecord,
  chartKind: ChartKind
): NormalizedChartData {
  const points = readArray(item.data)
    .map(readRecord)
    .map((point, index) => {
      const value = chartValue(point);
      return {
        label: readString(point.label) ?? `Value ${index + 1}`,
        value,
        point: chartPoint(point, index),
        color: normalizeChartColor(readString(point.color)),
      };
    })
    .filter(
      (
        point
      ): point is {
        label: string;
        value: number;
        point: ChartPointData;
        color: string | null;
      } =>
        Boolean(point)
    );
  const series = readArray(item.series)
    .map(readRecord)
    .map((series, index) => {
      const rawValues = readArray(series.values ?? series.data);
      const values = rawValues.map(chartValue);
      return {
        name: readString(series.name) ?? `Series ${index + 1}`,
        points: rawValues.map((value, valueIndex) =>
          chartPoint(value, valueIndex)
        ),
        values,
      };
    })
    .filter((series) => series.values.length);
  if (!series.length && points.length) {
    series.push({
      name: readString(item.title) ?? "Series 1",
      points: points.map((point) => point.point),
      values: points.map((point) => point.value),
    });
  }
  if (!series.length) {
    series.push(emptyChartSeries());
  }
  if (isPieLikeChart(chartKind) && series.length > 1) {
    series.splice(1);
  }
  const maxLength = Math.min(
    24,
    Math.max(1, ...series.map((item) => item.values.length), points.length)
  );
  const categoryValues = readArray(item.categories);
  const categories = normalizeCategories(
    categoryValues.length ? categoryValues : points.map((point) => point.label),
    maxLength
  );
  const colors = readColorArray(item.colors);
  const legacySeriesColors = colors.length
    ? []
    : readColorArray(item.seriesColors ?? item.series_colors);
  const pointColors = points
    .map((point) => point.color)
    .filter((color): color is string => Boolean(color));

  return {
    categories,
    colors:
      colors.length > 0
        ? colors
        : legacySeriesColors.length > 0
          ? legacySeriesColors
          : pointColors.length > 0
            ? pointColors
            : [normalizeChartColor(readString(item.color)) ?? DEFAULT_CHART_COLORS[0]],
    series: series.map((item) => ({
      ...item,
      values: padValues(item.values, categories.length),
      points: padPoints(item.points, categories.length, item.values),
    })),
  };
}

function normalizeCategories(values: unknown[], length: number): string[] {
  return Array.from({ length }, (_, index) => {
    const value = values[index];
    return readStringValue(value) || `Value ${index + 1}`;
  });
}

function padValues(values: number[], length: number): number[] {
  return Array.from({ length }, (_, index) => values[index] ?? 0);
}

function padPoints(
  points: ChartPointData[],
  length: number,
  fallbackValues: number[]
): ChartPointData[] {
  return Array.from({ length }, (_, index) => {
    const point = points[index];
    if (point) return point;
    return { x: index + 1, y: fallbackValues[index] ?? 0 };
  });
}

function chartValue(value: unknown): number {
  const direct = readNumber(value);
  if (direct != null) return direct;

  const record = readRecord(value);
  return (
    readNumber(record.value) ??
    readNumber(record.y) ??
    readNumber(record.data) ??
    0
  );
}

function chartPoint(value: unknown, index: number): ChartPointData {
  const record = readRecord(value);
  const radius = readNumber(record.r ?? record.radius);
  return {
    x: readNumber(record.x) ?? index + 1,
    y: chartValue(value),
    ...(radius != null ? { r: radius } : {}),
  };
}
