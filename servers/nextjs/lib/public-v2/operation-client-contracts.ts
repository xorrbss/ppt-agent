/**
 * Browser-safe client state for the owner-scoped public v2 operation API.
 *
 * Only opaque operation identifiers, idempotency keys, request fingerprints,
 * event cursors, and non-content export replay descriptors are persisted.
 * Generation content, credentials, and download grants never enter localStorage.
 */

export type PublicV2OperationKind =
  | "presentation.generate"
  | "presentation.export";
export type PublicV2OperationStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";
export type PublicV2OperationPhase =
  | "admission"
  | "authoring"
  | "validating"
  | "rendering"
  | "packaging"
  | "publishing";
export type PublicV2Lifecycle =
  | "idle"
  | "pending_admission"
  | "submitting"
  | "monitoring"
  | "succeeded"
  | "failed"
  | "cancelled";

export type PublicV2GenerationMode =
  | "auto"
  | "registered-template"
  | "brand-style"
  | "free-design"
  | "fidelity-first"
  | "editable-first";

/** A mode a new request may ask for; ``registered-template`` only appears on historical receipts. */
export type PublicV2RequestGenerationMode = Exclude<PublicV2GenerationMode, "registered-template">;

/** A design-pack pin on a historical route receipt; the design-pack route was removed on 2026-11-03. */
export type PublicV2DesignPackPin = {
  designPackId: string;
  version: number;
  digest: string;
};

export type PublicV2GenerationRequest = {
  schemaVersion?: "presentation-generation-request/v2";
  generationMode?: PublicV2RequestGenerationMode;
  strategy?: "authored" | "svg";
  commonHeaderDesign?: import("@/lib/common-header-design").CommonHeaderDesign;
  commonHeaderMode?: import("@/lib/common-header-design").CommonHeaderMode;
  commonHeaderCoverException?: boolean;
  svgTemplatePin?: import("@/app/(presentation-generator)/services/api/svg-templates").SvgTemplatePin;
  svgTemplateMode?: import("@/app/(presentation-generator)/services/api/svg-templates").SvgTemplateMode;
  svgTemplateFrameMap?: Record<string, string>;
  svgDesignReferenceSnapshotId?: string;
  aspectRatio?: "16:9" | "9:16" | "9:13" | "4:3" | "1:1" | "21:9";
  pageSize?: "A4";
  orientation?: "landscape" | "portrait";
  svgCanvas?: { widthMm: number; heightMm: number };
  content: string;
  slidesMarkdown?: string[];
  instructions?: string;
  slideCount?: number;
  language?: string;
  referenceSnapshotIds?: string[];
  qualityPolicy?: "interactive-v1" | "release-v1";
};

export type PublicV2GenerationRouteReceipt = {
  schemaVersion: "generation-route-receipt/v1";
  requestedMode: PublicV2GenerationMode;
  selectedRoute: "source-native" | "authored-fallback";
  designPackPin: PublicV2DesignPackPin | null;
  narrativeIrDigest: string | null;
  fallbackReason: string | null;
  outputCapability: "fidelity" | "editable";
  receiptDigest: string;
};

export type PublicV2ExportRequest = {
  schemaVersion?: "presentation-export-request/v2";
  sourceRevision: number;
  output: {
    format: "pptx" | "pdf";
    /** Public v2 currently exposes Fidelity only in the public browser flow. */
    mode: "fidelity-raster";
    fontEmbedding?: "none";
    requiredCapabilities?: string[];
  };
  qualityPolicy?: "interactive-v1" | "release-v1";
};

export type PublicV2Artifact = {
  artifactId: string;
  operationId: string;
  presentationId: string;
  kind: "presentation-pptx" | "presentation-pdf";
  digest: string;
  sizeBytes: number;
  mediaType: string;
  download: string;
};

export type PublicV2OperationEvent = {
  eventId: string;
  operationId: string;
  sequence: number;
  occurredAt: string;
  status: PublicV2OperationStatus;
  phase?: PublicV2OperationPhase | null;
  progressPercent?: number | null;
  reasonCode?: string | null;
};

export type PublicV2OperationFailure = {
  reasonCode: string;
  retryable: boolean;
  httpStatus?: number;
};

export type PublicV2Quality = {
  releaseReadiness: "ready" | "blocked" | "not_assessed";
  receipt: Record<string, unknown> | null;
};

export type PublicV2Readiness = {
  scope: "artifact:fidelity";
  fidelityArtifactReady: boolean;
  nativeEditabilityReady: false;
  promotionReady: false;
  deploymentAuthorized: false;
};

export type PublicV2GenerationStageTiming =
  | "not_started"
  | { durationMillis: number };

export type PublicV2GenerationTiming = {
  attemptNumber: number;
  outcome: "succeeded" | "failed" | "budget_exhausted" | "cancelled" | "dead_letter" | "worker_crash";
  stages: {
    outline: PublicV2GenerationStageTiming;
    provider: PublicV2GenerationStageTiming;
    authoring: PublicV2GenerationStageTiming;
    repair: PublicV2GenerationStageTiming;
    chromiumReview: PublicV2GenerationStageTiming;
    publication: PublicV2GenerationStageTiming;
  };
  queueWaitMillis: number | "not_started";
  wallMillis: number | "not_started";
  logicalCalls: number | "not_started";
  retryReason: string | null;
  usageAvailability: {
    tokens: "available" | "partial" | "unavailable" | "not_started";
    cost: "available" | "partial" | "unavailable" | "not_started";
  };
  worker: {
    status: "observed" | "unavailable" | "not_started";
    worker: string | null;
    peakMemory: number | null;
    peakHandles: number | null;
  };
  terminalError: string | null;
};

export type PublicV2OperationSnapshot = {
  lifecycle: PublicV2Lifecycle;
  operationId: string | null;
  presentationId: string | null;
  kind: PublicV2OperationKind | null;
  idempotencyKey: string | null;
  status: PublicV2OperationStatus | null;
  phase: PublicV2OperationPhase | null;
  progressPercent: number | null;
  sourceRevision: number | null;
  eventCursor: string | null;
  events: readonly PublicV2OperationEvent[];
  failure: PublicV2OperationFailure | null;
  pendingAdmission: boolean;
  cancellationRequested: boolean;
  quality: PublicV2Quality;
  /** Server-resolved routing evidence; never used to choose a client route. */
  generationRouteReceipt: PublicV2GenerationRouteReceipt | null;
  readiness: PublicV2Readiness;
  generationTiming: PublicV2GenerationTiming | null;
  artifacts: readonly PublicV2Artifact[];
  /** Artifact availability, intentionally separate from quality/readiness. */
  downloadStatus: "unavailable" | "ready";
  canCancel: boolean;
};

