// Generated from ooxml-package.schema.json; do not edit.
export const CONTRACT_SCHEMA_SHA256 = "sha256:41573e339b620dcba47bb1589f3d527001de4e23d298696b3a0f10f21742ab0a" as const;

export type JsonValue = string | number | boolean | null | readonly JsonValue[] | { readonly [key: string]: JsonValue };

export interface PackageInspection {
  readonly classification: "PRESERVE_EXACT" | "PATCH_SLICE" | "GENERATED" | "QUARANTINED";
  readonly partCount: number;
  readonly slideCount: number;
  readonly masterCount: number;
  readonly layoutCount: number;
  readonly externalRelationshipCount: number;
  readonly externalRelationshipWarnings: readonly string[];
  readonly packageDigest: string;
  readonly relationshipDigest: string;
  readonly closureDigest: string;
  readonly macroDetected: boolean;
  readonly quarantineReasons: readonly string[];
}

export interface PackageVersionReceipt {
  readonly schemaVersion: "authored-vnext-ooxml-package/v1";
  readonly packageId: string;
  readonly sourcePackageId: string | null;
  readonly ownerId: string;
  readonly state: "imported" | "quarantined" | "editable" | "archived";
  readonly mode: "PRESERVE_EXACT" | "CORPORATE_NORMALIZE";
  readonly origin: "IMPORTED" | "GENERATED";
  readonly revision: number;
  readonly artifactDigest: string;
  readonly archiveByteLength: number;
  readonly inspection: PackageInspection;
  readonly createdAt: string;
}

export interface ImportPackageReceipt {
  readonly schemaVersion: "authored-vnext-ooxml-import/v1";
  readonly replayed: boolean;
  readonly editable: boolean;
  readonly sourceArchived: true;
  readonly package: PackageVersionReceipt;
}

export interface EditableForkRequest {
  readonly schemaVersion: "authored-vnext-ooxml-fork-command/v1";
  readonly mode: "PRESERVE_EXACT" | "CORPORATE_NORMALIZE";
  readonly expectedRevision: number;
  readonly expectedArtifactDigest: string;
  readonly idempotencyKey: string;
  readonly expectedAccountGeneration: number;
  readonly masterContract?: Readonly<Record<string, JsonValue>>;
  readonly slides?: readonly Readonly<Record<string, JsonValue>>[];
}

export interface TextSelector {
  readonly kind: "placeholder" | "element";
  readonly idx?: number;
  readonly role?: string;
  readonly id?: number | string;
  readonly occurrence?: number;
}

export interface PackageOperationCommand {
  readonly schemaVersion: "authored-vnext-ooxml-operation-command/v1";
  readonly operation: "NOOP_SAVE" | "PATCH_TEXT_SLICE" | "CREATE_MASTER" | "UPDATE_MASTER" | "DELETE_MASTER" | "CREATE_LAYOUT" | "UPDATE_LAYOUT" | "DELETE_LAYOUT" | "SAVE_EXPORT" | "ARCHIVE";
  readonly expectedRevision: number;
  readonly expectedArtifactDigest: string;
  readonly idempotencyKey: string;
  readonly expectedAccountGeneration: number;
  readonly partName?: string;
  readonly selector?: TextSelector;
  readonly expectedText?: string;
  readonly replacementText?: string;
  readonly masterContract?: Readonly<Record<string, JsonValue>>;
  readonly slides?: readonly Readonly<Record<string, JsonValue>>[];
}

export interface PackageOperationReceipt {
  readonly schemaVersion: "authored-vnext-ooxml-operation/v1";
  readonly operationId: string;
  readonly packageId: string;
  readonly operation: "NOOP_SAVE" | "PATCH_TEXT_SLICE" | "CREATE_MASTER" | "UPDATE_MASTER" | "DELETE_MASTER" | "CREATE_LAYOUT" | "UPDATE_LAYOUT" | "DELETE_LAYOUT" | "SAVE_EXPORT" | "ARCHIVE";
  readonly state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  readonly attemptCount: number;
  readonly fencingToken: number;
  readonly resumable: boolean;
  readonly replayed: boolean;
  readonly expectedRevision: number;
  readonly expectedArtifactDigest: string;
  readonly resultRevision: number | null;
  readonly resultArtifactDigest: string | null;
  readonly changedParts: readonly string[];
  readonly preservedExactPartCount: number;
  readonly silentLossCount: number;
  readonly errorCode: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface PackageReceiptReadBack {
  readonly schemaVersion: "authored-vnext-ooxml-readback/v1";
  readonly package: PackageVersionReceipt;
  readonly sourceArtifactDigest: string;
  readonly sourceArchiveRetained: true;
  readonly unsupportedPartsPassthrough: boolean;
  readonly untouchedPartsVerified: boolean;
  readonly preservedExactPartCount: number;
  readonly patchSlicePartCount: number;
  readonly generatedPartCount: number;
  readonly silentLossCount: number;
  readonly changedParts: readonly string[];
}

export interface PackageRollbackRequest {
  readonly schemaVersion: "authored-vnext-ooxml-rollback-command/v1";
  readonly targetRevision: number;
  readonly expectedRevision: number;
  readonly expectedArtifactDigest: string;
  readonly idempotencyKey: string;
  readonly expectedAccountGeneration: number;
}
