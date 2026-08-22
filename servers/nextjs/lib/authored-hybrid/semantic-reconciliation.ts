import { createHash } from "node:crypto";

import type {
  BrowserAuthoredHybridObservation,
  BrowserElementObservation,
} from "./observation.ts";

const QID = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/;
const DIGEST = /^sha256:[a-f0-9]{64}$/;

export type DeclaredSemanticKind =
  | "text"
  | "image"
  | "shape"
  | "connector"
  | "group"
  | "table"
  | "chart"
  | "complexIsland";

export interface DeclaredSemanticElementForObservation {
  readonly elementId: string;
  readonly kind: DeclaredSemanticKind;
  readonly parentId?: string | null;
  readonly childIds?: readonly string[];
  readonly sourceDigest: string;
  readonly assetContentDigest?: string;
  readonly requiredFeatures?: readonly string[];
}

export interface BrowserObservationReceiptElementV1 {
  readonly elementId: string;
  readonly bounds: {
    readonly xPt: number;
    readonly yPt: number;
    readonly widthPt: number;
    readonly heightPt: number;
    readonly rotationDeg: number;
  };
  readonly parentId: string | null;
  readonly sourceDigest: string;
  readonly paintDigest: string;
  readonly computedFontFamilies: readonly string[];
  readonly lineRectDigests: readonly string[];
  readonly observedFeatures: readonly string[];
  /** Diagnostics only. Never used as declared identity authority. */
  readonly ordinal: number;
  /** Diagnostics only. Never used as declared identity authority. */
  readonly domPath: string;
}

export interface BrowserObservationReceiptV1 {
  readonly schemaVersion: "browser-observation-receipt/v1";
  readonly inputBundleDigest: string;
  readonly rendererSourceDigest: string;
  readonly rendererVersion: string;
  readonly policyVersion: string;
  readonly fontSetDigest: string;
  readonly fontsReady: boolean;
  readonly imagesReady: boolean;
  readonly unresolvedResourceDigests: readonly string[];
  readonly mutationFreeWindowMs: number;
  readonly captureEpoch: number;
  readonly capturedPngDigest: string;
  readonly elements: readonly BrowserObservationReceiptElementV1[];
  readonly receiptDigest: string;
}

export interface DeclaredObservationReconciliation {
  readonly publicationStatus: "accepted";
  readonly elements: ReadonlyArray<{
    readonly elementId: string;
    readonly declaredKind: DeclaredSemanticKind;
    readonly observedKind: BrowserElementObservation["candidateKind"];
    readonly state: "matched" | "rasterFallback";
    readonly fallbackReasons: readonly string[];
  }>;
}

export class AuthoredSemanticPublicationError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "AuthoredSemanticPublicationError";
    this.code = code;
  }
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonical(record[key])}`
  ).join(",")}}`;
}

function digest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
}

function requireDigest(value: string, field: string): void {
  if (!DIGEST.test(value)) throw new Error(`${field}_invalid`);
}

function observationKindMatches(
  declared: DeclaredSemanticKind,
  observed: BrowserElementObservation["candidateKind"]
): boolean {
  if (declared === observed) return true;
  return new Set(["connector", "group", "table", "chart", "complexIsland"]).has(
    declared
  ) && observed === "complex";
}

function validateDeclaredGraph(
  declaredElements: readonly DeclaredSemanticElementForObservation[]
): Map<string, DeclaredSemanticElementForObservation> {
  const byId = new Map<string, DeclaredSemanticElementForObservation>();
  for (const element of declaredElements) {
    if (!QID.test(element.elementId)) {
      throw new AuthoredSemanticPublicationError("authored_semantic_qid_invalid");
    }
    if (byId.has(element.elementId)) {
      throw new AuthoredSemanticPublicationError("authored_semantic_qid_duplicate");
    }
    requireDigest(element.sourceDigest, "authored_semantic_source_digest");
    if (element.assetContentDigest) {
      requireDigest(element.assetContentDigest, "authored_semantic_asset_digest");
    }
    byId.set(element.elementId, element);
  }
  for (const element of declaredElements) {
    const childIds = [...(element.childIds ?? [])];
    if (new Set(childIds).size !== childIds.length) {
      throw new AuthoredSemanticPublicationError("authored_semantic_ownership_mismatch");
    }
    if (element.parentId) {
      const parent = byId.get(element.parentId);
      if (!parent || !(parent.childIds ?? []).includes(element.elementId)) {
        throw new AuthoredSemanticPublicationError("authored_semantic_ownership_mismatch");
      }
    }
    for (const childId of childIds) {
      if (byId.get(childId)?.parentId !== element.elementId) {
        throw new AuthoredSemanticPublicationError("authored_semantic_ownership_mismatch");
      }
    }
  }
  return byId;
}

export function reconcileDeclaredObservation(
  declaredElements: readonly DeclaredSemanticElementForObservation[],
  observation: BrowserAuthoredHybridObservation,
  observedAssetContentDigests: Readonly<Record<string, string>> = {}
): DeclaredObservationReconciliation {
  const declared = validateDeclaredGraph(declaredElements);
  const observed = new Map<string, BrowserElementObservation>();
  for (const element of observation.elements) {
    if (element.identityAuthority !== "declared-qid" || observed.has(element.id)) {
      throw new AuthoredSemanticPublicationError("authored_semantic_qid_mismatch");
    }
    observed.set(element.id, element);
  }
  if (
    observed.size !== declared.size ||
    [...declared.keys()].some((elementId) => !observed.has(elementId)) ||
    [...observed.keys()].some((elementId) => !declared.has(elementId))
  ) {
    throw new AuthoredSemanticPublicationError("authored_semantic_qid_mismatch");
  }

  const elements = declaredElements.map((source) => {
    const actual = observed.get(source.elementId)!;
    if ((actual.parentElementId ?? null) !== (source.parentId ?? null)) {
      throw new AuthoredSemanticPublicationError("authored_semantic_ownership_mismatch");
    }
    if (source.assetContentDigest) {
      const actualAssetDigest = observedAssetContentDigests[source.elementId];
      if (!actualAssetDigest || actualAssetDigest !== source.assetContentDigest) {
        throw new AuthoredSemanticPublicationError("authored_semantic_asset_mismatch");
      }
    }
    const fallbackReasons = [
      ...actual.fallbackReasons,
      ...(observationKindMatches(source.kind, actual.candidateKind)
        ? []
        : ["declared-observed-kind-mismatch"]),
    ];
    return {
      elementId: source.elementId,
      declaredKind: source.kind,
      observedKind: actual.candidateKind,
      state: fallbackReasons.length ? "rasterFallback" as const : "matched" as const,
      fallbackReasons: [...new Set(fallbackReasons)].sort(),
    };
  });
  return { publicationStatus: "accepted", elements };
}

function lineRectDigests(element: BrowserElementObservation): string[] {
  return (element.text?.layout?.lineRectsPx ?? []).map((rect) => digest(rect));
}

function paintEvidence(element: BrowserElementObservation): unknown {
  return {
    boundsPx: element.boundsPx,
    rotationDeg: element.rotationDeg,
    opacity: element.opacity,
    cssZIndex: element.cssZIndex,
    fallbackReasons: [...element.fallbackReasons].sort(),
    text: element.text ? {
      style: element.text.style,
      runs: element.text.runs.map((run) => ({
        boundsPx: run.boundsPx,
        fragmentRectsPx: run.fragmentRectsPx,
        style: run.style,
        script: run.script,
        breakKind: run.breakKind,
      })),
    } : undefined,
    image: element.image,
    shape: element.shape,
  };
}

export function buildBrowserObservationReceiptV1(
  declaredElements: readonly DeclaredSemanticElementForObservation[],
  observation: BrowserAuthoredHybridObservation,
  context: {
    readonly inputBundleDigest: string;
    readonly rendererSourceDigest: string;
    readonly rendererVersion: string;
    readonly policyVersion: string;
    readonly fontSetDigest: string;
    readonly capturedPngDigest: string;
    readonly observedAssetContentDigests?: Readonly<Record<string, string>>;
  }
): BrowserObservationReceiptV1 {
  reconcileDeclaredObservation(
    declaredElements,
    observation,
    context.observedAssetContentDigests
  );
  const readiness = observation.renderReadiness;
  if (!readiness || readiness.status !== "ready") {
    throw new Error("authored_browser_observation_not_ready");
  }
  [
    context.inputBundleDigest,
    context.rendererSourceDigest,
    context.fontSetDigest,
    context.capturedPngDigest,
  ].forEach((value, index) => requireDigest(value, `browser_observation_digest_${index}`));
  const declared = new Map(declaredElements.map((element) => [element.elementId, element]));
  const elements = observation.elements.map((element, ordinal) => {
    const source = declared.get(element.id)!;
    const computedFontFamilies = element.text
      ? [...new Set([
          ...element.text.style.fontFamilies,
          ...element.text.runs.flatMap((run) => run.style.fontFamilies),
        ])]
      : [];
    const observedFeatures = [...new Set([
      ...element.fallbackReasons,
      ...(source.requiredFeatures ?? []),
    ])].sort();
    return {
      elementId: element.id,
      bounds: {
        xPt: element.boundsPx.x * 0.75,
        yPt: element.boundsPx.y * 0.75,
        widthPt: element.boundsPx.width * 0.75,
        heightPt: element.boundsPx.height * 0.75,
        rotationDeg: element.rotationDeg,
      },
      parentId: element.parentElementId ?? null,
      sourceDigest: source.sourceDigest,
      paintDigest: digest(paintEvidence(element)),
      computedFontFamilies,
      lineRectDigests: lineRectDigests(element),
      observedFeatures,
      ordinal,
      domPath: element.domPath,
    } satisfies BrowserObservationReceiptElementV1;
  });
  const unsigned = {
    schemaVersion: "browser-observation-receipt/v1" as const,
    inputBundleDigest: context.inputBundleDigest,
    rendererSourceDigest: context.rendererSourceDigest,
    rendererVersion: context.rendererVersion,
    policyVersion: context.policyVersion,
    fontSetDigest: context.fontSetDigest,
    fontsReady: readiness.fontStatus === "loaded" || readiness.fontStatus === "unsupported",
    imagesReady: readiness.decodedImageCount === readiness.imageCount,
    unresolvedResourceDigests: readiness.unresolvedResources.map((resource) => digest(resource)),
    mutationFreeWindowMs: readiness.mutationFreeWindowMs,
    captureEpoch: Math.max(1, readiness.captureEpoch),
    capturedPngDigest: context.capturedPngDigest,
    elements,
  };
  return { ...unsigned, receiptDigest: digest(unsigned) };
}
