/**
 * Canonical semantic-digest helpers and the semantic slide context shared by
 * the authored-hybrid slide layer.
 *
 * Split out of `slide-layer.ts` to keep that module within the repository
 * file-size budget. `slide-layer.ts` re-exports every public name here, so
 * importers keep using that module.
 */

import { createHash } from "node:crypto";

import type { PresentationExportFallbackElement } from "../presentation-export-quality.ts";
import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";

export interface AuthoredSemanticSlideContext {
  readonly exportAttemptId: string;
  readonly inputBundleDigest: string;
  readonly rendererSourceDigest: string;
  readonly fontSetDigest: string;
  readonly declaredElementIds: readonly string[];
  readonly declaredElements: readonly DeclaredSemanticElementForObservation[];
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`
  ).join(",")}}`;
}

export function semanticDigest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
}

export function normalizeDigest(value: string): string {
  return value.startsWith("sha256:") ? value : `sha256:${value}`;
}

export function semanticRasterDegradationDigest(
  context: Pick<AuthoredSemanticSlideContext, "inputBundleDigest" | "rendererSourceDigest">,
  slideNumber: number,
  reason: string,
): string {
  return semanticDigest({
    schemaVersion: "hybrid-raster-degradation-receipt/browser-v1",
    inputBundleDigest: context.inputBundleDigest,
    rendererSourceDigest: normalizeDigest(context.rendererSourceDigest),
    slideNumber,
    state: "degradedFidelityOnly",
    reason,
  });
}

export function semanticRasterOwnerQids(
  fallbackElements: readonly Pick<PresentationExportFallbackElement, "elementId">[],
  nativeElementIds: ReadonlySet<string>,
): string[] {
  return fallbackElements
    .map((element) => element.elementId)
    .filter((elementId) => !nativeElementIds.has(elementId))
    .sort();
}
