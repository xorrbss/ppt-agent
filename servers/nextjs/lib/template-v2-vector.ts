import type { JsonRecord } from "./template-v2-studio.ts";

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
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
