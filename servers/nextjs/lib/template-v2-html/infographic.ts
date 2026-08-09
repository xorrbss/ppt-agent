import { DEFAULT_CHART_COLORS, normalizeChartColor } from "./chart-colors";
import { cssNumber, escapeAttribute, escapeCssColor, escapeHtml } from "./css";
import { frameStyle, readBox, transformStyle } from "./style";
import type { InfographicKind, JsonRecord, RenderMode } from "./types";
import { clamp, readArray, readNumber, readRecord, readString } from "./values";

interface InfographicMetrics {
  ratio: number;
  label: string;
}

export function renderInfographic(item: JsonRecord, mode: RenderMode): string {
  const data = infographicData(item);
  const kind = infographicKindFromValue(readString(data.type));
  if (kind === "gauge") return renderGaugeInfographic(item, mode);
  return renderProgressBarInfographic(item, mode);
}

function renderProgressBarInfographic(item: JsonRecord, mode: RenderMode): string {
  const metrics = infographicMetrics(item);
  const highlightColor = infographicHighlightColor(item);
  const baseColor = infographicBaseColor(item);
  const fallbackSize = { width: 180, height: 40 };
  const box = readBox(item, fallbackSize);
  const showLabel = (box.height ?? fallbackSize.height) >= 28;
  const label = showLabel
    ? `<div style="color:#111827;font-size:${cssNumber(
      Math.max(
        10,
        Math.min(16, Math.round((box.height ?? fallbackSize.height) * 0.3))
      )
    )}px;font-weight:700;line-height:1;text-align:right">${escapeHtml(
      metrics.label
    )}</div>`
    : "";

  return `<div style="${frameStyle(item, mode, fallbackSize)}${transformStyle(
    item
  )}display:flex;flex-direction:column;gap:6px;justify-content:center;overflow:hidden"><div style="position:relative;width:100%;height:${cssNumber(
    Math.max(
      6,
      Math.min(18, Math.round((box.height ?? fallbackSize.height) * 0.35))
    )
  )}px;border-radius:999px;background:${escapeCssColor(
    baseColor
  )};overflow:hidden"><div style="height:100%;width:${cssNumber(
    metrics.ratio * 100
  )}%;border-radius:inherit;background:${escapeCssColor(highlightColor)}"></div></div>${label}</div>`;
}

function renderGaugeInfographic(item: JsonRecord, mode: RenderMode): string {
  const metrics = infographicMetrics(item);
  const highlightColor = infographicHighlightColor(item);
  const baseColor = infographicBaseColor(item);
  const fallbackSize = { width: 160, height: 96 };
  const progressPath =
    metrics.ratio > 0
      ? `<path d="${escapeAttribute(
        describeGaugeArc(60, 60, 48, metrics.ratio)
      )}" fill="none" stroke="${escapeAttribute(
        escapeCssColor(highlightColor)
      )}" stroke-width="12" stroke-linecap="round"/>`
      : "";

  return `<div style="${frameStyle(item, mode, fallbackSize)}${transformStyle(
    item
  )}overflow:hidden"><svg width="100%" height="100%" viewBox="0 0 120 72" preserveAspectRatio="xMidYMid meet" style="display:block"><path d="M 12 60 A 48 48 0 0 1 108 60" fill="none" stroke="${escapeAttribute(
    escapeCssColor(baseColor)
  )}" stroke-width="12" stroke-linecap="round"/>${progressPath}<text x="60" y="52" text-anchor="middle" fill="#111827" font-family="Arial, Helvetica, sans-serif" font-size="18" font-weight="700">${escapeHtml(
    metrics.label
  )}</text></svg></div>`;
}

function infographicKindFromValue(value: string | null): InfographicKind {
  return value === "gauge" ? "gauge" : "progress_bar";
}

function infographicData(item: JsonRecord): JsonRecord {
  return readRecord(item.data);
}

function infographicMetrics(item: JsonRecord): InfographicMetrics {
  const data = infographicData(item);
  const rawMin = readNumber(data.min_value) ?? 0;
  const rawMax = readNumber(data.max_value) ?? 100;
  const min = Math.min(rawMin, rawMax);
  const max = Math.max(rawMin, rawMax);
  const value = clamp(readNumber(data.value) ?? min, min, max);
  const ratio = max === min ? 0 : (value - min) / (max - min);

  return {
    ratio,
    label: formatInfographicNumber(value),
  };
}

function infographicHighlightColor(item: JsonRecord): string {
  const colors = readArray(item.colors);
  return (
    normalizeChartColor(readString(colors[1])) ??
    DEFAULT_CHART_COLORS[0]
  );
}

function infographicBaseColor(item: JsonRecord): string {
  const colors = readArray(item.colors);
  return (
    normalizeChartColor(readString(colors[0])) ??
    "#E5E7EB"
  );
}

function describeGaugeArc(
  centerX: number,
  centerY: number,
  radius: number,
  ratio: number
): string {
  const start = gaugePoint(centerX, centerY, radius, 180);
  const end = gaugePoint(
    centerX,
    centerY,
    radius,
    180 + clamp(ratio, 0, 1) * 180
  );
  return `M ${cssNumber(start.x)} ${cssNumber(start.y)} A ${cssNumber(
    radius
  )} ${cssNumber(radius)} 0 0 1 ${cssNumber(end.x)} ${cssNumber(end.y)}`;
}

function gaugePoint(
  centerX: number,
  centerY: number,
  radius: number,
  angleDegrees: number
): { x: number; y: number } {
  const radians = (angleDegrees * Math.PI) / 180;
  return {
    x: centerX + radius * Math.cos(radians),
    y: centerY + radius * Math.sin(radians),
  };
}

function formatInfographicNumber(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  return Object.is(rounded, -0) ? "0" : String(rounded);
}
