// Generated from export-contract.schema.json; do not edit.
export const CONTRACT_SCHEMA_SHA256 = "sha256:fb34dcea968cfea4485e470eb482601d3016a4b43a9118cd8affc2249828bf4e" as const;

export type JsonValue = string | number | boolean | null | readonly JsonValue[] | { readonly [key: string]: JsonValue };

export interface OwnerScope {
  readonly tenantId: string;
  readonly ownerId: string;
}

export interface SlideSpecDigest {
  readonly slideId: string;
  readonly revision: number;
  readonly digest: string;
}

export interface AssetDigest {
  readonly assetId: string;
  readonly digest: string;
}

export interface QualityIssue {
  readonly code: string;
  readonly path: string;
  readonly message: string;
}

export interface QualityDecision {
  readonly decision: "approved" | "rejected";
  readonly decisionDigest: string;
  readonly checkedAt: string;
  readonly issues: readonly QualityIssue[];
}

export interface ArtifactReceipt {
  readonly schemaVersion: "authored-vnext-artifact-receipt/v1";
  readonly artifactId: string;
  readonly artifactRevision: number;
  readonly sha256: string;
  readonly byteLength: number;
  readonly mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  readonly createdAt: string;
  readonly downloadUrl: string;
  readonly packageReadbackDigest: string;
  readonly slideCount: number;
  readonly sourceDeckDigest: string;
  readonly sourceMasterDigest: string;
  readonly sourceSpecManifestDigest: string;
  readonly sourceAssetManifestDigest: string;
  readonly sourceFontDigest: string;
  readonly sourcePackageDigest?: string | null;
  readonly qualityDecision: QualityDecision;
}

export interface CreateExportJobRequest {
  readonly schemaVersion: "authored-vnext-export/v1";
  readonly ownerScope: OwnerScope;
  readonly deckId: string;
  readonly expectedDeckRevision: number;
  readonly expectedDeckDigest: string;
  readonly masterId: string;
  readonly expectedMasterRevision: number;
  readonly expectedMasterDigest: string;
  readonly expectedActiveArtifactRevision: number | null;
  readonly expectedActiveArtifactDigest: string | null;
  readonly slideSpecDigests: readonly SlideSpecDigest[];
  readonly assetDigests: readonly AssetDigest[];
  readonly fontDigest: string;
  readonly packageDigest: string | null;
  readonly idempotencyKey: string;
  readonly operationFence: number;
  readonly timeoutMs: number;
  readonly maxAttempts: number;
}

export interface CreateExportJobResponse {
  readonly schemaVersion: "authored-vnext-export/v1";
  readonly jobId: string;
  readonly state: "queued";
  readonly idempotencyKey: string;
  readonly operationFence: number;
  readonly createdAt: string;
  readonly statusUrl: string;
}

export interface ExportError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly failedClosed: true;
}

export interface ExportJobStatusResponse {
  readonly schemaVersion: "authored-vnext-export/v1";
  readonly jobId: string;
  readonly state: "queued" | "running" | "retry_wait" | "succeeded" | "failed" | "cancelled";
  readonly operationFence: number;
  readonly attemptCount: number;
  readonly maxAttempts: number;
  readonly resumable: boolean;
  readonly cancelRequested: boolean;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly nextAttemptAt?: string | null;
  readonly artifact: ArtifactReceipt | null;
  readonly error: ExportError | null;
}

export interface CancelExportJobRequest {
  readonly schemaVersion: "authored-vnext-export/v1";
  readonly ownerScope: OwnerScope;
  readonly expectedOperationFence: number;
}

export interface RollbackArtifactPublicationRequest {
  readonly schemaVersion: "authored-vnext-export/v1";
  readonly ownerScope: OwnerScope;
  readonly expectedActiveArtifactRevision: number;
  readonly expectedActiveArtifactDigest: string;
  readonly targetArtifactId: string;
  readonly targetArtifactRevision: number;
  readonly targetArtifactDigest: string;
  readonly idempotencyKey: string;
  readonly operationFence: number;
}

export interface WorkerSlide {
  readonly slideId: string;
  readonly layoutId: string;
  readonly values: Readonly<Record<string, JsonValue>>;
}

export interface WorkerAsset {
  readonly assetId: string;
  readonly mediaType: string;
  readonly bytesBase64: string;
  readonly sha256: string;
}

export interface WorkerDigests {
  readonly masterSha256: string;
  readonly specSha256: string;
  readonly assetManifestSha256: string;
  readonly fontManifestSha256: string;
  readonly packageSha256: string | null;
}

export interface WorkerPackageReceipt {
  readonly packageDigest: string;
  readonly relationshipDigest: string;
  readonly closureDigest: string;
  readonly slideCount: number;
  readonly masterCount: number;
  readonly layoutCount: number;
  readonly macroDetected: boolean;
  readonly quarantineReasons: readonly string[];
}

export interface WorkerPreservationReceipt {
  readonly silentLossCount: number;
  readonly missingParts: readonly string[];
  readonly unexpectedChangedParts: readonly string[];
}

export interface WorkerSlideReceipt {
  readonly partName: string;
  readonly slideId: string | null;
  readonly layoutId: string | null;
  readonly masterDigest: string | null;
}

export interface WorkerMasterReceipt {
  readonly contractDigest: string;
  readonly semanticDigest: string;
  readonly furnitureCount: number;
  readonly logoCount: number;
  readonly pageNumberCount: number;
  readonly fontFileCount: number;
  readonly duplicateFurnitureCount: 0;
  readonly fontFilesMode: "REFERENCE_METADATA_VERIFIED";
}

export interface WorkerReadBackReceipt {
  readonly adapterRevision: number;
  readonly validation: "candidate-reopened";
  readonly candidateDigestVerified: true;
  readonly package: WorkerPackageReceipt;
  readonly preservation: WorkerPreservationReceipt;
  readonly master: WorkerMasterReceipt;
  readonly slides: readonly WorkerSlideReceipt[];
}

export interface WorkerResumeReceipt {
  readonly attempt: number;
  readonly checkpointDigest: string;
}

export interface WorkerRequestEnvelope {
  readonly schemaVersion: "authored-vnext-ooxml-worker/v1";
  readonly operation: "EXPORT_PPTX";
  readonly operationId: string;
  readonly jobId?: string;
  readonly fencingToken: number;
  readonly attempt?: number;
  readonly timeoutMs?: number;
  readonly deadline?: string;
  readonly master: Readonly<Record<string, JsonValue>>;
  readonly slides: readonly WorkerSlide[];
  readonly assets: readonly WorkerAsset[];
  readonly digests: WorkerDigests;
  readonly resumeFromReceipt?: WorkerResumeReceipt | null;
}

export interface BufferedArtifact {
  readonly contentBase64: string;
  readonly sha256: string;
  readonly byteLength: number;
  readonly mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation";
  readonly filenameExtension: ".pptx";
}

export interface WorkerResponseEnvelope {
  readonly schemaVersion: "authored-vnext-ooxml-worker/v1";
  readonly operation: "EXPORT_PPTX";
  readonly operationId: string;
  readonly fencingToken: number;
  readonly status: "SUCCEEDED";
  readonly artifact: BufferedArtifact;
  readonly receipt: WorkerReadBackReceipt;
}

export interface WorkerError {
  readonly code: string;
  readonly message: string;
  readonly retryable: boolean;
  readonly failedClosed: true;
}

export interface WorkerErrorEnvelope {
  readonly schemaVersion: "authored-vnext-ooxml-worker/v1";
  readonly status: "FAILED";
  readonly error: WorkerError;
}
