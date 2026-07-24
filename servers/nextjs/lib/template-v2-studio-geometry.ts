import {
  elementCapabilities,
  elementPosition,
  elementRotation,
  elementSize,
  normalizeElementGeometry,
} from "./template-v2-konva.ts";
import type { ElementGeometry, JsonRecord } from "./template-v2-studio.ts";

export type TemplateV2GeometryField =
  | "x"
  | "y"
  | "width"
  | "height"
  | "rotation";

export function currentTemplateV2Geometry(
  element: JsonRecord
): ElementGeometry {
  const position = elementPosition(element);
  const size = elementSize(element);
  return normalizeElementGeometry(element, {
    ...position,
    ...size,
    rotation: elementRotation(element),
  });
}

export function updateTemplateV2GeometryField(
  element: JsonRecord,
  field: TemplateV2GeometryField,
  rawValue: string
): ElementGeometry | null {
  const capabilities = elementCapabilities(element);
  const supported =
    capabilities.move &&
    ((field === "x" || field === "y") ||
      (capabilities.resize && (field === "width" || field === "height")) ||
      (capabilities.rotate && field === "rotation"));
  if (!supported || rawValue.trim() === "") return null;

  const value = Number(rawValue);
  if (!Number.isFinite(value)) return null;

  const geometry = currentTemplateV2Geometry(element);
  return normalizeElementGeometry(element, {
    x: field === "x" ? value : geometry.x,
    y: field === "y" ? value : geometry.y,
    width: field === "width" ? value : geometry.width,
    height: field === "height" ? value : geometry.height,
    rotation: field === "rotation" ? value : geometry.rotation,
  });
}
