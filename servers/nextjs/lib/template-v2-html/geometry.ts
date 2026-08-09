import type { Box, JsonRecord, Point } from "./types";
import { readArray, readNumber, readRecord, readRecordOrNull, readString } from "./values";

export function polygonSourcePoints(item: JsonRecord): Point[] {
  return readArray(item.points)
    .map(readRecord)
    .map((point) => {
      const x = readNumber(point.x);
      const y = readNumber(point.y);
      return x != null && y != null ? { x, y } : null;
    })
    .filter((point): point is Point => point != null);
}

export function vectorShape(item: JsonRecord): "polygon" | "ellipse" {
  return readString(item.shape) === "ellipse" ? "ellipse" : "polygon";
}

export function polygonPoints(item: JsonRecord): Point[] {
  const points = polygonSourcePoints(item);
  if (readString(item.type) === "vector" && vectorShape(item) === "ellipse") {
    return points;
  }
  const closed = polygonClosed(item, points);
  const rounded = closed
    ? roundedPolygonPoints(points, cornerRadii(item, points.length))
    : points;
  const curve = curveSettings(item);
  if (!curve) return rounded;
  return sampleSmoothCurve(rounded, closed, curve.tension, curve.segments);
}

function cornerRadii(item: JsonRecord, pointCount: number): number[] {
  return readArray(item.corner_radii ?? item.cornerRadii)
    .map(readNumber)
    .filter((value): value is number => value != null)
    .slice(0, pointCount)
    .map((value) => Math.max(0, value));
}

function pointAt(points: Point[], index: number) {
  return points[((index % points.length) + points.length) % points.length];
}

function lerpPoint(start: Point, end: Point, t: number): Point {
  return {
    x: start.x + (end.x - start.x) * t,
    y: start.y + (end.y - start.y) * t,
  };
}

function roundedPolygonPoints(points: Point[], radii: number[], segments = 8): Point[] {
  if (points.length < 3 || radii.length === 0) return points;
  const rounded: Point[] = [];
  points.forEach((point, index) => {
    const radius = radii[index] ?? 0;
    const previous = pointAt(points, index - 1);
    const next = pointAt(points, index + 1);
    const prevDistance = Math.hypot(point.x - previous.x, point.y - previous.y);
    const nextDistance = Math.hypot(point.x - next.x, point.y - next.y);
    const safeRadius = Math.min(radius, prevDistance / 2, nextDistance / 2);
    if (safeRadius <= 0) {
      rounded.push(point);
      return;
    }
    const from = lerpPoint(point, previous, safeRadius / prevDistance);
    const to = lerpPoint(point, next, safeRadius / nextDistance);
    rounded.push(from);
    for (let step = 1; step < segments; step += 1) {
      const t = step / segments;
      rounded.push({
        x: (1 - t) * (1 - t) * from.x + 2 * (1 - t) * t * point.x + t * t * to.x,
        y: (1 - t) * (1 - t) * from.y + 2 * (1 - t) * t * point.y + t * t * to.y,
      });
    }
    rounded.push(to);
  });
  return rounded;
}

function curveSettings(item: JsonRecord) {
  const curve = readRecordOrNull(item.curve);
  if (!curve) return null;
  const rawType = readString(curve.type)?.trim().toLowerCase();
  if (rawType !== "smooth") return null;
  const rawTension = curve.tension;
  const tension = rawTension == null ? 0.5 : rawTension;
  const rawSegments = curve.segments;
  const segments = rawSegments == null ? 16 : rawSegments;
  if (
    typeof tension !== "number" ||
    !Number.isFinite(tension) ||
    tension < 0 ||
    tension > 1 ||
    typeof segments !== "number" ||
    !Number.isInteger(segments) ||
    segments < 1 ||
    segments > 96
  ) {
    return null;
  }
  return {
    type: "smooth",
    tension,
    segments,
  };
}

function hermitePoint(
  start: Point,
  end: Point,
  startTangent: Point,
  endTangent: Point,
  t: number
): Point {
  const t2 = t * t;
  const t3 = t2 * t;
  const h00 = 2 * t3 - 3 * t2 + 1;
  const h10 = t3 - 2 * t2 + t;
  const h01 = -2 * t3 + 3 * t2;
  const h11 = t3 - t2;
  return {
    x:
      h00 * start.x +
      h10 * startTangent.x +
      h01 * end.x +
      h11 * endTangent.x,
    y:
      h00 * start.y +
      h10 * startTangent.y +
      h01 * end.y +
      h11 * endTangent.y,
  };
}

function sampleSmoothCurve(points: Point[], closed: boolean, tension: number, segments: number): Point[] {
  if (points.length < 3 || tension <= 0) return points;
  const sampled: Point[] = [];
  const segmentCount = closed ? points.length : points.length - 1;
  for (let index = 0; index < segmentCount; index += 1) {
    const p0 = closed ? pointAt(points, index - 1) : points[Math.max(0, index - 1)];
    const p1 = pointAt(points, index);
    const p2 = pointAt(points, index + 1);
    const p3 = closed ? pointAt(points, index + 2) : points[Math.min(points.length - 1, index + 2)];
    if (index === 0) sampled.push(p1);
    const tangentScale = tension * 0.5;
    const startTangent = {
      x: (p2.x - p0.x) * tangentScale,
      y: (p2.y - p0.y) * tangentScale,
    };
    const endTangent = {
      x: (p3.x - p1.x) * tangentScale,
      y: (p3.y - p1.y) * tangentScale,
    };
    for (let step = 1; step <= segments; step += 1) {
      sampled.push(
        hermitePoint(p1, p2, startTangent, endTangent, step / segments)
      );
    }
  }
  return sampled;
}

export function polygonClosed(item: JsonRecord, points: Point[]): boolean {
  if (readString(item.type) === "vector" && vectorShape(item) === "ellipse") {
    return true;
  }
  const value = item.closed;
  if (value === false || value === "false" || value === "0") return false;
  if (value === true || value === "true" || value === "1") return true;
  if (readString(item.type) === "vector") {
    return vectorShape(item) === "polygon";
  }
  return points.length > 2;
}

export function polygonBox(item: JsonRecord, points: Point[]): Box {
  if (points.length === 0) {
    return { x: 0, y: 0, width: 1, height: 1 };
  }

  const minX = Math.min(...points.map((point) => point.x));
  const minY = Math.min(...points.map((point) => point.y));
  const maxX = Math.max(...points.map((point) => point.x));
  const maxY = Math.max(...points.map((point) => point.y));
  const stroke = readRecord(item.stroke);
  const strokeWidth = Math.max(1, readNumber(stroke.width) ?? 1);
  return {
    x: minX,
    y: minY,
    width: Math.max(maxX - minX, strokeWidth, 1),
    height: Math.max(maxY - minY, strokeWidth, 1),
  };
}
