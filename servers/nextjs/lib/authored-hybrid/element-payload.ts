/**
 * Element payload discrimination for the authored-hybrid H2 contract.
 *
 * Split out of `contract.ts`, which owns validation and (de)serialization of a
 * whole slide. These are the narrowing predicates that answer a different
 * question: given one observed element, which optional payload does it
 * actually carry, and is a rasterized shape still safe to re-emit as editable
 * OOXML geometry?
 *
 * `AuthoredHybridElement` declares `text` and `shape` as optional siblings, so
 * every consumer needs the same guards to reach either payload without an
 * unchecked cast. Keeping the unsafe-reason set beside `isEditableRasterShapePayload`
 * makes the fail-closed rule readable in one place: a shape that fell back to
 * raster for any reason that changes how it is painted (clipping, masking,
 * filters, blending, non-rigid transforms, animation) can never be promoted
 * back to a native shape, because the vector re-emission would not reproduce
 * what Chrome rasterized.
 */
import type {
  AuthoredHybridElement,
  AuthoredHybridShapePayload,
  AuthoredHybridTextPayload,
} from "./schema.ts";

export function hasTextPayload(
  element: AuthoredHybridElement
): element is AuthoredHybridElement & { text: AuthoredHybridTextPayload } {
  return "text" in element && element.text !== undefined;
}

export function hasShapePayload(
  element: AuthoredHybridElement
): element is AuthoredHybridElement & { shape: AuthoredHybridShapePayload } {
  return "shape" in element && element.shape !== undefined;
}

const UNSAFE_EDITABLE_SHAPE_REASONS = new Set([
  "extraction-error",
  "invalid-bounds",
  "outside-slide",
  "clip-path",
  "mask",
  "filter",
  "backdrop-filter",
  "mix-blend-mode",
  "complex-transform",
  "transformed-ancestor",
  "animated",
  "unsupported-background",
  "unsupported-opacity",
]);

export function isEditableRasterShapePayload(
  element: AuthoredHybridElement
): element is AuthoredHybridElement & { shape: AuthoredHybridShapePayload } {
  return (
    element.classification.mode === "raster" &&
    element.classification.candidateKind === "shape" &&
    hasShapePayload(element) &&
    !element.classification.reasons.some((reason) =>
      UNSAFE_EDITABLE_SHAPE_REASONS.has(reason)
    ) &&
    Boolean(
      element.shape.fill ||
      element.shape.gradient ||
      element.shape.stroke ||
      element.shape.borderLines?.length ||
      element.shape.shadowLayers?.length
    )
  );
}
