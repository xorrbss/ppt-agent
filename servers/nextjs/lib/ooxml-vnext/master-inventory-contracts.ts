import type { OoxmlSecurityPolicy } from "./security.ts";

export const MASTER_LAYOUT_INVENTORY_VERSION = "ooxml-master-layout-inventory/v1" as const;
export const CORPORATE_MASTER_OPAQUE_BUNDLE_VERSION = "ooxml-corporate-master-opaque/v1" as const;

export class MasterInventoryError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "MasterInventoryError";
    this.code = code;
  }
}

export interface PresentationMasterReference {
  readonly relationshipId: string;
  readonly presentationMasterId: string | null;
  readonly order: number | null;
}

export interface MasterLayoutReference {
  readonly relationshipId: string;
  readonly masterLayoutId: string | null;
  readonly layoutPartName: string;
  readonly order: number | null;
  readonly listedInMasterXml: boolean;
}

export interface SlideMasterInventoryRecord {
  readonly partName: string;
  readonly digest: string;
  readonly contentType: string | null;
  readonly name: string | null;
  readonly presentationReferences: readonly PresentationMasterReference[];
  readonly layouts: readonly MasterLayoutReference[];
  readonly relationshipPartName: string | null;
  readonly issueCodes: readonly string[];
}

export interface SlideLayoutInventoryRecord {
  readonly partName: string;
  readonly digest: string;
  readonly contentType: string | null;
  readonly name: string | null;
  readonly parentMasterParts: readonly string[];
  readonly forwardMasterRelationshipIds: readonly string[];
  readonly relationshipPartName: string | null;
  readonly issueCodes: readonly string[];
}

export interface MasterLayoutRelationshipRecord {
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

export interface MasterLayoutInventory {
  readonly version: typeof MASTER_LAYOUT_INVENTORY_VERSION;
  readonly packageDigest: string;
  readonly relationshipDigest: string;
  readonly masters: readonly SlideMasterInventoryRecord[];
  readonly layouts: readonly SlideLayoutInventoryRecord[];
  readonly structuralRelationships: readonly MasterLayoutRelationshipRecord[];
  readonly issueCodes: readonly string[];
}

export type CorporateMasterSelector =
  | { readonly partName: string }
  | { readonly presentationRelationshipId: string }
  | { readonly presentationMasterId: string };

export interface OpaqueCorporateMasterMember {
  readonly partName: string;
  readonly bytes: number;
  readonly sha256: string;
  readonly contentType: string | null;
  readonly payloadBase64: string;
}

export interface CorporateMasterPublicationPolicy {
  readonly mutationClassification: "PRESERVE_EXACT";
  readonly partNames: "IDENTITY_ONLY";
  readonly collisions: "ALLOW_IDENTICAL_REJECT_DIFFERENT";
  readonly externalRelationships: "PRESERVE_INERT_NEVER_DEREFERENCE";
  readonly missingRelationshipTargets: "REJECT";
}

export interface CorporateMasterOpaqueBundle {
  readonly version: typeof CORPORATE_MASTER_OPAQUE_BUNDLE_VERSION;
  readonly sourcePackageDigest: string;
  readonly sourceRelationshipDigest: string;
  readonly masterPartName: string;
  readonly masterDigest: string;
  readonly layoutPartNames: readonly string[];
  readonly closurePartNames: readonly string[];
  readonly members: readonly OpaqueCorporateMasterMember[];
  readonly relationships: readonly MasterLayoutRelationshipRecord[];
  readonly externalRelationshipCount: number;
  readonly publicationPolicy: CorporateMasterPublicationPolicy;
  readonly captureDigest: string;
}

export interface MasterInventoryOptions {
  readonly policy?: Readonly<OoxmlSecurityPolicy>;
}
