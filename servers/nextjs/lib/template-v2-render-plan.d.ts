export interface TemplateV2PlanFrame {
  x: number;
  y: number;
  width: number | null;
  height: number | null;
}

export interface TemplateV2RenderPlanNode {
  type: string;
  path: string;
  frame: TemplateV2PlanFrame;
  absoluteFrame: TemplateV2PlanFrame;
  rotation: number;
  children: TemplateV2RenderPlanNode[];
  text?: TemplateV2PlannedText;
  container?: TemplateV2PlannedContainer;
  image?: TemplateV2PlannedImage;
  vector?: {
    shape: string | null;
    closed: boolean;
    tension: number | null;
    segments: number | null;
    frame: TemplateV2PlanFrame;
    points: Array<{ x: number; y: number }>;
  };
  chart?: {
    type: string;
    categories: string[];
    series: Array<{ name: string; values: number[] }>;
    colors: string[];
    title: string | null;
    titleColor: string;
    legendColor: string;
    axisColor: string;
    gridColor: string;
    xAxis: boolean;
    yAxis: boolean;
    xAxisTitle: string | null;
    yAxisTitle: string | null;
    xAxisGrid: boolean;
    yAxisGrid: boolean;
    dataLabels: "base" | "mid" | "top" | "outside" | null;
    legend: boolean;
    source: string | null;
    horizontal: boolean;
    stacked: boolean;
  };
  infographic?: {
    type: string;
    minimum: number;
    maximum: number;
    value: number;
    ratio: number;
    colors: string[];
  };
}

export interface TemplateV2PlannedShadow {
  color: string;
  blur: number;
  opacity: number;
  offsetX: number;
  offsetY: number;
}

export interface TemplateV2PlannedStroke {
  color: string;
  width: number;
  opacity: number;
}

export interface TemplateV2PlannedAlignment {
  horizontal: "left" | "center" | "right" | null;
  vertical: "top" | "middle" | "bottom" | null;
}

export interface TemplateV2PlannedText {
  alignment: TemplateV2PlannedAlignment | null;
  stroke: TemplateV2PlannedStroke | null;
  shadow: TemplateV2PlannedShadow | null;
}

export interface TemplateV2PlannedContainer extends TemplateV2PlannedText {}

export interface TemplateV2PlannedImage {
  fit: "contain" | "cover" | "fill";
  flipH: boolean;
  flipV: boolean;
  opacity: number;
  focusX: number;
  focusY: number;
  cropScale: number;
  clipPath: string | null;
  color: string | null;
  isIcon: boolean;
}

export const TEMPLATE_V2_PLAN_ELEMENT_TYPES: readonly string[];
export const TEMPLATE_V2_PLAN_CHART_TYPES: readonly string[];

export function isTemplateV2SafeColor(value: unknown): value is string;

export function sampleTemplateV2SmoothPoints(
  points: unknown,
  options?: {
    closed?: boolean;
    tension?: number;
    segments?: number;
    path?: string;
  }
): Array<{ x: number; y: number }>;

export function createTemplateV2SlideRenderPlan(
  slide: unknown,
  options?: { pathPrefix?: string }
): {
  canvas: { width: number; height: number };
  components: Array<{
    id: string;
    path: string;
    frame: TemplateV2PlanFrame;
    elements: TemplateV2RenderPlanNode[];
  }>;
};

export function assertTemplateV2PlanClosedVector(vectorPlan: unknown): boolean;
