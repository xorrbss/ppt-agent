import type { ExternalRelationshipPolicyReceipt } from "./external-relationship-policy.ts";
import type { OoxmlSecurityPolicy } from "./security.ts";

export type PackagePartClassification = "PRESERVE_EXACT" | "PATCH_SLICE" | "GENERATED" | "QUARANTINED";
export interface ContentTypeManifest {
  readonly defaults: ReadonlyMap<string, string>;
  readonly overrides: ReadonlyMap<string, string>;
  readonly digest: string;
}
export interface PackageRelationship {
  readonly source: string;
  readonly relsPart: string;
  readonly id: string;
  readonly type: string;
  readonly target: string;
  readonly targetMode: "Internal" | "External";
  readonly targetModeRaw: string | null;
  readonly normalizedTarget: string | null;
  readonly digest: string;
}
export interface PackagePartRecord {
  readonly name: string;
  readonly bytes: number;
  readonly digest: string;
  readonly contentType: string | null;
  readonly classification: PackagePartClassification;
}
export interface PackageInventoryManifest {
  readonly version: "ooxml-package-inventory/v1";
  readonly partCount: number;
  readonly totalBytes: number;
  readonly packageDigest: string;
  readonly inventoryDigest: string;
  readonly parts: readonly PackagePartRecord[];
}
export interface PackagePreservationManifest {
  readonly version: "ooxml-preservation-manifest/v1";
  readonly sourcePackageDigest: string;
  readonly sourceRelationshipDigest: string;
  readonly sourceClosureDigest: string;
  readonly sourceInventoryDigest: string;
  readonly classification: PackagePartClassification;
  readonly parts: readonly PackagePartRecord[];
  readonly partDigests: Readonly<Record<string, string>>;
  readonly quarantineReasons: readonly string[];
  readonly manifestDigest: string;
}
export interface PackageGraph {
  readonly entries: ReadonlyMap<string, Buffer>;
  readonly parts: readonly PackagePartRecord[];
  readonly partDigests: Readonly<Record<string, string>>;
  readonly contentTypes: ContentTypeManifest;
  readonly relationships: readonly PackageRelationship[];
  readonly externalRelationships: readonly PackageRelationship[];
  readonly externalRelationshipPolicy: ExternalRelationshipPolicyReceipt;
  readonly reachableParts: readonly string[];
  readonly packageDigest: string;
  readonly relationshipDigest: string;
  readonly closureDigest: string;
  readonly inventory: PackageInventoryManifest;
  readonly preservationManifest: PackagePreservationManifest;
  readonly classification: PackagePartClassification;
  readonly macroDetected: boolean;
  readonly quarantineReasons: readonly string[];
}
export interface PackageMutationAuthorization {
  readonly generatedParts?: ReadonlySet<string> | readonly string[];
  readonly patchSliceParts?: ReadonlySet<string> | readonly string[];
  readonly policy?: Readonly<OoxmlSecurityPolicy>;
}
export interface PackagePreservationReceipt {
  readonly candidate: PackageGraph;
  readonly changedParts: readonly string[];
  readonly addedParts: readonly string[];
  readonly removedParts: readonly string[];
  readonly preservedExactPartCount: number;
  readonly patchSlicePartCount: number;
  readonly generatedPartCount: number;
}
export interface PackageGraphOptions {
  readonly generatedParts?: ReadonlySet<string> | readonly string[];
  readonly patchSliceParts?: ReadonlySet<string> | readonly string[];
  readonly policy?: Readonly<OoxmlSecurityPolicy>;
}
export interface PackageGraphPrecondition {
  readonly packageDigest?: string;
  readonly relationshipDigest?: string;
  readonly closureDigest?: string;
  readonly partDigests?: Readonly<Record<string, string>> | ReadonlyMap<string, string>;
}
export class PackageGraphError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = "PackageGraphError";
    this.code = code;
  }
}
export class PackageGraphPreconditionError extends PackageGraphError {
  readonly failures: readonly string[];
  constructor(failures: readonly string[]) {
    super("PRECONDITION_FAILED", `OOXML package precondition failed: ${failures.join(", ")}`);
    this.name = "PackageGraphPreconditionError";
    this.failures = Object.freeze([...failures]);
  }
}
export class PackagePreservationError extends PackageGraphError {
  readonly failures: readonly string[];
  constructor(failures: readonly string[]) {
    super("PRESERVATION_FAILED", `OOXML package preservation failed: ${failures.join(", ")}`);
    this.name = "PackagePreservationError";
    this.failures = Object.freeze([...failures]);
  }
}
