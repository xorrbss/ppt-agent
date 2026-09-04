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

export type PublicV2DesignPackPin = {
  designPackId: string;
  version: number;
  digest: string;
};

export type PublicV2GenerationRequest = {
  schemaVersion?: "presentation-generation-request/v2";
  generationMode?: PublicV2GenerationMode;
  designPackPin?: PublicV2DesignPackPin;
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

export const PUBLIC_V2_FIDELITY_CAPABILITY = {
  mode: "fidelity-raster" as const,
  supportsPptx: true,
  supportsPdf: true,
  /** Fidelity artifacts preserve the rendered screen, not editable objects. */
  editableObjects: false,
  canvas: { width: 1280, height: 720 },
  accessibility: "raster-only" as const,
  cjkSafety: "release-receipt-required" as const,
  visualSafety: "overflow-and-occlusion-receipt-required" as const,
} as const;

export class PublicV2ApiError extends Error {
  readonly status: number;
  readonly reasonCode: string;
  readonly retryable: boolean;

  constructor(
    status: number,
    reasonCode: string,
    retryable = status >= 500 || status === 429,
  ) {
    super(reasonCode);
    this.name = "PublicV2ApiError";
    this.status = status;
    this.reasonCode = reasonCode;
    this.retryable = retryable;
  }
}

export type PublicV2OperationClientOptions = {
  fetchImpl?: typeof fetch;
  storage?: Storage | null;
  storageKey?: string;
  /** Server-derived, pseudonymous principal namespace for browser checkpoints. */
  storageScope?: string;
  createIdempotencyKey?: () => string;
};

type PublicV2Receipt = {
  operationId: string;
  presentationId: string;
  status: PublicV2OperationStatus;
};

type PublicV2Status = {
  operationId: string;
  presentationId: string;
  kind: PublicV2OperationKind;
  status: PublicV2OperationStatus;
  phase: PublicV2OperationPhase | null;
  progressPercent: number | null;
  sourceRevision: number | null;
  error: PublicV2OperationFailure | null;
  quality: PublicV2Quality;
  readiness: PublicV2Readiness;
  generationTiming: PublicV2GenerationTiming | null;
  generationRouteReceipt: PublicV2GenerationRouteReceipt | null;
  artifacts: PublicV2Artifact[];
};

type PersistedOperation = {
  version: 1;
  kind: PublicV2OperationKind;
  idempotencyKey: string;
  requestFingerprint: string;
  presentationId: string | null;
  operationId: string | null;
  eventCursor: string | null;
  /** Non-content descriptor used only to replay an uncertain export admission. */
  exportRecovery: PublicV2ExportRequest | null;
};

const DEFAULT_STORAGE_PREFIX = "presenton.public-v2.operation/v1";
const ACTIVE_PRINCIPAL_SCOPE_KEY = "presenton.public-v2.active-principal/v1";
const STORAGE_SCOPE = /^[A-Za-z0-9._-]{1,80}$/;
const PUBLIC_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const STATUSES = new Set<PublicV2OperationStatus>([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
]);
const PHASES = new Set<PublicV2OperationPhase>([
  "admission",
  "authoring",
  "validating",
  "rendering",
  "packaging",
  "publishing",
]);
const GENERATION_MODES = new Set<PublicV2GenerationMode>([
  "auto",
  "registered-template",
  "brand-style",
  "free-design",
  "fidelity-first",
  "editable-first",
]);
const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

const EMPTY_SNAPSHOT: PublicV2OperationSnapshot = {
  lifecycle: "idle",
  operationId: null,
  presentationId: null,
  kind: null,
  idempotencyKey: null,
  status: null,
  phase: null,
  progressPercent: null,
  sourceRevision: null,
  eventCursor: null,
  events: [],
  failure: null,
  pendingAdmission: false,
  cancellationRequested: false,
  quality: { releaseReadiness: "not_assessed", receipt: null },
  readiness: {
    scope: "artifact:fidelity",
    fidelityArtifactReady: false,
    nativeEditabilityReady: false,
    promotionReady: false,
    deploymentAuthorized: false,
  },
  generationTiming: null,
  generationRouteReceipt: null,
  artifacts: [],
  downloadStatus: "unavailable",
  canCancel: false,
};

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new PublicV2ApiError(502, "public_v2_invalid_response");
  }
  return value as Record<string, unknown>;
}

function publicId(value: unknown, field: string): string {
  if (typeof value !== "string" || !PUBLIC_ID.test(value)) {
    throw new PublicV2ApiError(502, `public_v2_invalid_${field}`);
  }
  return value;
}

function operationStatus(value: unknown): PublicV2OperationStatus {
  if (typeof value !== "string" || !STATUSES.has(value as PublicV2OperationStatus)) {
    throw new PublicV2ApiError(502, "public_v2_invalid_operation_status");
  }
  return value as PublicV2OperationStatus;
}

function operationPhase(value: unknown): PublicV2OperationPhase | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || !PHASES.has(value as PublicV2OperationPhase)) {
    throw new PublicV2ApiError(502, "public_v2_invalid_operation_phase");
  }
  return value as PublicV2OperationPhase;
}

function optionalPercent(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0 || value > 100) {
    throw new PublicV2ApiError(502, "public_v2_invalid_progress_percent");
  }
  return value;
}

function optionalRevision(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new PublicV2ApiError(502, "public_v2_invalid_source_revision");
  }
  return value;
}

function nonNegativeCounter(value: unknown, reasonCode: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new PublicV2ApiError(502, reasonCode);
  }
  return value;
}

function timingCounter(value: unknown): number | "not_started" {
  return value === "not_started"
    ? value
    : nonNegativeCounter(value, "public_v2_invalid_generation_timing");
}

function generationStageTiming(value: unknown): PublicV2GenerationStageTiming {
  if (value === "not_started") return value;
  const source = record(value);
  if (Object.keys(source).some((key) => key !== "durationMillis")) {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_stage_timing");
  }
  return {
    durationMillis: nonNegativeCounter(
      source.durationMillis,
      "public_v2_invalid_generation_stage_timing",
    ),
  };
}

function generationTiming(value: unknown): PublicV2GenerationTiming | null {
  if (value === null || value === undefined) return null;
  const source = record(value);
  if (source.schemaVersion !== "public-generation-timing/v1") {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_timing");
  }
  const stages = record(source.stages);
  const stageNames = ["outline", "provider", "authoring", "repair", "chromiumReview", "publication"] as const;
  if (Object.keys(stages).length !== stageNames.length || stageNames.some((name) => !(name in stages))) {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_timing_stages");
  }
  const outcomes = new Set(["succeeded", "failed", "budget_exhausted", "cancelled", "dead_letter", "worker_crash"]);
  if (typeof source.outcome !== "string" || !outcomes.has(source.outcome)) {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_timing_outcome");
  }
  const usage = record(source.usageAvailability);
  const availability = new Set(["available", "partial", "unavailable", "not_started"]);
  if (typeof usage.tokens !== "string" || !availability.has(usage.tokens) || typeof usage.cost !== "string" || !availability.has(usage.cost)) {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_usage_availability");
  }
  const worker = record(source.worker);
  if (worker.status !== "observed" && worker.status !== "unavailable" && worker.status !== "not_started") {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_worker");
  }
  const optionalWorkerCounter = (counter: unknown): number | null => counter === null || counter === undefined
    ? null
    : nonNegativeCounter(counter, "public_v2_invalid_generation_worker");
  const retryReason = source.retryReason === null || source.retryReason === undefined
    ? null
    : typeof source.retryReason === "string" && source.retryReason
      ? source.retryReason
      : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_generation_retry_reason"); })();
  const terminalError = source.terminalError === null || source.terminalError === undefined
    ? null
    : typeof source.terminalError === "string" && source.terminalError
      ? source.terminalError
      : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_generation_terminal_error"); })();
  return {
    attemptNumber: nonNegativeCounter(source.attemptNumber, "public_v2_invalid_generation_attempt"),
    outcome: source.outcome as PublicV2GenerationTiming["outcome"],
    stages: Object.fromEntries(stageNames.map((name) => [name, generationStageTiming(stages[name])])) as PublicV2GenerationTiming["stages"],
    queueWaitMillis: timingCounter(source.queueWaitMillis),
    wallMillis: timingCounter(source.wallMillis),
    logicalCalls: timingCounter(source.logicalCalls),
    retryReason,
    usageAvailability: {
      tokens: usage.tokens as PublicV2GenerationTiming["usageAvailability"]["tokens"],
      cost: usage.cost as PublicV2GenerationTiming["usageAvailability"]["cost"],
    },
    worker: {
      status: worker.status as PublicV2GenerationTiming["worker"]["status"],
      worker: worker.worker === null || worker.worker === undefined
        ? null
        : typeof worker.worker === "string" && worker.worker
          ? worker.worker
          : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_generation_worker"); })(),
      peakMemory: optionalWorkerCounter(worker.peakMemory),
      peakHandles: optionalWorkerCounter(worker.peakHandles),
    },
    terminalError,
  };
}

function lifecycleFor(status: PublicV2OperationStatus): PublicV2Lifecycle {
  if (status === "succeeded") return "succeeded";
  if (status === "failed") return "failed";
  if (status === "cancelled") return "cancelled";
  return "monitoring";
}

function failure(value: unknown, httpStatus?: number): PublicV2OperationFailure | null {
  if (value === null || value === undefined) return null;
  const source = record(value);
  if (typeof source.reasonCode !== "string" || !source.reasonCode.trim()) {
    throw new PublicV2ApiError(502, "public_v2_invalid_operation_error");
  }
  return {
    reasonCode: source.reasonCode,
    retryable: source.retryable === true,
    ...(httpStatus === undefined ? {} : { httpStatus }),
  };
}

function artifact(value: unknown): PublicV2Artifact {
  const source = record(value);
  const item: PublicV2Artifact = {
    artifactId: publicId(source.artifactId, "artifact_id"),
    operationId: publicId(source.operationId, "artifact_operation_id"),
    presentationId: publicId(source.presentationId, "artifact_presentation_id"),
    kind: source.kind === "presentation-pptx" || source.kind === "presentation-pdf"
      ? source.kind
      : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_artifact_kind"); })(),
    digest: typeof source.digest === "string" ? source.digest : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_artifact_digest"); })(),
    sizeBytes: typeof source.sizeBytes === "number" && Number.isSafeInteger(source.sizeBytes) && source.sizeBytes >= 0
      ? source.sizeBytes
      : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_artifact_size"); })(),
    mediaType: typeof source.mediaType === "string" && source.mediaType.trim()
      ? source.mediaType
      : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_artifact_media_type"); })(),
    download: typeof source.download === "string" ? source.download : (() => { throw new PublicV2ApiError(502, "public_v2_invalid_artifact_download"); })(),
  };
  if (item.download !== `/api/v2/artifacts/${encodeURIComponent(item.artifactId)}/download`) {
    throw new PublicV2ApiError(502, "public_v2_invalid_artifact_download");
  }
  return item;
}

function parseReceipt(value: unknown): PublicV2Receipt {
  const source = record(value);
  if (source.schemaVersion !== "operation-receipt/v2") {
    throw new PublicV2ApiError(502, "public_v2_invalid_receipt");
  }
  return {
    operationId: publicId(source.operationId, "operation_id"),
    presentationId: publicId(source.presentationId, "presentation_id"),
    status: operationStatus(source.status),
  };
}

function designPackPin(value: unknown): PublicV2DesignPackPin {
  const source = record(value);
  if (
    typeof source.designPackId !== "string"
    || !PUBLIC_ID.test(source.designPackId)
    || typeof source.version !== "number"
    || !Number.isSafeInteger(source.version)
    || source.version < 1
    || typeof source.digest !== "string"
    || !SHA256_DIGEST.test(source.digest)
  ) {
    throw new PublicV2ApiError(502, "public_v2_invalid_design_pack_pin");
  }
  return {
    designPackId: source.designPackId,
    version: source.version,
    digest: source.digest,
  };
}

function generationRouteReceipt(
  value: unknown,
): PublicV2GenerationRouteReceipt | null {
  if (value === null || value === undefined) return null;
  const source = record(value);
  if (source.schemaVersion !== "generation-route-receipt/v1") {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_route_receipt");
  }
  const requestedMode = source.requestedMode;
  const selectedRoute = source.selectedRoute;
  const outputCapability = source.outputCapability;
  if (
    typeof requestedMode !== "string"
    || !GENERATION_MODES.has(requestedMode as PublicV2GenerationMode)
    || (selectedRoute !== "source-native" && selectedRoute !== "authored-fallback")
    || (outputCapability !== "fidelity" && outputCapability !== "editable")
    || typeof source.receiptDigest !== "string"
    || !SHA256_DIGEST.test(source.receiptDigest)
    || (
      source.narrativeIrDigest !== null
      && source.narrativeIrDigest !== undefined
      && (typeof source.narrativeIrDigest !== "string" || !SHA256_DIGEST.test(source.narrativeIrDigest))
    )
    || (
      source.fallbackReason !== null
      && source.fallbackReason !== undefined
      && typeof source.fallbackReason !== "string"
    )
  ) {
    throw new PublicV2ApiError(502, "public_v2_invalid_generation_route_receipt");
  }
  return {
    schemaVersion: "generation-route-receipt/v1",
    requestedMode: requestedMode as PublicV2GenerationMode,
    selectedRoute,
    designPackPin: source.designPackPin === null || source.designPackPin === undefined
      ? null
      : designPackPin(source.designPackPin),
    narrativeIrDigest: typeof source.narrativeIrDigest === "string"
      ? source.narrativeIrDigest
      : null,
    fallbackReason: typeof source.fallbackReason === "string"
      ? source.fallbackReason
      : null,
    outputCapability,
    receiptDigest: source.receiptDigest,
  };
}

function parseStatus(value: unknown): PublicV2Status {
  const source = record(value);
  if (source.schemaVersion !== "operation-status/v2") {
    throw new PublicV2ApiError(502, "public_v2_invalid_status");
  }
  const kind = source.kind;
  if (kind !== "presentation.generate" && kind !== "presentation.export") {
    throw new PublicV2ApiError(502, "public_v2_invalid_operation_kind");
  }
  const releaseReadiness = source.releaseReadiness;
  if (releaseReadiness !== "ready" && releaseReadiness !== "blocked" && releaseReadiness !== "not_assessed") {
    throw new PublicV2ApiError(502, "public_v2_invalid_release_readiness");
  }
  const artifacts = Array.isArray(source.artifacts) ? source.artifacts.map(artifact) : (() => {
    throw new PublicV2ApiError(502, "public_v2_invalid_artifacts");
  })();
  const operationId = publicId(source.operationId, "operation_id");
  const presentationId = publicId(source.presentationId, "presentation_id");
  if (
    source.readinessScope !== "artifact:fidelity" ||
    typeof source.fidelityArtifactReady !== "boolean" ||
    source.nativeEditabilityReady !== false ||
    source.promotionReady !== false ||
    source.deploymentAuthorized !== false ||
    (releaseReadiness === "ready" && source.fidelityArtifactReady !== true) ||
    (source.fidelityArtifactReady === true && (releaseReadiness !== "ready" || artifacts.length === 0))
  ) {
    throw new PublicV2ApiError(502, "public_v2_invalid_readiness_boundary");
  }
  if (artifacts.some((item) => item.operationId !== operationId || item.presentationId !== presentationId)) {
    throw new PublicV2ApiError(502, "public_v2_artifact_binding_invalid");
  }
  return {
    operationId,
    presentationId,
    kind,
    status: operationStatus(source.status),
    phase: operationPhase(source.phase),
    progressPercent: optionalPercent(source.progressPercent),
    sourceRevision: optionalRevision(source.sourceRevision),
    error: failure(source.error),
    quality: {
      releaseReadiness,
      receipt: source.qualityReceipt === null || source.qualityReceipt === undefined
        ? null
        : record(source.qualityReceipt),
    },
    readiness: {
      scope: "artifact:fidelity",
      fidelityArtifactReady: source.fidelityArtifactReady,
      nativeEditabilityReady: false,
      promotionReady: false,
      deploymentAuthorized: false,
    },
    generationTiming: generationTiming(source.generationTiming),
    generationRouteReceipt: generationRouteReceipt(source.generationRouteReceipt),
    artifacts,
  };
}

function parseEvent(value: unknown, expectedOperationId: string): PublicV2OperationEvent {
  const source = record(value);
  if (source.schemaVersion !== "operation-event/v2") {
    throw new PublicV2ApiError(502, "public_v2_invalid_event");
  }
  const operationId = publicId(source.operationId, "event_operation_id");
  if (operationId !== expectedOperationId) {
    throw new PublicV2ApiError(502, "public_v2_event_binding_invalid");
  }
  if (typeof source.sequence !== "number" || !Number.isSafeInteger(source.sequence) || source.sequence < 1) {
    throw new PublicV2ApiError(502, "public_v2_invalid_event_sequence");
  }
  if (typeof source.occurredAt !== "string" || !source.occurredAt) {
    throw new PublicV2ApiError(502, "public_v2_invalid_event_time");
  }
  if (source.reasonCode !== null && source.reasonCode !== undefined && typeof source.reasonCode !== "string") {
    throw new PublicV2ApiError(502, "public_v2_invalid_event_reason");
  }
  return {
    eventId: publicId(source.eventId, "event_id"),
    operationId,
    sequence: source.sequence,
    occurredAt: source.occurredAt,
    status: operationStatus(source.status),
    phase: operationPhase(source.phase),
    progressPercent: optionalPercent(source.progressPercent),
    reasonCode: typeof source.reasonCode === "string" ? source.reasonCode : null,
  };
}

function parseEventPage(value: unknown, expectedOperationId: string): {
  events: PublicV2OperationEvent[];
  nextCursor: string | null;
} {
  const source = record(value);
  if (source.schemaVersion !== "operation-event-page/v2" || publicId(source.operationId, "event_page_operation_id") !== expectedOperationId || !Array.isArray(source.events)) {
    throw new PublicV2ApiError(502, "public_v2_invalid_event_page");
  }
  const events = source.events.map((event) => parseEvent(event, expectedOperationId));
  if (events.some((event, index) => index > 0 && event.sequence <= events[index - 1].sequence)) {
    throw new PublicV2ApiError(502, "public_v2_event_order_invalid");
  }
  const nextCursor = source.nextCursor === null || source.nextCursor === undefined
    ? null
    : publicId(source.nextCursor, "event_cursor");
  return { events, nextCursor };
}

async function responseBody(response: Response): Promise<unknown> {
  const body = await response.json().catch(() => null);
  if (response.ok) return body;
  const source = body && typeof body === "object" && !Array.isArray(body)
    ? body as Record<string, unknown>
    : {};
  const detail = source.detail && typeof source.detail === "object" && !Array.isArray(source.detail)
    ? source.detail as Record<string, unknown>
    : source;
  const reasonCode = typeof detail.reasonCode === "string"
    ? detail.reasonCode
    : "public_v2_request_failed";
  throw new PublicV2ApiError(response.status, reasonCode);
}

function stableJson(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (typeof value === "object") {
    const source = value as Record<string, unknown>;
    return `{${Object.keys(source).filter((key) => source[key] !== undefined).sort().map((key) => `${JSON.stringify(key)}:${stableJson(source[key])}`).join(",")}}`;
  }
  throw new PublicV2ApiError(400, "public_v2_invalid_request");
}

function persistedExportRequest(value: unknown): PublicV2ExportRequest {
  const source = record(value);
  const output = record(source.output);
  if (
    source.schemaVersion !== "presentation-export-request/v2"
    || typeof source.sourceRevision !== "number"
    || !Number.isSafeInteger(source.sourceRevision)
    || source.sourceRevision < 0
    || source.qualityPolicy !== "release-v1"
    || (output.format !== "pptx" && output.format !== "pdf")
    || output.mode !== "fidelity-raster"
    || output.fontEmbedding !== "none"
  ) {
    throw new PublicV2ApiError(400, "public_v2_invalid_export_recovery");
  }
  let requiredCapabilities: string[] | undefined;
  if (output.requiredCapabilities !== undefined) {
    const capabilities = output.requiredCapabilities;
    if (
      !Array.isArray(capabilities)
      || !capabilities.every((item): item is string => typeof item === "string")
    ) {
      throw new PublicV2ApiError(400, "public_v2_invalid_export_recovery");
    }
    requiredCapabilities = [...capabilities];
  }
  return {
    schemaVersion: "presentation-export-request/v2",
    sourceRevision: source.sourceRevision,
    qualityPolicy: "release-v1",
    output: {
      format: output.format,
      mode: "fidelity-raster",
      fontEmbedding: "none",
      ...(requiredCapabilities === undefined ? {} : { requiredCapabilities }),
    },
  };
}

async function requestFingerprint(value: unknown): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new PublicV2ApiError(503, "public_v2_crypto_unavailable", true);
  }
  const data = new TextEncoder().encode(stableJson(value));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function defaultStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function defaultIdempotencyKey(): string {
  if (!globalThis.crypto?.randomUUID) {
    throw new PublicV2ApiError(503, "public_v2_crypto_unavailable", true);
  }
  return `public-v2:${globalThis.crypto.randomUUID()}`;
}

function normalizedStorageScope(value: string | undefined): string {
  const scope = value?.trim() || "anonymous";
  if (!STORAGE_SCOPE.test(scope)) {
    throw new PublicV2ApiError(400, "public_v2_storage_scope_invalid");
  }
  return scope;
}

/**
 * An operation client can be created once per page. `resume()` is safe to call
 * after each reload: it reads only the opaque durable operation checkpoint.
 */
export class PublicV2OperationClient {
  private readonly fetchImpl: typeof fetch;
  private readonly storage: Storage | null;
  private readonly storageKey: string;
  private readonly createIdempotencyKey: () => string;
  private snapshotValue: PublicV2OperationSnapshot = EMPTY_SNAPSHOT;
  private readonly listeners = new Set<(snapshot: PublicV2OperationSnapshot) => void>();
  private inFlightSubmission: Promise<PublicV2OperationSnapshot> | null = null;

  constructor(options: PublicV2OperationClientOptions = {}) {
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.storage = options.storage === undefined ? defaultStorage() : options.storage;
    const scope = normalizedStorageScope(options.storageScope);
    this.storageKey = options.storageKey ?? `${DEFAULT_STORAGE_PREFIX}:${scope}`;
    this.createIdempotencyKey = options.createIdempotencyKey ?? defaultIdempotencyKey;
    if (options.storageKey === undefined) this.rotatePrincipalCheckpoint(scope);
  }

  get snapshot(): PublicV2OperationSnapshot {
    return this.snapshotValue;
  }

  subscribe(listener: (snapshot: PublicV2OperationSnapshot) => void): () => void {
    this.listeners.add(listener);
    listener(this.snapshotValue);
    return () => this.listeners.delete(listener);
  }

  /** Remove the local checkpoint. This never cancels server-side work. */
  clear(): void {
    try {
      this.storage?.removeItem(this.storageKey);
    } catch {
      // Storage failure must not alter an operation already accepted by the API.
    }
    this.update(EMPTY_SNAPSHOT);
  }

  async resume(): Promise<PublicV2OperationSnapshot> {
    const persisted = this.readPersisted();
    if (!persisted) return this.snapshotValue;
    this.update({
      ...EMPTY_SNAPSHOT,
      lifecycle: persisted.operationId ? "monitoring" : "pending_admission",
      operationId: persisted.operationId,
      presentationId: persisted.presentationId,
      kind: persisted.kind,
      idempotencyKey: persisted.idempotencyKey,
      eventCursor: persisted.eventCursor,
      pendingAdmission: persisted.operationId === null,
    });
    return persisted.operationId ? this.refresh() : this.snapshotValue;
  }

  async submitGeneration(request: PublicV2GenerationRequest): Promise<PublicV2OperationSnapshot> {
    return this.submit({
      kind: "presentation.generate",
      presentationId: null,
      path: "/api/v2/presentations/generations",
      request: {
        ...request,
        schemaVersion: "presentation-generation-request/v2",
        engine: "authored",
        qualityPolicy: "interactive-v1",
      },
    });
  }

  async submitExport(
    presentationId: string,
    request: PublicV2ExportRequest,
  ): Promise<PublicV2OperationSnapshot> {
    if (!PUBLIC_ID.test(presentationId)) {
      throw new PublicV2ApiError(400, "public_v2_invalid_presentation_id");
    }
    return this.submit({
      kind: "presentation.export",
      presentationId,
      path: `/api/v2/presentations/${encodeURIComponent(presentationId)}/exports`,
      request: {
        ...request,
        schemaVersion: "presentation-export-request/v2",
        qualityPolicy: "release-v1",
        output: {
          ...request.output,
          mode: "fidelity-raster",
          fontEmbedding: "none",
        },
      },
    });
  }

  async recoverPendingExport(): Promise<PublicV2OperationSnapshot> {
    const persisted = this.readPersisted();
    if (
      !persisted
      || persisted.operationId !== null
      || persisted.kind !== "presentation.export"
      || !persisted.presentationId
      || !persisted.exportRecovery
    ) {
      throw new PublicV2ApiError(409, "public_v2_export_recovery_unavailable", false);
    }
    return this.submit({
      kind: "presentation.export",
      presentationId: persisted.presentationId,
      path: `/api/v2/presentations/${encodeURIComponent(persisted.presentationId)}/exports`,
      request: persisted.exportRecovery as unknown as Record<string, unknown>,
    });
  }

  async refresh(): Promise<PublicV2OperationSnapshot> {
    const operationId = this.snapshotValue.operationId;
    if (!operationId) return this.snapshotValue;
    try {
      const status = parseStatus(await this.request(`/api/v2/operations/${encodeURIComponent(operationId)}`));
      if (status.operationId !== operationId) {
        throw new PublicV2ApiError(502, "public_v2_operation_binding_invalid");
      }
      this.updateStatus(status);
      await this.readNewEvents(status.operationId);
      return this.snapshotValue;
    } catch (error) {
      this.setTransportFailure(error);
      throw error;
    }
  }

  async cancel(): Promise<PublicV2OperationSnapshot> {
    const operationId = this.snapshotValue.operationId;
    if (!operationId || !this.snapshotValue.canCancel) return this.snapshotValue;
    try {
      const response = record(await this.request(`/api/v2/operations/${encodeURIComponent(operationId)}/cancel`, { method: "POST" }));
      if (response.schemaVersion !== "operation-cancel-receipt/v2" || publicId(response.operationId, "cancel_operation_id") !== operationId || typeof response.cancellationRequested !== "boolean") {
        throw new PublicV2ApiError(502, "public_v2_invalid_cancel_receipt");
      }
      this.update({ ...this.snapshotValue, cancellationRequested: response.cancellationRequested });
      return this.refresh();
    } catch (error) {
      this.setTransportFailure(error);
      throw error;
    }
  }

  artifactDownloadUrl(item: PublicV2Artifact): string {
    return item.download;
  }

  private async submit(input: {
    kind: PublicV2OperationKind;
    presentationId: string | null;
    path: string;
    request: Record<string, unknown>;
  }): Promise<PublicV2OperationSnapshot> {
    if (this.inFlightSubmission) {
      throw new PublicV2ApiError(409, "public_v2_submission_in_progress");
    }
    const promise = this.doSubmit(input);
    this.inFlightSubmission = promise;
    try {
      return await promise;
    } finally {
      this.inFlightSubmission = null;
    }
  }

  private async doSubmit(input: {
    kind: PublicV2OperationKind;
    presentationId: string | null;
    path: string;
    request: Record<string, unknown>;
  }): Promise<PublicV2OperationSnapshot> {
    const fingerprint = await requestFingerprint(input.request);
    const persisted = this.readPersisted();
    const reusePending = persisted
      && persisted.operationId === null
      && persisted.kind === input.kind
      && persisted.presentationId === input.presentationId
      && persisted.requestFingerprint === fingerprint;
    if (persisted?.operationId === null && !reusePending) {
      throw new PublicV2ApiError(409, "public_v2_pending_request_mismatch", false);
    }
    const idempotencyKey = reusePending ? persisted.idempotencyKey : this.createIdempotencyKey();
    const checkpoint: PersistedOperation = {
      version: 1,
      kind: input.kind,
      idempotencyKey,
      requestFingerprint: fingerprint,
      presentationId: input.presentationId,
      operationId: null,
      eventCursor: null,
      exportRecovery: input.kind === "presentation.export"
        ? persistedExportRequest(input.request)
        : null,
    };
    this.writePersisted(checkpoint);
    this.update({
      ...EMPTY_SNAPSHOT,
      lifecycle: "submitting",
      kind: input.kind,
      presentationId: input.presentationId,
      idempotencyKey,
      pendingAdmission: true,
    });
    try {
      const receipt = parseReceipt(await this.request(input.path, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(input.request),
      }));
      if (input.presentationId && receipt.presentationId !== input.presentationId) {
        throw new PublicV2ApiError(502, "public_v2_presentation_binding_invalid");
      }
      this.writePersisted({
        ...checkpoint,
        presentationId: receipt.presentationId,
        operationId: receipt.operationId,
        exportRecovery: null,
      });
      this.update({
        ...EMPTY_SNAPSHOT,
        lifecycle: lifecycleFor(receipt.status),
        operationId: receipt.operationId,
        presentationId: receipt.presentationId,
        kind: input.kind,
        idempotencyKey,
        status: receipt.status,
        pendingAdmission: false,
        canCancel: receipt.status === "queued" || receipt.status === "running",
      });
      return this.refresh();
    } catch (error) {
      this.setTransportFailure(error);
      throw error;
    }
  }

  private async readNewEvents(operationId: string): Promise<void> {
    let cursor = this.snapshotValue.eventCursor;
    const seenCursors = new Set<string>();
    while (true) {
      const query = new URLSearchParams({ limit: "200" });
      if (cursor) query.set("after", cursor);
      const suffix = `?${query.toString()}`;
      const page = parseEventPage(await this.request(`/api/v2/operations/${encodeURIComponent(operationId)}/events${suffix}`), operationId);
      const events = mergeEvents(this.snapshotValue.events, page.events);
      // A normal incremental poll is empty and returns nextCursor=null. Keep
      // the prior checkpoint in that case, otherwise the next refresh would
      // replay the entire event history from the beginning.
      const nextCursor = page.events.length > 0 && page.nextCursor
        ? page.nextCursor
        : cursor;
      this.update({ ...this.snapshotValue, events, eventCursor: nextCursor });
      this.writeCursor(nextCursor);
      // `nextCursor` is also a durable checkpoint for a short page. Continue
      // in this refresh only when the server filled the requested page.
      if (page.events.length < 200 || !page.nextCursor || page.nextCursor === cursor || seenCursors.has(page.nextCursor)) return;
      seenCursors.add(page.nextCursor);
      cursor = page.nextCursor;
    }
  }

  private updateStatus(status: PublicV2Status): void {
    const next: PublicV2OperationSnapshot = {
      ...this.snapshotValue,
      lifecycle: lifecycleFor(status.status),
      operationId: status.operationId,
      presentationId: status.presentationId,
      kind: status.kind,
      status: status.status,
      phase: status.phase,
      progressPercent: status.progressPercent,
      sourceRevision: status.sourceRevision,
      failure: status.error,
      pendingAdmission: false,
      cancellationRequested: this.snapshotValue.cancellationRequested && (status.status === "queued" || status.status === "running"),
      quality: status.quality,
      readiness: status.readiness,
      generationTiming: status.generationTiming,
      generationRouteReceipt: status.generationRouteReceipt,
      artifacts: status.artifacts,
      downloadStatus: status.artifacts.length ? "ready" : "unavailable",
      canCancel: status.status === "queued" || status.status === "running",
    };
    this.update(next);
  }

  private setTransportFailure(error: unknown): void {
    const known = error instanceof PublicV2ApiError
      ? error
      : new PublicV2ApiError(0, "public_v2_network_error", true);
    this.update({
      ...this.snapshotValue,
      lifecycle: this.snapshotValue.operationId
        ? this.snapshotValue.lifecycle
        : this.snapshotValue.pendingAdmission
          ? "pending_admission"
          : "failed",
      failure: {
        reasonCode: known.reasonCode,
        retryable: known.retryable,
        ...(known.status ? { httpStatus: known.status } : {}),
      },
    });
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const response = await this.fetchImpl(path, {
      cache: "no-store",
      credentials: "include",
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    try {
      return await responseBody(response);
    } catch (error) {
      if (error instanceof PublicV2ApiError && (error.status === 401 || error.status === 403)) {
        this.clear();
      }
      throw error;
    }
  }

  private readPersisted(): PersistedOperation | null {
    try {
      const raw = this.storage?.getItem(this.storageKey);
      if (!raw) return null;
      const value = record(JSON.parse(raw));
      if (value.version !== 1 || (value.kind !== "presentation.generate" && value.kind !== "presentation.export") || typeof value.idempotencyKey !== "string" || !value.idempotencyKey || typeof value.requestFingerprint !== "string" || !value.requestFingerprint) {
        return null;
      }
      const presentationId = value.presentationId === null ? null : publicId(value.presentationId, "persisted_presentation_id");
      const operationId = value.operationId === null ? null : publicId(value.operationId, "persisted_operation_id");
      const eventCursor = value.eventCursor === null ? null : publicId(value.eventCursor, "persisted_event_cursor");
      const exportRecovery = value.exportRecovery === null || value.exportRecovery === undefined
        ? null
        : persistedExportRequest(value.exportRecovery);
      if (value.kind === "presentation.generate" && exportRecovery !== null) return null;
      return {
        version: 1,
        kind: value.kind,
        idempotencyKey: value.idempotencyKey,
        requestFingerprint: value.requestFingerprint,
        presentationId,
        operationId,
        eventCursor,
        exportRecovery,
      };
    } catch {
      return null;
    }
  }

  private writePersisted(value: PersistedOperation): void {
    try {
      this.storage?.setItem(this.storageKey, JSON.stringify(value));
    } catch {
      // The request still proceeds: idempotency remains valid for this page load.
    }
  }

  private writeCursor(eventCursor: string | null): void {
    const persisted = this.readPersisted();
    if (!persisted) return;
    this.writePersisted({ ...persisted, eventCursor });
  }

  private rotatePrincipalCheckpoint(scope: string): void {
    try {
      const previousScope = this.storage?.getItem(ACTIVE_PRINCIPAL_SCOPE_KEY);
      if (
        previousScope
        && previousScope !== scope
        && STORAGE_SCOPE.test(previousScope)
      ) {
        this.storage?.removeItem(`${DEFAULT_STORAGE_PREFIX}:${previousScope}`);
      }
      this.storage?.setItem(ACTIVE_PRINCIPAL_SCOPE_KEY, scope);
    } catch {
      // Browser storage is only a resume aid; API authority remains server-side.
    }
  }

  private update(snapshot: PublicV2OperationSnapshot): void {
    this.snapshotValue = snapshot;
    for (const listener of this.listeners) listener(snapshot);
  }
}

function mergeEvents(
  current: readonly PublicV2OperationEvent[],
  incoming: readonly PublicV2OperationEvent[],
): PublicV2OperationEvent[] {
  const byId = new Map(current.map((event) => [event.eventId, event]));
  for (const event of incoming) byId.set(event.eventId, event);
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence).slice(-400);
}

export function createPublicV2OperationClient(
  options?: PublicV2OperationClientOptions,
): PublicV2OperationClient {
  return new PublicV2OperationClient(options);
}
