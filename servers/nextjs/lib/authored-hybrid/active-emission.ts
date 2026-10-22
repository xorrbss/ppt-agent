import type { DeclaredSemanticElementForObservation } from "./semantic-reconciliation.ts";

type CanonicalMode = "legacyHtml" | "bundleGraph";
type BundleKind = "authored" | "legacyHtml";

interface ParsedBundleV1 {
  readonly bundleKind: BundleKind;
  readonly presentationId: string;
  readonly slideId: string;
  readonly slideRevision: number;
  readonly bundleDigest: string;
  readonly fontManifestDigest: string;
  readonly semanticKind:
    | "authoredSemanticSource"
    | "semanticUnavailable"
    | "compatibilityObservation";
  readonly declaredElementIds: readonly string[];
  readonly declaredElements: readonly DeclaredSemanticElementForObservation[];
  readonly fontFamilies: readonly string[];
}

export interface ActiveEmissionSlideBinding {
  readonly slideId: string;
  readonly slideRevision: number;
  readonly bundleKind: BundleKind;
  readonly bundleDigest: string;
  readonly fontManifestDigest: string;
  readonly semanticKind: ParsedBundleV1["semanticKind"];
  readonly declaredElementIds: readonly string[];
  readonly declaredElements: readonly DeclaredSemanticElementForObservation[];
  readonly fontFamilies: readonly string[];
}

export interface ActiveEmissionExportBinding {
  readonly schemaVersion: "authored-active-export-binding/browser-v1";
  readonly canonicalMode: CanonicalMode;
  readonly presentationRevision: number;
  readonly activeGeneration: number;
  readonly canonicalDigest: string | null;
  readonly sceneDigest: string | null;
  readonly slides: readonly ActiveEmissionSlideBinding[];
}
