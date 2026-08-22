/** Browser-side projections of the immutable authored semantic v1 contracts. */

export type DigestV1 = `sha256:${string}`;
export type QidV1 = string;

export interface ElementIdentityRegistryV1 {
  readonly schemaVersion: "authored-element-identity-registry/v1";
  readonly activeQids: readonly QidV1[];
  readonly retiredQids: readonly QidV1[];
  readonly lineageDigest: DigestV1 | null;
  readonly digest: DigestV1 | null;
}

export interface AuthoredSemanticElementV1 {
  readonly elementId: QidV1;
  readonly semanticRole: string;
  readonly parentId: QidV1 | null;
  readonly childIds: readonly QidV1[];
  readonly intendedOrder: number;
  readonly intendedBounds: Readonly<Record<string, unknown>> | null;
  readonly requiredFeatures: readonly string[];
  readonly kind:
    | "text"
    | "image"
    | "shape"
    | "connector"
    | "group"
    | "table"
    | "chart"
    | "complexIsland";
  readonly payload?: Readonly<Record<string, unknown>>;
  readonly sourceRef?: DigestV1;
  readonly rasterReason?: string | null;
}

export interface AuthoredSemanticSourceV1 {
  readonly schemaVersion: "authored-semantic-source/v1";
  readonly componentKind: "authoredSemanticSource";
  readonly presentationId: string;
  readonly slideId: string;
  readonly slideRevision: number;
  readonly operationId: string;
  readonly origin: "authored-declared-v1";
  readonly authoredHtmlDigest: DigestV1;
  readonly elements: readonly AuthoredSemanticElementV1[];
  readonly identityRegistry: ElementIdentityRegistryV1;
  readonly digest: DigestV1 | null;
}

export interface SemanticUnavailableReceiptV1 {
  readonly schemaVersion: "semantic-unavailable-receipt/v1";
  readonly componentKind: "semanticUnavailable";
  readonly presentationId: string;
  readonly slideId: string;
  readonly slideRevision: number;
  readonly operationId: string;
  readonly originGenerationAttemptId: string | null;
  readonly authoredHtmlDigest: DigestV1;
  readonly state: "degradedFidelityOnly";
  readonly failureCode: string;
  readonly boundedAttemptCount: number;
  readonly compilerVersion: string;
  readonly policyVersion: string;
  readonly digest: DigestV1 | null;
}

export interface CompatibilitySemanticObservationV1 {
  readonly schemaVersion: "compatibility-semantic-observation/v1";
  readonly componentKind: "compatibilityObservation";
  readonly presentationId: string;
  readonly slideId: string;
  readonly slideRevision: number;
  readonly operationId: string;
  readonly authoredHtmlDigest: DigestV1;
  readonly extractorVersion: string;
  readonly orderedRemapDigest: DigestV1 | null;
  readonly elements: readonly Readonly<Record<string, unknown>>[];
  readonly digest: DigestV1 | null;
}

export type AuthoredSemanticComponentV1 =
  | AuthoredSemanticSourceV1
  | SemanticUnavailableReceiptV1;
export type LegacyCompatibilityComponentV1 =
  | CompatibilitySemanticObservationV1
  | SemanticUnavailableReceiptV1;

export interface ArtifactPointerV1 {
  readonly schemaVersion: "authored-artifact-pointer/v1";
  readonly artifactRef: string;
  readonly digest: DigestV1;
  readonly byteLength: number;
  readonly mediaType: string;
}

interface EmissionBundleBaseV1 {
  readonly presentationId: string;
  readonly slideId: string;
  readonly slideRevision: number;
  readonly operationId: string;
  readonly originGenerationAttemptId: string | null;
  readonly authoredHtml: ArtifactPointerV1;
  readonly assetManifest: ArtifactPointerV1;
  readonly fontManifest: ArtifactPointerV1;
  readonly masterContractDigest: DigestV1;
  readonly compositionDigest: DigestV1;
  readonly elementIdentityRegistryDigest: DigestV1;
  readonly qualityPolicyVersion: string;
  readonly bundleDigest: DigestV1;
}

export interface AuthoredEmissionBundleV1 extends EmissionBundleBaseV1 {
  readonly schemaVersion: "authored-emission-bundle/v1";
  readonly bundleKind: "authored";
  readonly semanticComponent: AuthoredSemanticComponentV1;
}

export interface LegacyHtmlBundleV1 extends EmissionBundleBaseV1 {
  readonly schemaVersion: "legacy-html-bundle/v1";
  readonly bundleKind: "legacyHtml";
  readonly compatibilityComponent: LegacyCompatibilityComponentV1;
}

export type EmissionBundleV1 = AuthoredEmissionBundleV1 | LegacyHtmlBundleV1;

export interface OrderedSlideBundleV1 {
  readonly slideId: string;
  readonly slideRevision: number;
  readonly bundleKind: EmissionBundleV1["bundleKind"];
  readonly bundleDigest: DigestV1;
}

export interface AuthoredDeckEmissionManifestV1 {
  readonly schemaVersion: "authored-deck-emission-manifest/v1";
  readonly presentationId: string;
  readonly presentationRevision: number;
  readonly operationId: string;
  readonly generationAttemptId: string | null;
  readonly orderedSlides: readonly OrderedSlideBundleV1[];
  readonly masterContractDigest: DigestV1;
  readonly compositionDigest: DigestV1;
  readonly assetManifestDigest: DigestV1;
  readonly fontManifestDigest: DigestV1;
  readonly qualityPolicyVersion: string;
  readonly canonicalDigest: DigestV1;
}

export interface ActiveAuthoredEmissionResponseV1 {
  readonly schemaVersion: "active-authored-emission-response/v1";
  readonly presentationId: string;
  readonly canonicalMode: "legacyHtml" | "bundleGraph";
  readonly presentationRevision: number;
  readonly canonicalDigest: DigestV1 | null;
  readonly manifest: AuthoredDeckEmissionManifestV1 | null;
  readonly bundles: readonly EmissionBundleV1[];
}

export interface BrowserObservationReceiptV1 {
  readonly schemaVersion: "browser-observation-receipt/v1";
  readonly inputBundleDigest: DigestV1;
  readonly rendererSourceDigest: DigestV1;
  readonly rendererVersion: string;
  readonly policyVersion: string;
  readonly fontSetDigest: DigestV1;
  readonly fontsReady: boolean;
  readonly imagesReady: boolean;
  readonly unresolvedResourceDigests: readonly DigestV1[];
  readonly mutationFreeWindowMs: number;
  readonly captureEpoch: number;
  readonly capturedPngDigest: DigestV1;
  readonly elements: readonly Readonly<Record<string, unknown>>[];
  readonly receiptDigest: DigestV1;
}

export interface WorkbenchCapabilityProjectionV1 {
  readonly schemaVersion: "workbench-capability-projection/v1";
  readonly presentationId: string;
  readonly canonicalMode: "legacyHtml" | "bundleGraph";
  readonly presentationRevision: number;
  readonly canonicalDigest: DigestV1 | null;
  readonly manifestDigest: DigestV1 | null;
  readonly degradedFidelityOnly: boolean;
  readonly fidelity: Readonly<Record<string, unknown>>;
  readonly hybrid: Readonly<Record<string, unknown>>;
  /** Server-issued decisions only; browser code must not synthesize a capability. */
  readonly capabilityMatrix: readonly {
    readonly capabilityId:
      | "shape.gradient"
      | "shape.shadow"
      | "layout.transform"
      | "layout.flex"
      | "layout.grid"
      | "shape.borderRadius"
      | "shape.opacity"
      | "shape.clip";
    readonly state: "disabled" | "shadow" | "ready" | "blocked";
    readonly decision:
      | "native"
      | "representationFallback"
      | "identityFailure"
      | "ownershipFailure"
      | "assetPublicationFailure"
      | "blocked";
    readonly reasonCode: string;
    readonly receiptDigest: DigestV1;
  }[];
  readonly receipts: readonly {
    readonly receiptId: "cjk" | "chart" | "zBand" | "degradation";
    readonly state: "disabled" | "shadow" | "ready" | "blocked";
    readonly decision:
      | "native"
      | "representationFallback"
      | "identityFailure"
      | "ownershipFailure"
      | "assetPublicationFailure"
      | "blocked";
    readonly reasonCode: string;
    readonly receiptDigest: DigestV1;
  }[];
  readonly readiness: {
    readonly authoredCreationReady: boolean;
    readonly fidelityExportReady: boolean;
    readonly promotionReady: false;
    readonly fullNativeParityReady: false;
    readonly independentVnextGenerationReady: false;
  };
  readonly killSwitches: readonly Readonly<Record<string, unknown>>[];
  readonly elements: readonly Readonly<Record<string, unknown>>[];
  readonly projectionDigest: DigestV1 | null;
}

export interface NativeAdmissionReceiptV1 {
  readonly schemaVersion: "native-admission-receipt/v1";
  readonly inputBundleDigest: DigestV1;
  readonly observationReceiptDigest: DigestV1;
  readonly reconciliationPolicyVersion: string;
  readonly serializerId: string;
  readonly serializerVersion: string;
  readonly elements: readonly {
    readonly elementId: QidV1;
    readonly kind: AuthoredSemanticElementV1["kind"];
    readonly state: "admitted" | "rasterFallback" | "publicationFailed";
    readonly admittedFeatures: readonly string[];
    readonly fallbackReason: string | null;
  }[];
  readonly receiptDigest: DigestV1;
}

export interface HybridExportPlanV1 {
  readonly schemaVersion: "hybrid-export-plan/v1";
  readonly exportAttemptId: string;
  readonly inputBundleDigest: DigestV1;
  readonly admissionReceiptDigest: DigestV1;
  readonly rendererSourceDigest: DigestV1;
  readonly paintEntries: readonly {
    readonly elementId: QidV1 | null;
    readonly paintOwner: "native" | "rasterIsland";
    readonly zBand: number;
    readonly artifactDigest: DigestV1 | null;
    readonly fallbackReason: string | null;
  }[];
  readonly fixedPointReached: boolean;
  readonly slideDemotedToRaster: boolean;
  readonly planDigest: DigestV1;
}

export interface CapabilityReceiptV1 {
  readonly schemaVersion: "authored-capability-receipt/v1";
  readonly capabilityId: string;
  readonly state: "disabled" | "shadow" | "ready" | "blocked";
  readonly scope: Readonly<Record<string, unknown>>;
  readonly serializerId: string;
  readonly serializerVersion: string;
  readonly featureSetDigest: DigestV1;
  readonly policyDigest: DigestV1;
  readonly evidenceBundleId: string;
  readonly evidenceDigest: DigestV1;
  readonly issuer: string;
  readonly environment: Readonly<Record<string, unknown>>;
  readonly evaluatedAt: string;
  readonly expiresAt: string;
  readonly stateReason: string;
  readonly supersedesReceiptDigest: DigestV1 | null;
  readonly revokesReceiptDigest: DigestV1 | null;
  readonly receiptDigest: DigestV1;
}
