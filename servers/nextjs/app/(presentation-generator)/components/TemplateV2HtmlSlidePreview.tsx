"use client";

import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Chart from "chart.js/auto";
import type { Chart as ChartInstance, ChartConfiguration, Plugin } from "chart.js";
import {
  hasTemplateV2RenderableUi,
  TEMPLATE_V2_HTML_HEIGHT,
  TEMPLATE_V2_HTML_WIDTH,
  templateV2UiToHtmlFragment,
} from "@/lib/template-v2-json-to-html";

type PresentonDataLabelOptions = {
  enabled?: boolean;
  color?: string;
  fontFamily?: string;
  fontSize?: number;
  horizontal?: boolean;
  position?: DataLabelPosition;
};

type DataLabelPosition = "base" | "mid" | "top" | "outside";

type PresentonChartGlobalState = {
  status: "pending" | "ready" | "error";
  pending: number;
  rendered: number;
  message?: string;
};

type Point = { x: number; y: number };
type Bounds = { left: number; right: number; top: number; bottom: number };

declare global {
  interface Window {
    __PRESENTON_JSON_CHARTS__?: PresentonChartGlobalState;
  }
}

const useChartLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;
let didRegisterPresentonDataLabelPlugin = false;

function registerPresentonDataLabelPlugin(): void {
  if (didRegisterPresentonDataLabelPlugin) return;
  Chart.register(presentonDataLabelPlugin);
  didRegisterPresentonDataLabelPlugin = true;
}

function chartGlobalState(): PresentonChartGlobalState | null {
  if (typeof window === "undefined") return null;

  const existing = window.__PRESENTON_JSON_CHARTS__;
  if (existing) return existing;

  const state: PresentonChartGlobalState = {
    status: "ready",
    pending: 0,
    rendered: 0,
  };
  window.__PRESENTON_JSON_CHARTS__ = state;
  return state;
}

function markChartsPending(count: number): void {
  if (count <= 0) return;
  const state = chartGlobalState();
  if (!state) return;
  state.pending += count;
  state.status = "pending";
  delete state.message;
}

function markChartsReady(count: number): void {
  if (count <= 0) return;
  const state = chartGlobalState();
  if (!state || state.status === "error") return;
  state.pending = Math.max(0, state.pending - count);
  state.rendered += count;
  if (state.pending === 0) {
    state.status = "ready";
  }
}

function markChartsError(message: string): void {
  const state = chartGlobalState();
  if (!state) return;
  state.pending = 0;
  state.status = "error";
  state.message = message;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function readNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function chartValue(raw: unknown): number {
  if (typeof raw === "number") return raw;
  const record = readRecord(raw);
  const value = record.y ?? record.value ?? record.data;
  const numeric = readNumber(value);
  if (numeric != null) return numeric;
  const parsed = readNumber(raw);
  return parsed ?? 0;
}

function formatChartValue(value: number): string {
  if (!Number.isFinite(value)) return "";
  if (
    Math.abs(value) >= 1000 &&
    typeof Intl !== "undefined" &&
    Intl.NumberFormat
  ) {
    return Intl.NumberFormat("en", { notation: "compact" }).format(value);
  }
  return Math.abs(value) % 1 === 0
    ? String(value)
    : String(Math.round(value * 10) / 10).replace(/\.0$/, "");
}

function formatAxisTick(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatChartValue(numeric) : String(value);
}

function hydrateScales(scales: unknown): void {
  const scaleRecords = readRecord(scales);
  Object.values(scaleRecords).forEach((scaleValue) => {
    const scale = readRecord(scaleValue);
    const ticks = readRecord(scale.ticks);
    if (ticks.presentonFormat) {
      ticks.callback = formatAxisTick;
      delete ticks.presentonFormat;
    }

    const radial = readRecord(scale.r);
    const radialTicks = readRecord(radial.ticks);
    if (radialTicks.presentonFormat) {
      radialTicks.callback = formatAxisTick;
      delete radialTicks.presentonFormat;
    }
  });
}

function barBorderRadius(rawValue: unknown, horizontal: boolean, radius = 7) {
  const value = chartValue(rawValue);

  if (horizontal) {
    return value < 0
      ? {
          bottomLeft: radius,
          bottomRight: 0,
          topLeft: radius,
          topRight: 0,
        }
      : {
          bottomLeft: 0,
          bottomRight: radius,
          topLeft: 0,
          topRight: radius,
        };
  }

  return value < 0
    ? {
        bottomLeft: radius,
        bottomRight: radius,
        topLeft: 0,
        topRight: 0,
      }
    : {
        bottomLeft: 0,
        bottomRight: 0,
        topLeft: radius,
        topRight: radius,
      };
}

function hydrateBarBorderRadii(config: ChartConfiguration): void {
  const datasets = Array.isArray(config.data?.datasets)
    ? config.data.datasets
    : [];

  datasets.forEach((dataset) => {
    const record = dataset as {
      borderRadius?: unknown;
      presentonBarRadius?: unknown;
    };
    const options = readRecord(record.presentonBarRadius);
    if (!Object.keys(options).length) return;

    const horizontal = Boolean(options.horizontal);
    const radius = readNumber(options.radius) ?? 7;
    record.borderRadius = (context: { raw?: unknown }) =>
      barBorderRadius(context?.raw, horizontal, radius);
    delete record.presentonBarRadius;
  });
}

function datasetBackgroundColor(dataset: unknown, index: number): string | null {
  const backgroundColor = readRecord(dataset).backgroundColor;
  const color = Array.isArray(backgroundColor)
    ? backgroundColor[index]
    : backgroundColor;
  return typeof color === "string" ? color : null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function parseColor(color: string | null): [number, number, number, number] | null {
  if (!color) return null;
  const hex = String(color).match(/^#([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$/);
  if (hex) {
    const raw =
      hex[1].length === 3
        ? hex[1]
          .split("")
          .map((ch) => ch + ch)
          .join("")
        : hex[1];
    const value = Number.parseInt(raw, 16);
    return [(value >> 16) & 255, (value >> 8) & 255, value & 255, 1];
  }

  const rgb = String(color).match(/^rgba?\(([^)]+)\)$/i);
  if (!rgb) return null;
  const channels = rgb[1].split(",").map((part) => Number(part.trim()));
  if (channels.length < 3 || channels.slice(0, 3).some(Number.isNaN)) {
    return null;
  }
  return [
    clamp(channels[0], 0, 255),
    clamp(channels[1], 0, 255),
    clamp(channels[2], 0, 255),
    clamp(Number.isFinite(channels[3]) ? channels[3] : 1, 0, 1),
  ];
}

function relativeLuminance(channels: number[]): number {
  const mapped = channels.map((channel) => {
    const normalized = channel / 255;
    return normalized <= 0.04045
      ? normalized / 12.92
      : Math.pow((normalized + 0.055) / 1.055, 2.4);
  });
  return mapped[0] * 0.2126 + mapped[1] * 0.7152 + mapped[2] * 0.0722;
}

function contrastRatio(a: number, b: number): number {
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

function contrastTextColor(backgroundColor: string | null, fallback: string): string {
  const background = parseColor(backgroundColor);
  if (!background) return fallback;
  const composite = [background[0], background[1], background[2]].map(
    (channel) => channel * background[3] + 255 * (1 - background[3])
  );
  const bgLuminance = relativeLuminance(composite);
  const dark = [16, 24, 40];
  const light = [255, 255, 255];
  const darkContrast = contrastRatio(bgLuminance, relativeLuminance(dark));
  const lightContrast = contrastRatio(bgLuminance, relativeLuminance(light));
  return lightContrast >= darkContrast ? "#FFFFFF" : "#101828";
}

function chartElementPoint(element: unknown): Point | null {
  const candidate = element as {
    x?: unknown;
    y?: unknown;
    tooltipPosition?: (useFinalPosition?: boolean) => { x?: unknown; y?: unknown };
  };
  const point = candidate;

  return readNumber(point.x) != null && readNumber(point.y) != null
    ? { x: readNumber(point.x) ?? 0, y: readNumber(point.y) ?? 0 }
    : null;
}

function pointRadius(element: unknown): number {
  const elementRecord = readRecord(element);
  const options = readRecord(elementRecord.options);
  return Math.max(
    0,
    readNumber(options.radius) ?? readNumber(elementRecord.radius) ?? 3
  );
}

function labelBounds(x: number, y: number, width: number, height: number): Bounds {
  const padding = 2;
  return {
    left: x - width / 2 - padding,
    right: x + width / 2 + padding,
    top: y - height / 2 - padding,
    bottom: y + height / 2 + padding,
  };
}

function boundsOverlap(a: Bounds, b: Bounds): boolean {
  return !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
}

function fitsChartArea(bounds: Bounds, area: Bounds): boolean {
  return (
    bounds.left >= area.left &&
    bounds.right <= area.right &&
    bounds.top >= area.top &&
    bounds.bottom <= area.bottom
  );
}

function lineDirection(
  elements: unknown[],
  index: number,
  datasetIndex: number
): number {
  const current = readNumber(readRecord(elements[index]).y);
  const prev = readNumber(readRecord(elements[index - 1]).y);
  const next = readNumber(readRecord(elements[index + 1]).y);
  if (current == null) return datasetIndex % 2 === 0 ? -1 : 1;
  if (prev != null && next != null) {
    if (current <= prev && current <= next) return -1;
    if (current >= prev && current >= next) return 1;
  }
  if (next != null && prev == null) return next < current ? 1 : -1;
  if (prev != null && next == null) return prev < current ? 1 : -1;
  return datasetIndex % 2 === 0 ? -1 : 1;
}

function drawBarLabel({
  color,
  ctx,
  element,
  fontSize,
  horizontal,
  label,
  outsideColor,
  position,
  value,
}: {
  color: string | null;
  ctx: CanvasRenderingContext2D;
  element: unknown;
  fontSize: number;
  horizontal: boolean;
  label: string;
  outsideColor: string;
  position: DataLabelPosition;
  value: number;
}) {
  const elementRecord = readRecord(element);
  const x = readNumber(elementRecord.x);
  const y = readNumber(elementRecord.y);
  const base = readNumber(elementRecord.base);
  const width = Math.abs(readNumber(elementRecord.width) || 0);
  const height = Math.abs(readNumber(elementRecord.height) || 0);
  if (x == null || y == null || base == null) return;

  const textWidth = ctx.measureText(label).width;
  const padding = 5;
  const fits = horizontal
    ? width >= textWidth + padding * 2 && height >= fontSize * 1.35
    : height >= fontSize * 1.65 && width >= textWidth + padding * 2;
  const resolvedPosition =
    position === "outside" || !fits ? "outside" : position;

  if (resolvedPosition !== "outside") {
    ctx.fillStyle = contrastTextColor(color, outsideColor);
    if (horizontal) {
      const direction = value < 0 ? -1 : 1;
      const labelX =
        resolvedPosition === "base"
          ? base + direction * (textWidth / 2 + padding)
          : resolvedPosition === "top"
            ? x - direction * (textWidth / 2 + padding)
            : (x + base) / 2;
      ctx.fillText(label, labelX, y);
      return;
    }

    const direction = value < 0 ? 1 : -1;
    const labelY =
      resolvedPosition === "base"
        ? base + direction * (fontSize / 2 + padding)
        : resolvedPosition === "top"
          ? y - direction * (fontSize / 2 + padding)
          : (y + base) / 2;
    ctx.fillText(label, x, labelY);
    return;
  }

  ctx.fillStyle = outsideColor;
  if (horizontal) {
    const direction = value < 0 ? -1 : 1;
    ctx.fillText(label, x + direction * (textWidth / 2 + padding), y);
    return;
  }

  const direction = value < 0 ? 1 : -1;
  ctx.fillText(label, x, y + direction * (fontSize / 2 + padding));
}

function drawPointLabel({
  chartArea,
  ctx,
  datasetIndex,
  element,
  fontSize,
  index,
  label,
  lineLike,
  metaElements,
  occupied,
  outsideColor,
  position,
}: {
  chartArea: Bounds;
  ctx: CanvasRenderingContext2D;
  datasetIndex: number;
  element: unknown;
  fontSize: number;
  index: number;
  label: string;
  lineLike: boolean;
  metaElements: unknown[];
  occupied: Bounds[];
  outsideColor: string;
  position: DataLabelPosition;
}) {
  const point = chartElementPoint(element);
  if (!point) return;

  const radius = pointRadius(element);
  const textWidth = ctx.measureText(label).width;
  const textHeight = fontSize * 1.15;
  const direction = lineLike
    ? lineDirection(metaElements, index, datasetIndex)
    : (index + datasetIndex) % 2 === 0
      ? -1
      : 1;
  const vertical = radius + textHeight / 2 + 5;
  const horizontal = radius + textWidth / 2 + 5;

  if (position !== "outside") {
    const placed =
      position === "base"
        ? { x: point.x, y: point.y + vertical }
        : position === "top"
          ? { x: point.x, y: point.y - vertical }
          : { x: point.x, y: point.y };
    const placedBounds = labelBounds(placed.x, placed.y, textWidth, textHeight);
    if (
      fitsChartArea(placedBounds, chartArea) &&
      occupied.every((existing) => !boundsOverlap(placedBounds, existing))
    ) {
      occupied.push(placedBounds);
      ctx.fillStyle = outsideColor;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(label, placed.x, placed.y);
      return;
    }
  }

  const candidates = [
    { x: point.x, y: point.y + direction * vertical },
    { x: point.x, y: point.y - direction * vertical },
    { x: point.x + horizontal, y: point.y },
    { x: point.x - horizontal, y: point.y },
    { x: point.x + horizontal, y: point.y + direction * vertical },
    { x: point.x - horizontal, y: point.y + direction * vertical },
    { x: point.x + horizontal, y: point.y - direction * vertical },
    { x: point.x - horizontal, y: point.y - direction * vertical },
    { x: point.x, y: point.y + direction * vertical * 1.7 },
    { x: point.x, y: point.y - direction * vertical * 1.7 },
  ];

  for (const candidate of candidates) {
    const bounds = labelBounds(candidate.x, candidate.y, textWidth, textHeight);
    if (!fitsChartArea(bounds, chartArea)) continue;
    if (occupied.some((existing) => boundsOverlap(bounds, existing))) continue;
    occupied.push(bounds);
    ctx.fillStyle = outsideColor;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, candidate.x, candidate.y);
    return;
  }
}

function drawArcLabel({
  color,
  ctx,
  element,
  fontSize,
  label,
  outsideColor,
  position,
}: {
  color: string | null;
  ctx: CanvasRenderingContext2D;
  element: unknown;
  fontSize: number;
  label: string;
  outsideColor: string;
  position: DataLabelPosition;
}) {
  const record = readRecord(element);
  const centerX = readNumber(record.x);
  const centerY = readNumber(record.y);
  const startAngle = readNumber(record.startAngle);
  const endAngle = readNumber(record.endAngle);
  const innerRadius = Math.max(0, readNumber(record.innerRadius) ?? 0);
  const outerRadius = Math.max(innerRadius, readNumber(record.outerRadius) ?? 0);
  let point: Point | null = null;

  if (
    centerX != null &&
    centerY != null &&
    startAngle != null &&
    endAngle != null &&
    outerRadius > 0
  ) {
    const angle = (startAngle + endAngle) / 2;
    const ringWidth = Math.max(1, outerRadius - innerRadius);
    const textHeight = fontSize * 1.15;
    const radius =
      position === "outside"
        ? outerRadius + textHeight / 2 + 7
        : position === "top"
          ? Math.max(innerRadius + textHeight / 2, outerRadius - textHeight / 2 - 5)
          : position === "base"
            ? innerRadius > 0
              ? innerRadius + Math.min(ringWidth * 0.25, textHeight + 5)
              : outerRadius * 0.35
            : innerRadius + ringWidth / 2;
    point = {
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
    };
  } else {
    const tooltipElement = element as {
      tooltipPosition?: (useFinalPosition?: boolean) => Point;
    };
    if (typeof tooltipElement.tooltipPosition === "function") {
      point = tooltipElement.tooltipPosition(true);
    }
  }

  if (!point) return;
  ctx.fillStyle =
    position === "outside"
      ? outsideColor
      : contrastTextColor(color, outsideColor);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, point.x || 0, point.y || 0);
}

function isPointType(type: string): boolean {
  return type === "line" || type === "scatter" || type === "bubble" || type === "radar";
}

function isArcType(type: string): boolean {
  return type === "pie" || type === "doughnut" || type === "polarArea";
}

function readDataLabelPosition(value: unknown): DataLabelPosition {
  return value === "base" ||
    value === "mid" ||
    value === "outside" ||
    value === "top"
    ? value
    : "top";
}

const presentonDataLabelPlugin: Plugin = {
  id: "presentonDataLabels",
  afterDatasetsDraw(chart, _args, options) {
    const labelOptions = options as PresentonDataLabelOptions | undefined;
    if (!labelOptions?.enabled) return;

    const ctx = chart.ctx;
    const fontSize = labelOptions.fontSize || 11;
    const outsideColor = labelOptions.color || "#475467";
    const position = readDataLabelPosition(labelOptions.position);
    ctx.save();
    ctx.font = `600 ${fontSize}px ${labelOptions.fontFamily || "Inter, Arial, sans-serif"
      }`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const occupied: Bounds[] = [];

    chart.data.datasets.forEach((dataset, datasetIndex) => {
      const meta = chart.getDatasetMeta(datasetIndex);
      if (meta.hidden) return;
      const metaType = String((meta as { type?: string }).type || "");

      meta.data.forEach((element, index) => {
        const raw = Array.isArray(dataset.data) ? dataset.data[index] : 0;
        const value = chartValue(raw);
        const label = formatChartValue(value);
        if (!label) return;

        if (metaType === "bar") {
          drawBarLabel({
            color: datasetBackgroundColor(dataset, index),
            ctx,
            element,
            fontSize,
            horizontal: Boolean(labelOptions.horizontal),
            label,
            outsideColor,
            position,
            value,
          });
          return;
        }

        if (isPointType(metaType)) {
          drawPointLabel({
            chartArea: chart.chartArea,
            ctx,
            datasetIndex,
            element,
            fontSize,
            index,
            label,
            lineLike: metaType === "line" || metaType === "radar",
            metaElements: meta.data,
            occupied,
            outsideColor,
            position,
          });
          return;
        }

        if (isArcType(metaType)) {
          drawArcLabel({
            color: datasetBackgroundColor(dataset, index),
            ctx,
            element,
            fontSize,
            label,
            outsideColor,
            position,
          });
          return;
        }

        const tooltipElement = element as {
          tooltipPosition?: (useFinalPosition?: boolean) => Point;
        };
        const fallbackPosition =
          typeof tooltipElement.tooltipPosition === "function"
            ? tooltipElement.tooltipPosition(true)
            : null;
        if (!fallbackPosition) return;
        ctx.fillStyle = outsideColor;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(label, fallbackPosition.x || 0, fallbackPosition.y || 0);
      });
    });

    ctx.restore();
  },
};

export function shouldRenderTemplateV2HtmlPreview(
  slide: unknown,
  presentationVersion?: unknown
): boolean {
  if (!slide || typeof slide !== "object") return false;
  const record = slide as Record<string, unknown>;
  const isTemplateV2Presentation = presentationVersion === "v2-standard";
  return (
    (isTemplateV2Presentation || hasTemplateV2RenderableUi(record.ui)) &&
    hasTemplateV2RenderableUi(record.ui)
  );
}

export function TemplateV2HtmlSlidePreview({
  slide,
  fonts,
  fixedSize = false,
  className = "",
  contentClassName = "",
}: {
  slide: unknown;
  fonts?: unknown;
  fixedSize?: boolean;
  className?: string;
  contentClassName?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);


  const html = useMemo(() => {
    if (!slide || typeof slide !== "object") return null;
    return templateV2UiToHtmlFragment((slide as Record<string, unknown>).ui, {
      fonts,
    });
  }, [fonts, slide]);
  const htmlMarkup = useMemo(
    () => ({ __html: html ?? "" }),
    [html]
  );

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;

    const updateWidth = () => setContainerWidth(element.clientWidth);
    updateWidth();

    const resizeObserver = new ResizeObserver(updateWidth);
    resizeObserver.observe(element);
    return () => resizeObserver.disconnect();
  }, []);

  useChartLayoutEffect(() => {
    const element = contentRef.current;
    if (!element || !html) return;

    let disposed = false;
    const charts: ChartInstance[] = [];
    let completed = false;
    let registeredPendingCount = 0;

    const finishReady = () => {
      if (completed) return;
      completed = true;
      element.dataset.presentonCharts = "ready";
      markChartsReady(registeredPendingCount);
    };

    const finishError = (message: string) => {
      if (completed) return;
      completed = true;
      element.dataset.presentonCharts = "error";
      markChartsError(message);
    };

    const renderCharts = () => {
      const canvases = Array.from(
        element.querySelectorAll<HTMLCanvasElement>("canvas[data-presenton-chart]")
      ).filter((canvas) => canvas.getAttribute("data-chart-config"));

      if (!canvases.length) {
        element.dataset.presentonCharts = "ready";
        return;
      }

      registeredPendingCount = canvases.length;
      element.dataset.presentonCharts = "pending";
      markChartsPending(registeredPendingCount);

      try {
        registerPresentonDataLabelPlugin();

        canvases.forEach((canvas) => {
          const configText = canvas.getAttribute("data-chart-config");
          if (!configText) return;

          const existing = Chart.getChart(canvas);
          existing?.destroy();

          const config = JSON.parse(configText) as ChartConfiguration;
          config.options = {
            ...(config.options ?? {}),
            animation: false,
            responsive: false,
            maintainAspectRatio: false,
          };
          hydrateScales(
            (config.options as { scales?: unknown } | undefined)?.scales
          );
          hydrateBarBorderRadii(config);

          const chart = new Chart(canvas, config);
          chart.update("none");
          canvas.dataset.presentonChartRendered = "true";
          charts.push(chart);
        });

        if (!disposed) finishReady();
      } catch (error) {
        const message = errorMessage(error);
        finishError(message);
        console.error("Failed to render template v2 charts:", error);
      }
    };

    renderCharts();

    return () => {
      disposed = true;
      if (!completed) {
        markChartsReady(registeredPendingCount);
      }
      charts.forEach((chart) => chart.destroy());
    };
  }, [html]);

  const scale = fixedSize
    ? 1
    : containerWidth
      ? Math.min((containerWidth / TEMPLATE_V2_HTML_WIDTH) * 0.98, 1)
      : 0;
  const previewHeight = TEMPLATE_V2_HTML_HEIGHT * (scale || 1);

  if (!html) {
    return (
      <div
        ref={containerRef}
        className={`relative flex aspect-video w-full items-center justify-center bg-white text-xs text-gray-500 ${className}`}
        style={
          fixedSize
            ? {
              width: TEMPLATE_V2_HTML_WIDTH,
              height: TEMPLATE_V2_HTML_HEIGHT,
            }
            : undefined
        }
      >
        Preview unavailable
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`relative w-full overflow-hidden bg-white ${className}`}
      style={
        fixedSize
          ? {
            width: TEMPLATE_V2_HTML_WIDTH,
            height: TEMPLATE_V2_HTML_HEIGHT,
          }
          : {
            height: scale ? previewHeight : undefined,
            aspectRatio: scale ? undefined : "16 / 9",
          }
      }
    >
      <div
        className={
          fixedSize ? "absolute left-0 top-0" : "absolute left-1/2 top-0"
        }
        style={{
          width: TEMPLATE_V2_HTML_WIDTH,
          height: TEMPLATE_V2_HTML_HEIGHT,
          transform: fixedSize
            ? undefined
            : `translateX(-50%) scale(${scale || 1})`,
          transformOrigin: fixedSize ? undefined : "top center",
          opacity: scale ? 1 : 0,
        }}
      >
        <div
          ref={contentRef}
          aria-label="Template v2 slide preview"
          className={`block h-full w-full bg-white ${contentClassName}`}
          style={{ pointerEvents: "none" }}
          dangerouslySetInnerHTML={htmlMarkup}
        />
      </div>
    </div>
  );
}
