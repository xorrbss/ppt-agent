export {
  createOoxmlMasterCompilerPort,
  type ImportedDocumentReceipt,
  type OoxmlDocumentHandle,
  type OoxmlMasterCompilerPort,
  type OoxmlMutationReceipt,
  type OoxmlReadBackReceipt,
  type OoxmlSaveReceipt,
  type OriginalPackageInventoryReceipt,
  type OriginalPackagePartReceipt,
  type PackageDigestPrecondition,
  type PackageInspectionReceipt,
  type PreservationPartManifestEntry,
  type PreservationPartStatus,
  type SlideMaterializationRequest,
} from "./adapter.ts";
export {
  DECK_MASTER_CONTRACT_VERSION,
  normalizeDeckMasterContract,
  validateDeckMasterContract,
  type DeckMasterContract,
} from "./contracts.ts";
export {
  assertPackagePreservation,
  type PackagePartClassification,
  type PackageGraphPrecondition,
  type PackageInventoryManifest,
  type PackagePreservationManifest,
  type PackagePreservationReceipt,
} from "./package-graph.ts";
export {
  captureCorporateMaster,
  inventoryMasterLayouts,
  materializeCorporateMasterBundle,
  type CorporateMasterOpaqueBundle,
  type CorporateMasterSelector,
  type MasterLayoutInventory,
  type SlideLayoutInventoryRecord,
  type SlideMasterInventoryRecord,
} from "./master-inventory.ts";
export {
  GENOFFICE_LAB_ORACLE_CAPABILITIES,
  GENOFFICE_LAB_ORACLE_POLICY,
  evaluateGenOfficeCompliance,
} from "./compliance.ts";
export { loadGenOfficeBufferedNoopLabOracle } from "./genoffice-lab-oracle.ts";
export {
  classifyExternalRelationship,
  evaluateExternalRelationshipPolicy,
  planExternalRelationshipRemoval,
  type ExternalRelationshipDecision,
  type ExternalRelationshipPolicyReceipt,
} from "./external-relationship-policy.ts";
export type {
  StableTextSelector,
  TextSlicePatchRequest,
  TextSlicePatchReceipt,
} from "./text-slice-patcher.ts";
export type {
  MaterializedRichText,
  MaterializedTextRun,
  MaterializedTextValue,
} from "./materializer.ts";
