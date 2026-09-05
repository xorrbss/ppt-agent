/**
 * The promoted-element contract handed to the backplate capture pass.
 *
 * Split out of `extractor.ts`, which owns the Chrome capture itself (viewport
 * calibration, PNG normalization, paint bands). This module owns the step in
 * between: deciding which observed elements the assembler is allowed to re-emit
 * as native OOXML, and describing each one precisely enough that the browser
 * can suppress exactly the matching paint on the residual raster.
 *
 * Two invariants live here, and they are the reason the split is safe to read
 * in isolation:
 * - promotion is fail-closed. An id must already be in
 *   `backplate.eligibleElementIds`, and the element must be native or one of
 *   the narrowly admitted raster kinds (text, shape, declared chart, admitted
 *   table). Anything else throws rather than silently double-painting.
 * - `contentKey` is the identity the browser matches against. It hashes only
 *   what the native re-emission actually reproduces, so a cosmetic DOM change
 *   that does not alter the emitted shape keeps the same key.
 */
import type { AuthoredHybridExpectedPromotedElement } from "./observation.ts";
import type { AuthoredHybridSlideV1 } from "./schema.ts";
import { promotedElementEmitsOuterShadow } from "./shadow-effect.ts";

export function validatePromotedElementIds(
  slide: AuthoredHybridSlideV1,
  promotedElementIds: readonly string[]
): string[] {
  const promoted = [...promotedElementIds];
  const unique = new Set(promoted);
  if (unique.size !== promoted.length) {
    throw new Error("Authored hybrid promotedElementIds must not contain duplicates.");
  }
  const eligible = new Set(slide.backplate.eligibleElementIds);
  for (const id of promoted) {
    if (!eligible.has(id)) {
      throw new Error(
        `Authored hybrid element ${id} is unknown or raster-only and cannot be suppressed.`
      );
    }
  }
  return promoted;
}

export function promotedElementContentKey(
  element: AuthoredHybridSlideV1["elements"][number]
): string {
  if ("text" in element && element.text !== undefined) {
    return `text:${JSON.stringify({
      role: element.text.role,
      plainText: element.text.plainText,
      paragraphs: element.text.paragraphs,
      style: element.text.style,
      runs: element.text.runs.map((run) => ({
        text: run.text,
        boundsPx: run.bounds.px,
        fragmentRectsPx: run.fragments.map((fragment) => fragment.px),
        style: run.style,
        script: run.script,
        breakKind: run.breakKind,
        lineIndex: run.lineIndex,
      })),
      containerShape: element.text.containerShape
        ? {
            boundsPx: element.text.containerShape.bounds.px,
            shape: element.text.containerShape.shape,
          }
        : undefined,
    })}`;
  }
  if ("image" in element) {
    return `image:${JSON.stringify({
      src: element.image.src,
      alt: element.image.alt,
      naturalWidth: element.image.naturalWidth,
      naturalHeight: element.image.naturalHeight,
      objectFit: element.image.objectFit,
      objectPosition: element.image.objectPosition,
      crop: element.image.crop,
    })}`;
  }
  if ("shape" in element) return `shape:${JSON.stringify(element.shape)}`;
  if (
    element.classification.mode === "raster" &&
    element.classification.candidateKind === "complex"
  ) {
    return `complex:${JSON.stringify({
      fallbackReasons: [...element.classification.reasons].sort(),
      tableEvidence: element.tableEvidence ?? null,
    })}`;
  }
  throw new Error(`Authored hybrid element ${element.id} is not native.`);
}

export function expectedPromotedElements(
  slide: AuthoredHybridSlideV1,
  promotedElementIds: readonly string[]
): AuthoredHybridExpectedPromotedElement[] {
  const byId = new Map(slide.elements.map((element) => [element.id, element]));
  return promotedElementIds.map((id) => {
    const element = byId.get(id);
    const rasterText =
      element?.classification.mode === "raster" &&
      element.classification.candidateKind === "text" &&
      "text" in element &&
      element.text !== undefined;
    const rasterShape =
      element?.classification.mode === "raster" &&
      element.classification.candidateKind === "shape" &&
      "shape" in element &&
      element.shape !== undefined;
    const rasterDeclaredChart =
      element?.identityAuthority === "declared-qid" &&
      element.classification.mode === "raster" &&
      element.classification.candidateKind === "complex" &&
      element.classification.reasons.includes("complex-chart");
    // A table is promotable when the server admitted it, either through the
    // declared semantic payload or through the markup-driven observed path;
    // both record the admitted id in backplate.nativeTableElementIds.
    const rasterDeclaredTable =
      (element?.identityAuthority === "declared-qid" ||
        (slide.backplate.nativeTableElementIds ?? []).includes(id)) &&
      element?.classification.mode === "raster" &&
      element.classification.candidateKind === "complex" &&
      element.classification.reasons.includes("complex-table");
    if (
      !element ||
      (element.classification.mode !== "native" && !rasterText &&
        !rasterShape && !rasterDeclaredChart && !rasterDeclaredTable)
    ) {
      throw new Error(`Authored hybrid element ${id} is not a promotable candidate.`);
    }
    return {
      id: element.id,
      identityAuthority: element.identityAuthority,
      domPath: element.domPath,
      tagName: element.tagName,
      sourceIndex: element.sourceIndex,
      candidateKind:
        element.classification.mode === "native"
          ? element.classification.kind
          : rasterText
            ? "text"
            : rasterShape
              ? "shape"
              : "complex",
      boundsPx: element.bounds.px,
      rotationDeg: element.rotationDeg,
      opacity: element.opacity,
      contentKey: promotedElementContentKey(element),
      // Suppress glyph paint only. Hiding a styled text container wholesale can
      // also hide inline SVG/icon descendants that must remain on the backplate.
      suppressWholeElement: rasterDeclaredChart || rasterDeclaredTable,
      suppressContainerPaint: Boolean("text" in element && element.text?.containerShape),
      suppressOuterShadow: promotedElementEmitsOuterShadow(element),
    };
  });
}
