export type JsonRecord = Record<string, unknown>;
export type RenderMode = "absolute" | "flow";
export type DataLabelPosition = "base" | "mid" | "top" | "outside";
export type ChartKind =
  | "bar"
  | "bubble"
  | "horizontal_bar"
  | "horizontal_stacked_bar"
  | "stacked_bar"
  | "line"
  | "area"
  | "pie"
  | "donut"
  | "polar_area"
  | "radar"
  | "scatter";
export type InfographicKind = "progress_bar" | "gauge";

export type JsonToHtmlItem = JsonRecord;

export interface ChartPointData {
  x: number;
  y: number;
  r?: number;
}

export interface ChartSeriesData {
  name: string;
  points: ChartPointData[];
  values: number[];
}

export interface NormalizedChartData {
  categories: string[];
  colors: string[];
  series: ChartSeriesData[];
}

export interface Box {
  x: number;
  y: number;
  width?: number;
  height?: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface FontFaceDefinition {
  family: string;
  url: string;
  format?: string;
  weight?: string;
  style?: string;
}

export interface TemplateV2HtmlOptions {
  fonts?: unknown;
  width?: number;
  height?: number;
}

export interface TemplateV2RenderPayload {
  items: JsonToHtmlItem[];
  width: number;
  height: number;
  fonts?: unknown;
  background: string;
}
