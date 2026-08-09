import type { ChartSeriesData, NormalizedChartData } from "./types";
import { readArray, readString } from "./values";

export const DEFAULT_CHART_COLORS = [
  "#7F22FE",
  "#155DFC",
  "#F59E0B",
  "#12B76A",
  "#EF4444",
  "#06B6D4",
  "#8B5CF6",
  "#64748B",
];

export const CHART_FONT_FAMILY = "Inter, Arial, sans-serif";

export function readColorArray(value: unknown): string[] {
  return readArray(value)
    .map((item) => normalizeChartColor(readString(item)))
    .filter((color): color is string => Boolean(color));
}

export function normalizeChartColor(value: string | null): string | null {
  if (!value) return null;
  return safeChartColor(value, DEFAULT_CHART_COLORS[0]);
}

export function emptyChartSeries(): ChartSeriesData {
  return {
    name: "Series 1",
    points: [{ x: 1, y: 0 }],
    values: [0],
  };
}

export function seriesColor(
  series: ChartSeriesData,
  data: NormalizedChartData,
  index = data.series.indexOf(series)
): string {
  return (
    data.colors[index % data.colors.length] ??
    DEFAULT_CHART_COLORS[index % DEFAULT_CHART_COLORS.length]
  );
}

export function categoryColors(series: ChartSeriesData, colors: string[]): string[] {
  return series.values.map(
    (_, index) =>
      colors[index % colors.length] ??
      DEFAULT_CHART_COLORS[index % DEFAULT_CHART_COLORS.length]
  );
}

export function safeChartColor(
  value: string | null | undefined,
  fallback = DEFAULT_CHART_COLORS[0]
): string {
  const color = withHash(value) ?? fallback;
  if (
    /^#[0-9A-Fa-f]{3}$/.test(color) ||
    /^#[0-9A-Fa-f]{6}$/.test(color) ||
    /^rgba?\(/i.test(color)
  ) {
    return color;
  }
  return fallback;
}

function withHash(value: string | null | undefined): string | null {
  if (!value) return null;
  const color = value.trim();
  if (!color) return null;
  return color.startsWith("#") || /^rgba?\(/i.test(color) ? color : `#${color}`;
}

export function withAlpha(color: string, alpha: number): string {
  const normalized = safeChartColor(color);
  const hex = normalized.match(/^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/);
  if (!hex) {
    const rgb = normalized.match(/^rgba?\(([^)]+)\)$/i);
    if (rgb) {
      const channels = rgb[1]
        .split(",")
        .slice(0, 3)
        .map((part) => part.trim());
      return `rgba(${channels.join(", ")}, ${alpha})`;
    }
    return normalized;
  }

  const raw =
    hex[1].length === 3
      ? hex[1]
        .split("")
        .map((char) => char + char)
        .join("")
      : hex[1];
  const int = Number.parseInt(raw, 16);
  return `rgba(${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255}, ${alpha})`;
}
