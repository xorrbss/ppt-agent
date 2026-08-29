import type { AuthoredHybridBounds } from "./schema.ts";
import type { BrowserElementObservation } from "./observation.ts";
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";

export const AUTHORED_NATIVE_CHART_SCHEMA_VERSION =
  "authored-native-chart/v1" as const;

export type AuthoredNativeChartType =
  | "bar"
  | "column"
  | "line"
  | "area"
  | "pie"
  | "donut";

export type AuthoredNativeChartLegendPosition =
  | "bottom"
  | "top"
  | "right"
  | "left";

export interface AuthoredNativeChartTextStyle {
  readonly fontFamily?: string;
  readonly fontSizePt?: number;
  readonly color?: string;
}

export interface AuthoredNativeChartPayloadV1 {
  readonly schemaVersion?: typeof AUTHORED_NATIVE_CHART_SCHEMA_VERSION;
  readonly chartType: AuthoredNativeChartType;
  readonly categories: readonly string[];
  readonly series: readonly {
    readonly name: string;
    readonly values: readonly number[];
    readonly color?: string;
  }[];
  readonly title?: string;
  readonly fontFamily?: string;
  readonly fontSizePt?: number;
  readonly textColor?: string;
  readonly palette?: readonly string[];
  readonly legend?: AuthoredNativeChartTextStyle & {
    readonly visible: boolean;
    readonly position?: AuthoredNativeChartLegendPosition;
  };
  readonly categoryAxis?: AuthoredNativeChartTextStyle & {
    readonly title?: string;
  };
  readonly valueAxis?: AuthoredNativeChartTextStyle & {
    readonly title?: string;
  };
}

export interface AuthoredHybridNativeChartSource {
  readonly id: string;
  readonly domPath: string;
  readonly tagName: string;
  readonly sourceIndex: number;
  readonly zOrder: number;
  readonly cssZIndex: number | null;
  readonly bounds: AuthoredHybridBounds;
  readonly rotationDeg: 0;
  readonly opacity: 1;
  readonly parentElementId?: string | null;
  readonly classification: {
    readonly mode: "native";
    readonly kind: "chart";
    readonly confidence: "safe";
  };
  readonly chart: AuthoredNativeChartPayloadV1;
}

export interface NativeChartPreparationResult {
  readonly charts: readonly AuthoredHybridNativeChartSource[];
  readonly rejections: ReadonlyMap<
    string,
    "unsupported-native-chart" | "unsupported-chart-css"
  >;
}

function nativeChartAdmissionEnabled(): boolean {
  // Native chart serialization remains compiled and type-checked, but actual
  // product admission stays disabled until the PowerPoint reopen seal passes.
  return false;
}

const HEX = /^#?[0-9A-Fa-f]{6}$/;
const MAX_CATEGORIES = 500;
const MAX_SERIES = 50;
const MAX_LABEL_LENGTH = 256;
const MAX_FONT_FAMILY_LENGTH = 200;
const MAX_FONT_SIZE_PT = 96;
const COMPLEX_CSS_REASONS = new Set([
  "animated",
  "backdrop-filter",
  "clip-path",
  "complex-transform",
  "filter",
  "mask",
  "mix-blend-mode",
  "overflow-clipped",
  "transformed-ancestor",
  "unsupported-opacity",
]);
const SUPPORTED_CHART_FEATURES = new Set([
  "chart",
  "chart-data",
  "native-chart",
  "chart.axes",
  "chart.legend",
  "chart.fonts",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown, maximum = MAX_LABEL_LENGTH): string | null {
  return typeof value === "string" && value.length <= maximum ? value : null;
}

function colorValue(value: unknown): string | undefined | null {
  if (value === undefined) return undefined;
  if (typeof value !== "string" || !HEX.test(value)) return null;
  return `#${value.replace("#", "").toUpperCase()}`;
}

function fontSizeValue(value: unknown): number | undefined | null {
  if (value === undefined) return undefined;
  return typeof value === "number" && Number.isFinite(value) && value >= 6 &&
      value <= MAX_FONT_SIZE_PT
    ? value
    : null;
}

function textStyleValue(
  value: unknown,
  includeTitle: boolean,
): (AuthoredNativeChartTextStyle & { title?: string }) | undefined | null {
  if (value === undefined) return undefined;
  if (!isRecord(value)) return null;
  const fontFamily = value.fontFamily === undefined
    ? undefined
    : stringValue(value.fontFamily, MAX_FONT_FAMILY_LENGTH);
  const fontSizePt = fontSizeValue(value.fontSizePt);
  const color = colorValue(value.color);
  const title = !includeTitle || value.title === undefined
    ? undefined
    : stringValue(value.title);
  if (
    fontFamily === null || fontSizePt === null || color === null ||
    title === null
  ) return null;
  return {
    ...(fontFamily ? { fontFamily } : {}),
    ...(fontSizePt !== undefined ? { fontSizePt } : {}),
    ...(color ? { color } : {}),
    ...(title !== undefined ? { title } : {}),
  };
}

/**
 * Validate the bounded chart subset at the export trust boundary. Unknown or
 * complex payloads are not guessed: the authored chart remains a raster island.
 */
export function parseAuthoredNativeChartPayload(
  value: unknown,
): AuthoredNativeChartPayloadV1 | null {
  if (!isRecord(value)) return null;
  if (
    value.schemaVersion !== undefined &&
    value.schemaVersion !== AUTHORED_NATIVE_CHART_SCHEMA_VERSION
  ) return null;
  if (
    !["bar", "column", "line", "area", "pie", "donut"].includes(
      String(value.chartType),
    ) ||
    !Array.isArray(value.categories) ||
    value.categories.length === 0 ||
    value.categories.length > MAX_CATEGORIES ||
    !Array.isArray(value.series) ||
    value.series.length === 0 ||
    value.series.length > MAX_SERIES
  ) return null;
  const categories = value.categories.map((item) => stringValue(item));
  if (categories.some((item) => item === null)) return null;
  const series = value.series.map((item) => {
    if (!isRecord(item)) return null;
    const name = stringValue(item.name);
    const color = colorValue(item.color);
    if (
      name === null || color === null || !Array.isArray(item.values) ||
      item.values.length !== categories.length ||
      item.values.some(
        (entry) => typeof entry !== "number" || !Number.isFinite(entry),
      )
    ) return null;
    return {
      name,
      values: item.values as number[],
      ...(color ? { color } : {}),
    };
  });
  if (series.some((item) => item === null)) return null;
  const title = value.title === undefined
    ? undefined
    : stringValue(value.title, 512);
  const fontFamily = value.fontFamily === undefined
    ? undefined
    : stringValue(value.fontFamily, MAX_FONT_FAMILY_LENGTH);
  const fontSizePt = fontSizeValue(value.fontSizePt);
  const textColor = colorValue(value.textColor);
  const palette = value.palette === undefined
    ? undefined
    : Array.isArray(value.palette)
      ? value.palette.map(colorValue)
      : null;
  const categoryAxis = textStyleValue(value.categoryAxis, true);
  const valueAxis = textStyleValue(value.valueAxis, true);
  let legend: AuthoredNativeChartPayloadV1["legend"];
  if (value.legend !== undefined) {
    if (!isRecord(value.legend) || typeof value.legend.visible !== "boolean") {
      return null;
    }
    const style = textStyleValue(value.legend, false);
    const position = value.legend.position === undefined
      ? undefined
      : String(value.legend.position);
    if (
      style === null ||
      (position !== undefined &&
        !["bottom", "top", "right", "left"].includes(position))
    ) return null;
    legend = {
      visible: value.legend.visible,
      ...(position
        ? { position: position as AuthoredNativeChartLegendPosition }
        : {}),
      ...style,
    };
  }
  if (
    title === null || fontFamily === null || fontSizePt === null ||
    textColor === null || palette === null ||
    palette?.some((item) => typeof item !== "string") || categoryAxis === null ||
    valueAxis === null
  ) return null;
  return Object.freeze({
    schemaVersion: AUTHORED_NATIVE_CHART_SCHEMA_VERSION,
    chartType: value.chartType as AuthoredNativeChartType,
    categories: Object.freeze(categories as string[]),
    series: Object.freeze(series as NonNullable<(typeof series)[number]>[]),
    ...(title !== undefined ? { title } : {}),
    ...(fontFamily ? { fontFamily } : {}),
    ...(fontSizePt !== undefined ? { fontSizePt } : {}),
    ...(textColor ? { textColor } : {}),
    ...(palette
      ? { palette: Object.freeze(palette as string[]) }
      : {}),
    ...(legend ? { legend: Object.freeze(legend) } : {}),
    ...(categoryAxis ? { categoryAxis: Object.freeze(categoryAxis) } : {}),
    ...(valueAxis ? { valueAxis: Object.freeze(valueAxis) } : {}),
  });
}

export function prepareDeclaredNativeCharts(
  declaredElements: readonly DeclaredSemanticElementForObservation[],
  observedElements: readonly BrowserElementObservation[],
): NativeChartPreparationResult {
  const observed = new Map(
    observedElements.map((element, zOrder) => [element.id, { element, zOrder }]),
  );
  const charts: AuthoredHybridNativeChartSource[] = [];
  const rejections = new Map<
    string,
    "unsupported-native-chart" | "unsupported-chart-css"
  >();
  for (const declared of declaredElements) {
    if (declared.kind !== "chart") continue;
    // Actual-product PowerPoint qualification currently fails in chart.dll.
    // Keep the chart on the fidelity backplate and emit no native chart OOXML
    // until this admission path has a fresh first-open/save/reopen seal.
    if (!nativeChartAdmissionEnabled()) {
      rejections.set(declared.elementId, "unsupported-native-chart");
      continue;
    }
    const observedEntry = observed.get(declared.elementId);
    const payload = parseAuthoredNativeChartPayload(declared.payload);
    if (!observedEntry || !payload) {
      rejections.set(declared.elementId, "unsupported-native-chart");
      continue;
    }
    const actual = observedEntry.element;
    if (
      actual.candidateKind !== "complex" ||
      !actual.fallbackReasons.includes("complex-chart") ||
      (declared.requiredFeatures ?? []).some(
        (feature) => !SUPPORTED_CHART_FEATURES.has(feature),
      )
    ) {
      rejections.set(declared.elementId, "unsupported-native-chart");
      continue;
    }
    if (
      Math.abs(actual.rotationDeg) > 0.001 || actual.opacity < 0.999 ||
      actual.fallbackReasons.some((reason) => COMPLEX_CSS_REASONS.has(reason))
    ) {
      rejections.set(declared.elementId, "unsupported-chart-css");
      continue;
    }
    charts.push({
      id: actual.id,
      domPath: actual.domPath,
      tagName: actual.tagName,
      sourceIndex: actual.sourceIndex,
      zOrder: observedEntry.zOrder,
      cssZIndex: actual.cssZIndex,
      bounds: {
        px: actual.boundsPx,
        inches: {
          x: actual.boundsPx.x / 96,
          y: actual.boundsPx.y / 96,
          width: actual.boundsPx.width / 96,
          height: actual.boundsPx.height / 96,
        },
      },
      rotationDeg: 0,
      opacity: 1,
      parentElementId: actual.parentElementId ?? null,
      classification: { mode: "native", kind: "chart", confidence: "safe" },
      chart: payload,
    });
  }
  return { charts, rejections };
}

function escapeXml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function normalizedHex(value: string | undefined, fallback: string): string {
  return (value ?? fallback).replace("#", "").toUpperCase();
}

function textPropertiesXml(
  payload: AuthoredNativeChartPayloadV1,
  style: AuthoredNativeChartTextStyle | undefined,
): string {
  const family = escapeXml(style?.fontFamily ?? payload.fontFamily ?? "Arial");
  const size = Math.round((style?.fontSizePt ?? payload.fontSizePt ?? 10) * 100);
  const color = normalizedHex(style?.color ?? payload.textColor, "404040");
  return `<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="${size}"><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:latin typeface="${family}"/><a:ea typeface="${family}"/></a:defRPr></a:pPr><a:endParaRPr lang="en-US"/></a:p></c:txPr>`;
}

function richTextXml(
  text: string,
  payload: AuthoredNativeChartPayloadV1,
  style?: AuthoredNativeChartTextStyle,
): string {
  const family = escapeXml(style?.fontFamily ?? payload.fontFamily ?? "Arial");
  const size = Math.round((style?.fontSizePt ?? payload.fontSizePt ?? 10) * 100);
  const color = normalizedHex(style?.color ?? payload.textColor, "404040");
  return `<c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="${size}"><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:latin typeface="${family}"/><a:ea typeface="${family}"/></a:rPr><a:t>${escapeXml(text)}</a:t></a:r></a:p></c:rich></c:tx>`;
}

function stringLiteral(values: readonly string[]): string {
  return `<c:strLit><c:ptCount val="${values.length}"/>${values.map((value, index) => `<c:pt idx="${index}"><c:v>${escapeXml(value)}</c:v></c:pt>`).join("")}</c:strLit>`;
}

function numberLiteral(values: readonly number[]): string {
  return `<c:numLit><c:formatCode>General</c:formatCode><c:ptCount val="${values.length}"/>${values.map((value, index) => `<c:pt idx="${index}"><c:v>${value}</c:v></c:pt>`).join("")}</c:numLit>`;
}

/** Build a self-contained native chart part with cached semantic source data. */
export function serializeNativeChartPart(
  source: AuthoredHybridNativeChartSource,
): string {
  const payload = source.chart;
  const palette = payload.palette?.length
    ? payload.palette
    : ["2457D6", "68A4FF", "19A974", "F2B134", "D84A5B", "7A5AF8"];
  const seriesXml = payload.series.map((series, index) => {
    const color = normalizedHex(series.color ?? palette[index % palette.length], "2457D6");
    return `<c:ser><c:idx val="${index}"/><c:order val="${index}"/><c:tx><c:v>${escapeXml(series.name)}</c:v></c:tx><c:spPr><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:ln><a:solidFill><a:srgbClr val="${color}"/></a:solidFill></a:ln></c:spPr><c:cat>${stringLiteral(payload.categories)}</c:cat><c:val>${numberLiteral(series.values)}</c:val></c:ser>`;
  }).join("");
  const axes = payload.chartType === "pie" || payload.chartType === "donut"
    ? ""
    : `<c:catAx><c:axId val="111111111"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="${payload.chartType === "bar" ? "l" : "b"}"/>${payload.categoryAxis?.title ? `<c:title>${richTextXml(payload.categoryAxis.title, payload, payload.categoryAxis)}<c:overlay val="0"/></c:title>` : ""}${textPropertiesXml(payload, payload.categoryAxis)}<c:crossAx val="222222222"/><c:crosses val="autoZero"/><c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/></c:catAx><c:valAx><c:axId val="222222222"/><c:scaling><c:orientation val="minMax"/></c:scaling><c:delete val="0"/><c:axPos val="${payload.chartType === "bar" ? "b" : "l"}"/><c:majorGridlines/>${payload.valueAxis?.title ? `<c:title>${richTextXml(payload.valueAxis.title, payload, payload.valueAxis)}<c:overlay val="0"/></c:title>` : ""}<c:numFmt formatCode="General" sourceLinked="1"/>${textPropertiesXml(payload, payload.valueAxis)}<c:crossAx val="111111111"/><c:crosses val="autoZero"/></c:valAx>`;
  const axisIds = payload.chartType === "pie" || payload.chartType === "donut"
    ? ""
    : '<c:axId val="111111111"/><c:axId val="222222222"/>';
  const plot = payload.chartType === "pie"
    ? `<c:pieChart><c:varyColors val="1"/>${seriesXml}<c:firstSliceAng val="0"/></c:pieChart>`
    : payload.chartType === "donut"
      ? `<c:doughnutChart><c:varyColors val="1"/>${seriesXml}<c:firstSliceAng val="0"/><c:holeSize val="50"/></c:doughnutChart>`
      : payload.chartType === "line"
        ? `<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>${seriesXml}<c:marker val="1"/>${axisIds}</c:lineChart>${axes}`
        : payload.chartType === "area"
          ? `<c:areaChart><c:grouping val="standard"/><c:varyColors val="0"/>${seriesXml}${axisIds}</c:areaChart>${axes}`
          : `<c:barChart><c:barDir val="${payload.chartType === "bar" ? "bar" : "col"}"/><c:grouping val="clustered"/><c:varyColors val="0"/>${seriesXml}<c:gapWidth val="120"/>${axisIds}</c:barChart>${axes}`;
  const title = payload.title
    ? `<c:title>${richTextXml(payload.title, payload, { fontSizePt: Math.max(12, payload.fontSizePt ?? 10) })}<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>`
    : '<c:autoTitleDeleted val="1"/>';
  const legend = payload.legend?.visible === false
    ? ""
    : `<c:legend><c:legendPos val="${{ bottom: "b", top: "t", right: "r", left: "l" }[payload.legend?.position ?? "bottom"]}"/><c:overlay val="0"/>${textPropertiesXml(payload, payload.legend)}</c:legend>`;
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><c:chart>${title}<c:plotArea><c:layout/>${plot}</c:plotArea>${legend}<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/><c:showDLblsOverMax val="0"/></c:chart>${textPropertiesXml(payload, undefined)}<c:printSettings><c:headerFooter/><c:pageMargins b="0.75" l="0.7" r="0.7" t="0.75" header="0.3" footer="0.3"/><c:pageSetup/></c:printSettings></c:chartSpace>`;
}
