import type { DeckMasterContract, NormalizedDeckMasterContract } from "./contracts.ts";
import type { DeckMasterReadBackReceipt } from "./master-compiler.ts";
import type { DeckMasterBinaryAsset } from "./master-compiler-contracts.ts";
import type { MaterializedTextValue } from "./materializer.ts";
import type {
  CorporateMasterOpaqueBundle,
  CorporateMasterSelector,
  MasterLayoutInventory,
} from "./master-inventory.ts";
import type {
  PackageGraphPrecondition,
  PackagePartClassification,
  PackagePreservationManifest,
} from "./package-graph.ts";
import type { BufferedPublicationReceipt } from "./publication.ts";
import type { ExternalRelationshipDecision } from "./external-relationship-policy.ts";
import type { TextSlicePatchReceipt, TextSlicePatchRequest } from "./text-slice-patcher.ts";

export type OoxmlDocumentHandle = string & { readonly __ooxmlDocumentHandle: unique symbol };
export type OoxmlDocumentOrigin = "IMPORTED" | "GENERATED";

export interface PackageDigestPrecondition extends PackageGraphPrecondition {
  readonly revision: number;
}

export interface PackageInspectionReceipt {
  readonly classification: PackagePartClassification;
  readonly partClassifications: Readonly<Record<PackagePartClassification, number>>;
  readonly partCount: number;
  readonly slideCount: number;
  readonly masterCount: number;
  readonly layoutCount: number;
  readonly externalRelationshipCount: number;
  readonly externalRelationshipDecisions: readonly ExternalRelationshipDecision[];
  readonly externalRelationshipWarnings: readonly string[];
  readonly packageDigest: string;
  readonly relationshipDigest: string;
  readonly closureDigest: string;
  readonly macroDetected: boolean;
  readonly quarantineReasons: readonly string[];
}

export interface OriginalPackagePartReceipt {
  readonly name: string;
  readonly byteLength: number;
  readonly sha256: string;
}

export interface OriginalPackageInventoryReceipt {
  readonly schemaVersion: 1;
  readonly archiveSha256: string;
  readonly archiveByteLength: number;
  readonly packageDigest: string;
  readonly relationshipDigest: string;
  readonly closureDigest: string;
  readonly parts: readonly OriginalPackagePartReceipt[];
}

export type PreservationPartStatus =
  | "PRESERVED_EXACT"
  | "PATCHED_SLICE"
  | "GENERATED"
  | "QUARANTINED"
  | "REMOVED_APPROVED"
  | "MISSING"
  | "UNAPPROVED_CHANGE"
  | "UNAPPROVED_ADDITION";

export interface PreservationPartManifestEntry {
  readonly name: string;
  readonly classification: PackagePartClassification;
  readonly status: PreservationPartStatus;
  readonly originalSha256: string | null;
  readonly currentSha256: string | null;
}

export interface ImportedDocumentReceipt extends PackageInspectionReceipt {
  readonly document: OoxmlDocumentHandle;
  readonly origin: OoxmlDocumentOrigin;
  readonly revision: 0;
  readonly originalPackage: OriginalPackageInventoryReceipt;
  readonly originalPreservationManifest: PackagePreservationManifest;
}

export interface OoxmlMutationReceipt {
  readonly document: OoxmlDocumentHandle;
  readonly revision: number;
  readonly operation:
    | "COMPILE_MASTER"
    | "IMPORT_CORPORATE_MASTER"
    | "DELETE_MASTER"
    | "MATERIALIZE_SLIDE"
    | "PATCH_TEXT_SLICE";
  readonly package: PackageInspectionReceipt;
  readonly changedParts: readonly string[];
  readonly textPatch?: TextSlicePatchReceipt;
}

export interface SlideMaterializationRequest {
  readonly slideId: string;
  readonly layoutId: string;
  readonly values: Readonly<Record<string, MaterializedTextValue>>;
}

export interface PreservationReadBackReceipt {
  readonly schemaVersion: 1;
  readonly originalPackage: OriginalPackageInventoryReceipt;
  readonly baselinePartCount: number;
  readonly preservedExactPartCount: number;
  readonly generatedPartCount: number;
  readonly patchSlicePartCount: number;
  readonly missingParts: readonly string[];
  readonly unexpectedAddedParts: readonly string[];
  readonly unexpectedChangedParts: readonly string[];
  readonly silentLossCount: number;
  readonly parts: readonly PreservationPartManifestEntry[];
}

export interface OoxmlReadBackReceipt {
  readonly document: OoxmlDocumentHandle;
  readonly origin: OoxmlDocumentOrigin;
  readonly revision: number;
  readonly package: PackageInspectionReceipt;
  readonly precondition: PackageDigestPrecondition;
  readonly preservation: PreservationReadBackReceipt;
  readonly master: DeckMasterReadBackReceipt | null;
  readonly slides: readonly {
    readonly partName: string;
    readonly slideId: string | null;
    readonly layoutId: string | null;
    readonly masterDigest: string | null;
    readonly bodyGrammarDigest?: string;
    readonly bodyGrammarSchemaVersion?: string;
    readonly bodyGrammarArchetype?: string;
    readonly bodyGrammarSlideRole?: string;
    readonly referenceLayoutId?: string;
    readonly referenceLayoutDigest?: string;
    readonly referenceSourceDigest?: string;
    readonly resolvedTemplateId?: string;
    readonly resolvedCompositionDigest?: string;
    readonly resolvedRuleDigest?: string;
    readonly pageNumber?: number;
    readonly slideCount?: number;
    readonly semanticRolesUsed?: readonly string[];
    readonly layoutRolesUsed?: readonly string[];
  }[];
}

export interface OoxmlSaveReceipt {
  readonly document: OoxmlDocumentHandle;
  readonly revision: number;
  readonly publication: BufferedPublicationReceipt;
  readonly readBack: OoxmlReadBackReceipt;
}

export interface OoxmlMasterCompilerPort {
  inspect(bytes: Uint8Array): PackageInspectionReceipt;
  importPackage(bytes: Uint8Array): ImportedDocumentReceipt;
  readOriginalPackage(document: OoxmlDocumentHandle): Uint8Array;
  inventoryMasters(document: OoxmlDocumentHandle): MasterLayoutInventory;
  captureCorporateMaster(
    document: OoxmlDocumentHandle,
    selector: CorporateMasterSelector,
  ): CorporateMasterOpaqueBundle;
  createGenerated(
    contract: DeckMasterContract,
    assets?: readonly DeckMasterBinaryAsset[],
  ): ImportedDocumentReceipt;
  compileMaster(
    document: OoxmlDocumentHandle,
    contract: DeckMasterContract,
    precondition: PackageDigestPrecondition,
    assets?: readonly DeckMasterBinaryAsset[],
  ): OoxmlMutationReceipt;
  importCorporateMaster(
    document: OoxmlDocumentHandle,
    bundle: CorporateMasterOpaqueBundle,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt;
  deleteMaster(
    document: OoxmlDocumentHandle,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt;
  materializeSlide(
    document: OoxmlDocumentHandle,
    request: SlideMaterializationRequest,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt;
  targetedTextOperation(
    document: OoxmlDocumentHandle,
    request: TextSlicePatchRequest,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt;
  readBack(document: OoxmlDocumentHandle): OoxmlReadBackReceipt;
  saveBuffered(
    document: OoxmlDocumentHandle,
    destinationPath: string,
    precondition: PackageDigestPrecondition,
    expectedDestinationSha256?: string | null,
  ): Promise<OoxmlSaveReceipt>;
}

export interface OoxmlAdapterSession {
  readonly document: OoxmlDocumentHandle;
  readonly origin: OoxmlDocumentOrigin;
  readonly originalArchiveBase64: string;
  readonly originalPackage: OriginalPackageInventoryReceipt;
  readonly originalPreservationManifest: PackagePreservationManifest;
  readonly baselineDigests: Readonly<Record<string, string>>;
  readonly generatedParts: Set<string>;
  readonly patchSliceParts: Set<string>;
  readonly ownedMasterParts: Set<string>;
  entries: Map<string, Buffer>;
  revision: number;
  masterContract: NormalizedDeckMasterContract | null;
  authoredMasterContract: unknown | null;
  masterRelationshipId: string | null;
}
