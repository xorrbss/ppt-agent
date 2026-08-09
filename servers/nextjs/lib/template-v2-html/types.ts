import type {
  TemplateV2ChartType,
  TemplateV2DataLabelPosition,
  TemplateV2InfographicType,
} from "./template-v2-contract.generated";

export type JsonRecord = Record<string, unknown>;
export type RenderMode = "absolute" | "flow";
export type DataLabelPosition = TemplateV2DataLabelPosition;
export type ChartKind = TemplateV2ChartType;
export type InfographicKind = TemplateV2InfographicType;

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
