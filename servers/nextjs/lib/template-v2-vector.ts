import type { JsonRecord } from "./template-v2-studio.ts";
import {
  sampleTemplateV2SmoothPoints,
  type TemplateV2RenderPlanNode,
} from "./template-v2-render-plan.mjs";

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

export interface TemplateV2VectorPoint {
  x: number;
  y: number;
}

export interface TemplateV2EditableVectorGeometry {
  shape: "polygon" | "ellipse" | null;
  closed: boolean;
  points: TemplateV2VectorPoint[];
  curve: {
    tension: number;
    segments: number;
    canRemove: boolean;
  } | null;
  /**
   * `[]` means the canonical field can be created without replacing authored
   * data. `null` means a legacy alias currently owns the rendered radii, so
   * the toolbar must not introduce a competing canonical field.
   */
  cornerRadii: number[] | null;
}

export type TemplateV2VectorPointTopologyAction = {
  type: "add-after";
  index: 0 | 1;
} | {
  type: "remove-proven-wrap-midpoint";
  index: 2;
};

export interface TemplateV2VectorPointTopologyEdit {
  points: JsonRecord[];
  cornerRadii: number[] | null;
  selectedIndex: number;
}

export interface TemplateV2ProvenVectorEditCapability {
  kind: "polygon-add-after-first-point";
  points: [TemplateV2VectorPoint, TemplateV2VectorPoint];
}

export interface TemplateV2ProvenVectorWrapEditCapability {
  kind: "polygon-add-after-last-wrap";
  points: [TemplateV2VectorPoint, TemplateV2VectorPoint];
}

export interface TemplateV2ProvenVectorWrapRemovalCapability {
  kind: "polygon-remove-proven-last-wrap-midpoint";
  points: [
    TemplateV2VectorPoint,
    TemplateV2VectorPoint,
    TemplateV2VectorPoint,
  ];
  removableIndex: 2;
}

const PROVEN_VECTOR_WIRE_KEYS = new Set([
  "type",
  "shape",
  "points",
  "closed",
  "upstream_element_extension",
]);

/**
 * Parses the exact two-point polygon shared by the independent first-point
 * and last-wrap upstream witnesses. Every other vector shape or field
 * combination stays fail-closed until it has its own fixed-fixture
 * round-trip contract.
 */
function templateV2ProvenVectorPoints(
  element: JsonRecord
): [TemplateV2VectorPoint, TemplateV2VectorPoint] | null {
  if (
    element.type !== "vector" ||
    element.shape !== "polygon" ||
    element.closed !== true ||
    element.upstream_element_extension !== "vector-vNext" ||
    Object.keys(element).some((key) => !PROVEN_VECTOR_WIRE_KEYS.has(key)) ||
    !Array.isArray(element.points) ||
    element.points.length !== 2
  ) {
    return null;
  }

  const points = element.points.map((point) => {
    if (
      !isRecord(point) ||
      Object.keys(point).some((key) => key !== "x" && key !== "y") ||
      typeof point.x !== "number" ||
      !Number.isFinite(point.x) ||
      typeof point.y !== "number" ||
      !Number.isFinite(point.y)
    ) {
      return null;
    }
    return { x: point.x, y: point.y };
  });
  if (points.some((point) => point === null)) return null;

  const provenPoints = points as [
    TemplateV2VectorPoint,
    TemplateV2VectorPoint,
  ];
  if (
    provenPoints[0].x !== 0 ||
    provenPoints[0].y !== 0 ||
    provenPoints[1].x !== 100 ||
    provenPoints[1].y !== 80
  ) {
    return null;
  }

  return provenPoints;
}

export function templateV2ProvenVectorEditCapability(
  element: JsonRecord
): TemplateV2ProvenVectorEditCapability | null {
  const points = templateV2ProvenVectorPoints(element);
  return points
    ? { kind: "polygon-add-after-first-point", points }
    : null;
}

/**
 * The last-to-first wrap insertion is kept as a separate capability because
 * it has its own fixed upstream round-trip witness.
 */
export function templateV2ProvenVectorWrapEditCapability(
  element: JsonRecord
): TemplateV2ProvenVectorWrapEditCapability | null {
  const points = templateV2ProvenVectorPoints(element);
  return points
    ? { kind: "polygon-add-after-last-wrap", points }
    : null;
}

/**
 * Recognizes only the exact three-point result produced by the pinned
 * last-to-first wrap insertion witness. No other point deletion is enabled.
 */
export function templateV2ProvenVectorWrapRemovalCapability(
  element: JsonRecord
): TemplateV2ProvenVectorWrapRemovalCapability | null {
  if (
    element.type !== "vector" ||
    element.shape !== "polygon" ||
    element.closed !== true ||
    element.upstream_element_extension !== "vector-vNext" ||
    Object.keys(element).some((key) => !PROVEN_VECTOR_WIRE_KEYS.has(key)) ||
    !Array.isArray(element.points) ||
    element.points.length !== 3
  ) {
    return null;
  }
  const expected = [
    { x: 0, y: 0 },
    { x: 100, y: 80 },
    { x: 50, y: 40 },
  ] as const;
  const points = element.points.map((point, index) => {
    if (
      !isRecord(point) ||
      Object.keys(point).some((key) => key !== "x" && key !== "y") ||
      point.x !== expected[index]!.x ||
      point.y !== expected[index]!.y
    ) {
      return null;
    }
    return { x: point.x, y: point.y };
  });
  if (points.some((point) => point === null)) return null;
  return {
    kind: "polygon-remove-proven-last-wrap-midpoint",
    points: points as [
      TemplateV2VectorPoint,
      TemplateV2VectorPoint,
      TemplateV2VectorPoint,
    ],
    removableIndex: 2,
  };
}

/**
 * Returns geometry that every current renderer can consume without coercion.
 * This parser is intentionally broader than the proven edit capability above:
 * successfully rendering an authored vector does not make it safe to edit.
 */
export function templateV2EditableVectorGeometry(
  element: JsonRecord
): TemplateV2EditableVectorGeometry | null {
  if (element.type !== "vector" || !Array.isArray(element.points)) return null;
  if (element.points.length < 2) return null;
  if (
    element.points.some(
      (point) =>
        !isRecord(point) ||
        typeof point.x !== "number" ||
        !Number.isFinite(point.x) ||
        typeof point.y !== "number" ||
        !Number.isFinite(point.y)
    )
  ) {
    return null;
  }

  const rawShape = element.shape;
  if (
    rawShape !== undefined &&
    rawShape !== null &&
    rawShape !== "polygon" &&
    rawShape !== "ellipse"
  ) {
    return null;
  }
  const shape = rawShape ?? null;
  if (shape === "ellipse" && element.points.length !== 2) return null;

  const rawClosed = element.closed;
  if (
    rawClosed !== undefined &&
    rawClosed !== null &&
    typeof rawClosed !== "boolean"
  ) {
    return null;
  }
  const closed =
    typeof rawClosed === "boolean" ? rawClosed : shape === "polygon";

  const rawCornerRadii = element.corner_radii ?? element.cornerRadii;
  if (
    rawCornerRadii != null &&
    (!Array.isArray(rawCornerRadii) ||
      rawCornerRadii.length > element.points.length ||
      rawCornerRadii.some(
        (radius) =>
          typeof radius !== "number" ||
          !Number.isFinite(radius) ||
          radius < 0 ||
          radius > 10000
      ))
  ) {
    return null;
  }
  const cornerRadii =
    Array.isArray(element.corner_radii)
      ? [...element.corner_radii]
      : element.cornerRadii != null
        ? null
        : [];
  const points = element.points.map((point) => ({
    x: (point as JsonRecord).x as number,
    y: (point as JsonRecord).y as number,
  }));

  const rawCurve = element.curve;
  if (rawCurve === undefined || rawCurve === null) {
    return { shape, closed, points, curve: null, cornerRadii };
  }
  if (!isRecord(rawCurve) || rawCurve.type !== "smooth") return null;

  const rawTension = rawCurve.tension;
  const tension = rawTension ?? 0.5;
  if (
    typeof tension !== "number" ||
    !Number.isFinite(tension) ||
    tension < 0 ||
    tension > 1
  ) {
    return null;
  }

  const rawSegments = rawCurve.segments;
  const segments = rawSegments ?? 16;
  if (
    typeof segments !== "number" ||
    !Number.isInteger(segments) ||
    segments < 1 ||
    segments > 96
  ) {
    return null;
  }

  return {
    shape,
    closed,
    points,
    curve: {
      tension,
      segments,
      canRemove: Object.keys(rawCurve).every((key) =>
        ["type", "tension", "segments"].includes(key)
      ),
    },
    cornerRadii,
  };
}

/**
 * Produces only the two topology edits with independent pinned upstream
 * witnesses. Any other index or topology operation remains fail-closed.
 */
export function editTemplateV2VectorPointTopology(
  element: JsonRecord,
  action: TemplateV2VectorPointTopologyAction
): TemplateV2VectorPointTopologyEdit | null {
  if (action.type === "remove-proven-wrap-midpoint") {
    const capability =
      templateV2ProvenVectorWrapRemovalCapability(element);
    if (!capability || action.index !== capability.removableIndex) {
      return null;
    }
    const points = capability.points.map((point) => ({ ...point }));
    points.splice(capability.removableIndex, 1);
    return {
      points,
      cornerRadii: null,
      selectedIndex: 1,
    };
  }

  const capability =
    action.index === 0
      ? templateV2ProvenVectorEditCapability(element)
      : action.index === 1
        ? templateV2ProvenVectorWrapEditCapability(element)
        : null;
  if (
    !capability ||
    action.type !== "add-after" ||
    !Array.isArray(element.points)
  ) {
    return null;
  }

  const [first, second] = capability.points;
  const current = action.index === 0 ? first : second;
  const next = action.index === 0 ? second : first;
  const x = current.x / 2 + next.x / 2;
  const y = current.y / 2 + next.y / 2;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const points = element.points.map((point) => ({
    ...(point as JsonRecord),
  }));
  points.splice(action.index + 1, 0, { x, y });
  return {
    points,
    cornerRadii: null,
    selectedIndex: action.index + 1,
  };
}

export function templateV2VectorCornerRadii(
  element: JsonRecord,
  pointCount: number
): number[] {
  const value = element.corner_radii ?? element.cornerRadii;
  if (!Array.isArray(value)) return [];
  return value.slice(0, pointCount).map((radius) =>
    typeof radius === "number" && Number.isFinite(radius)
      ? Math.max(0, radius)
      : 0
  );
}

function pointAt(points: TemplateV2VectorPoint[], index: number) {
  return points[((index % points.length) + points.length) % points.length];
}

function lerpPoint(
  start: TemplateV2VectorPoint,
  end: TemplateV2VectorPoint,
  t: number
): TemplateV2VectorPoint {
  return {
    x: start.x + (end.x - start.x) * t,
    y: start.y + (end.y - start.y) * t,
  };
}

export function roundedTemplateV2VectorPoints(
  points: TemplateV2VectorPoint[],
  radii: number[],
  segments = 8
): TemplateV2VectorPoint[] {
  if (points.length < 3 || radii.length === 0) return points;
  const resolvedSegments = Math.max(1, Math.min(64, Math.round(segments)));
  const rounded: TemplateV2VectorPoint[] = [];
  points.forEach((point, index) => {
    const radius = radii[index] ?? 0;
    const previous = pointAt(points, index - 1);
    const next = pointAt(points, index + 1);
    const previousDistance = Math.hypot(
      point.x - previous.x,
      point.y - previous.y
    );
    const nextDistance = Math.hypot(point.x - next.x, point.y - next.y);
    const safeRadius = Math.min(
      Math.max(0, radius),
      previousDistance / 2,
      nextDistance / 2
    );
    if (
      safeRadius <= 0 ||
      previousDistance === 0 ||
      nextDistance === 0
    ) {
      rounded.push(point);
      return;
    }
    const from = lerpPoint(point, previous, safeRadius / previousDistance);
    const to = lerpPoint(point, next, safeRadius / nextDistance);
    rounded.push(from);
    for (let step = 1; step < resolvedSegments; step += 1) {
      const t = step / resolvedSegments;
      rounded.push({
        x:
          (1 - t) * (1 - t) * from.x +
          2 * (1 - t) * t * point.x +
          t * t * to.x,
        y:
          (1 - t) * (1 - t) * from.y +
          2 * (1 - t) * t * point.y +
          t * t * to.y,
      });
    }
    rounded.push(to);
  });
  return rounded;
}

export function templateV2VectorRenderGeometry(
  element: JsonRecord,
  vector: NonNullable<TemplateV2RenderPlanNode["vector"]>
): { points: TemplateV2VectorPoint[]; width: number; height: number } {
  const plannedWidth = Math.max(1, vector.frame.width ?? 1);
  const plannedHeight = Math.max(1, vector.frame.height ?? 1);
  if (
    !vector.closed ||
    vector.shape === "ellipse" ||
    !Array.isArray(element.points)
  ) {
    return {
      points: vector.points,
      width: plannedWidth,
      height: plannedHeight,
    };
  }
  const sourcePoints = element.points.map((value) =>
    isRecord(value) &&
    typeof value.x === "number" &&
    Number.isFinite(value.x) &&
    typeof value.y === "number" &&
    Number.isFinite(value.y)
      ? { x: value.x, y: value.y }
      : null
  );
  if (sourcePoints.some((point) => point === null)) {
    return {
      points: vector.points,
      width: plannedWidth,
      height: plannedHeight,
    };
  }
  const points = sourcePoints as TemplateV2VectorPoint[];
  const radii = templateV2VectorCornerRadii(element, points.length);
  if (!radii.some((radius) => radius > 0)) {
    return {
      points: vector.points,
      width: plannedWidth,
      height: plannedHeight,
    };
  }

  let rendered = roundedTemplateV2VectorPoints(points, radii);
  if (vector.tension !== null) {
    rendered = sampleTemplateV2SmoothPoints(rendered, {
      closed: vector.closed,
      tension: vector.tension,
      segments: vector.segments ?? 16,
      path: vector.shape ?? "vector",
    });
  }
  const minX = Math.min(...rendered.map((point) => point.x));
  const minY = Math.min(...rendered.map((point) => point.y));
  const maxX = Math.max(...rendered.map((point) => point.x));
  const maxY = Math.max(...rendered.map((point) => point.y));
  return {
    points: rendered.map((point) => ({
      x: point.x - minX,
      y: point.y - minY,
    })),
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

/**
 * The shared render-plan predates dash support and rejects a non-empty dash.
 * Studio/export still use it for every other validated layout field, so provide
 * a non-mutating planning projection while the authored element remains the
 * fidelity source used by Konva and export rendering.
 */
export function projectTemplateV2ElementForRenderPlan(
  element: JsonRecord
): JsonRecord {
  let projected = element;
  const stroke = isRecord(element.stroke) ? element.stroke : null;
  if (
    element.type === "vector" &&
    stroke &&
    Array.isArray(stroke.dash) &&
    stroke.dash.length > 0
  ) {
    projected = { ...projected, stroke: { ...stroke, dash: [] } };
  }

  if (isRecord(element.child)) {
    const child = projectTemplateV2ElementForRenderPlan(element.child);
    if (child !== element.child) projected = { ...projected, child };
  }
  if (Array.isArray(element.children)) {
    let childrenChanged = false;
    const children = element.children.map((value) => {
      if (!isRecord(value)) return value;
      const child = projectTemplateV2ElementForRenderPlan(value);
      if (child !== value) childrenChanged = true;
      return child;
    });
    if (childrenChanged) projected = { ...projected, children };
  }
  return projected;
}

export function translateTemplateV2Vector(
  element: JsonRecord,
  deltaX: number,
  deltaY: number
): JsonRecord {
  if (
    element.type !== "vector" ||
    !Number.isFinite(deltaX) ||
    !Number.isFinite(deltaY) ||
    (deltaX === 0 && deltaY === 0) ||
    !Array.isArray(element.points)
  ) {
    return element;
  }
  const points = element.points.map((point) => {
    if (
      !isRecord(point) ||
      typeof point.x !== "number" ||
      !Number.isFinite(point.x) ||
      typeof point.y !== "number" ||
      !Number.isFinite(point.y)
    ) {
      return null;
    }
    return {
      ...point,
      x: round(point.x + deltaX),
      y: round(point.y + deltaY),
    };
  });
  return points.some((point) => point === null)
    ? element
    : { ...element, points };
}
