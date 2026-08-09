import {
  TEMPLATE_V2_DATA_LABEL_POSITIONS,
  type TemplateV2DataLabelPosition,
} from "./template-v2-html/template-v2-contract.generated.ts";

export const CHART_BROWSER_SCRIPT_URL = "/vendor/chart-4.5.1.umd.min.js";
export const CHART_DATALABELS_SCRIPT_URL =
  "/vendor/chartjs-plugin-datalabels-2.2.0.min.js";

export type ChartDatasetLike = {
  backgroundColor?: unknown;
  data?: unknown;
  [key: string]: unknown;
};

export type ChartConfigurationLike = {
  data?: {
    datasets?: ChartDatasetLike[];
    labels?: unknown;
    [key: string]: unknown;
  };
  options?: Record<string, unknown>;
  plugins?: unknown[];
  type?: unknown;
};

export type ChartInstanceLike = {
  destroy(): void;
  update(mode?: string): void;
};

export type ChartConstructorLike = {
  new (
    item: HTMLCanvasElement,
    config: ChartConfigurationLike,
  ): ChartInstanceLike;
  getChart(item: HTMLCanvasElement): ChartInstanceLike | undefined;
  register(...plugins: unknown[]): void;
};

export type DataLabelsContextLike = {
  chart: {
    getDatasetMeta(datasetIndex: number): { type?: unknown };
  };
  dataIndex: number;
  dataset: ChartDatasetLike;
  datasetIndex: number;
};

export type ChartBrowserRuntime = {
  Chart: ChartConstructorLike;
  ChartDataLabels: unknown;
};

// Data label types and utilities
export type PresentonDataLabelOptions = {
  enabled?: boolean;
  color?: string;
  fontFamily?: string;
  fontSize?: number;
  horizontal?: boolean;
  position?: DataLabelPosition;
};

export type DataLabelPosition = TemplateV2DataLabelPosition;

const DATA_LABEL_POSITIONS = new Set<string>(TEMPLATE_V2_DATA_LABEL_POSITIONS);

type Point = { x: number; y: number };
type Bounds = { left: number; right: number; top: number; bottom: number };

// Utility functions for data label rendering
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

function datasetBackgroundColor(dataset: unknown, index: number): string | null {
  const backgroundColor = readRecord(dataset).backgroundColor;
  const color = Array.isArray(backgroundColor)
    ? backgroundColor[index]
    : backgroundColor;
  return typeof color === "string" ? color : null;
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
  return typeof value === "string" && DATA_LABEL_POSITIONS.has(value)
    ? (value as DataLabelPosition)
    : "top";
}

export function createPresentonDataLabelPlugin(): unknown {
  return {
    id: "presentonDataLabels",
    afterDatasetsDraw(chart: unknown, _args: unknown, options: unknown) {
      const typedChart = chart as {
        ctx: CanvasRenderingContext2D;
        data: { datasets: unknown[] };
        chartArea: Bounds;
        getDatasetMeta(index: number): {
          hidden?: boolean;
          type?: string;
          data: unknown[];
        };
      };
      const labelOptions = options as PresentonDataLabelOptions | undefined;
      if (!labelOptions?.enabled) return;

      const ctx = typedChart.ctx;
      const fontSize = labelOptions.fontSize || 11;
      const outsideColor = labelOptions.color || "#475467";
      const position = readDataLabelPosition(labelOptions.position);
      ctx.save();
      ctx.font = `600 ${fontSize}px ${labelOptions.fontFamily || "Inter, Arial, sans-serif"
        }`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const occupied: Bounds[] = [];

      typedChart.data.datasets.forEach((dataset, datasetIndex) => {
        const meta = typedChart.getDatasetMeta(datasetIndex);
        if (meta.hidden) return;
        const metaType = String(meta.type || "");

        meta.data.forEach((element, index) => {
          const dataArray = (dataset as { data?: unknown }).data;
          const raw = Array.isArray(dataArray) ? dataArray[index] : 0;
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
              chartArea: typedChart.chartArea,
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
}

let didRegisterPresentonDataLabelPlugin = false;

function ensureLocalScript(
  src: string,
  marker: string,
  isReady: () => boolean,
) {
  if (isReady()) return Promise.resolve();

  let script = document.querySelector<HTMLScriptElement>(
    `script[data-presenton-chart-runtime="${marker}"]`,
  );
  if (!script) {
    script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset.presentonChartRuntime = marker;
    document.head.appendChild(script);
  }

  return new Promise<void>((resolve, reject) => {
    if (isReady()) {
      resolve();
      return;
    }
    const handleLoad = () => {
      if (isReady()) {
        resolve();
        return;
      }
      script.remove();
      reject(new Error(`Local chart runtime did not initialize: ${src}`));
    };
    const handleError = () => {
      script.remove();
      reject(new Error(`Failed to load local chart runtime: ${src}`));
    };
    script.addEventListener("load", handleLoad, { once: true });
    script.addEventListener("error", handleError, { once: true });
  });
}

export function registerPresentonDataLabelPlugin(Chart: ChartConstructorLike): void {
  if (didRegisterPresentonDataLabelPlugin) return;
  Chart.register(createPresentonDataLabelPlugin());
  didRegisterPresentonDataLabelPlugin = true;
}

export function loadChartBrowserRuntime(): Promise<ChartBrowserRuntime> {
  if (typeof document === "undefined" || typeof window === "undefined") {
    return Promise.reject(
      new Error("The local Chart.js runtime requires a browser document"),
    );
  }

  const existing = window.__PRESENTON_CHART_BROWSER_RUNTIME__;
  if (existing) return existing;

  const runtime = (async () => {
    await ensureLocalScript(
      CHART_BROWSER_SCRIPT_URL,
      "chartjs",
      () => typeof window.Chart === "function",
    );
    await ensureLocalScript(
      CHART_DATALABELS_SCRIPT_URL,
      "datalabels",
      () => Boolean(window.ChartDataLabels),
    );

    const Chart = window.Chart;
    const ChartDataLabels = window.ChartDataLabels;
    if (!Chart || !ChartDataLabels) {
      throw new Error("The local Chart.js runtime is incomplete");
    }
    Chart.register(ChartDataLabels);
    registerPresentonDataLabelPlugin(Chart);
    return { Chart, ChartDataLabels };
  })();

  window.__PRESENTON_CHART_BROWSER_RUNTIME__ = runtime;
  void runtime.catch(() => {
    if (window.__PRESENTON_CHART_BROWSER_RUNTIME__ === runtime) {
      delete window.__PRESENTON_CHART_BROWSER_RUNTIME__;
    }
  });
  return runtime;
}

declare global {
  interface Window {
    Chart?: ChartConstructorLike;
    ChartDataLabels?: unknown;
    __PRESENTON_CHART_BROWSER_RUNTIME__?: Promise<ChartBrowserRuntime>;
  }
}
