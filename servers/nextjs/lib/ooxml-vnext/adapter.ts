import { randomUUID } from "node:crypto";
import { writePptxArchive } from "../authored-hybrid/pptx-archive.ts";
import type { DeckMasterContract } from "./contracts.ts";
import { deckMasterRole, normalizeDeckMasterContract } from "./contracts.ts";
import type { DeckMasterBinaryAsset } from "./master-compiler-contracts.ts";
import {
  assertDeckMasterReadBack,
  compileDeckMasterParts,
  readDeckMasterParts,
} from "./master-compiler.ts";
import { materializeSlideParts } from "./materializer.ts";
import {
  captureCorporateMaster,
  inventoryMasterLayouts,
  materializeCorporateMasterBundle,
  type CorporateMasterOpaqueBundle,
  type CorporateMasterSelector,
  type MasterLayoutInventory,
} from "./master-inventory.ts";
import {
  buildPackageGraph,
  type PackageGraph,
} from "./package-graph.ts";
import {
  OOXML_CONTENT_TYPES,
  OOXML_RELATIONSHIP_TYPES,
  addPresentationMasterReference,
  addPresentationSlideReference,
  addRelationship,
  cloneEntries,
  createEmptyPresentationPackage,
  ensureContentTypeOverride,
  nextNumericId,
  nextPartNumber,
  removeContentTypeOverride,
  removePresentationReference,
  removeRelationship,
  setApplicationSlideCount,
} from "./package-ops.ts";
import { inspectPptxPackageSecurity } from "./security.ts";
import {
  patchTextSlice,
  type TextSlicePatchReceipt,
  type TextSlicePatchRequest,
} from "./text-slice-patcher.ts";
import { readBackSession, saveBufferedSession } from "./adapter-publication.ts";
import type {
  ImportedDocumentReceipt,
  OoxmlAdapterSession as Session,
  OoxmlDocumentHandle,
  OoxmlMasterCompilerPort,
  OoxmlMutationReceipt,
  OoxmlReadBackReceipt,
  OoxmlSaveReceipt,
  PackageDigestPrecondition,
  PackageInspectionReceipt,
  SlideMaterializationRequest,
} from "./adapter-contracts.ts";
import {
  asBuffer,
  assertOperationPrecondition,
  changedPartNames,
  graphFor,
  inspectionReceipt,
  originalPackageInventory,
  presentationMasterRelationshipId,
  slidePartNames,
} from "./adapter-session.ts";

export type {
  ImportedDocumentReceipt,
  OoxmlDocumentHandle,
  OoxmlDocumentOrigin,
  OoxmlMasterCompilerPort,
  OoxmlMutationReceipt,
  OoxmlReadBackReceipt,
  OoxmlSaveReceipt,
  OriginalPackageInventoryReceipt,
  OriginalPackagePartReceipt,
  PackageDigestPrecondition,
  PackageInspectionReceipt,
  PreservationPartManifestEntry,
  PreservationPartStatus,
  PreservationReadBackReceipt,
  SlideMaterializationRequest,
} from "./adapter-contracts.ts";

const PRESENTATION_PART = "ppt/presentation.xml";
const PRESENTATION_RELS = "ppt/_rels/presentation.xml.rels";
const MASTER_PART = "ppt/slideMasters/slideMaster1.xml";

class OoxmlMasterCompilerAdapter implements OoxmlMasterCompilerPort {
  readonly #sessions = new Map<OoxmlDocumentHandle, Session>();

  inspect(bytes: Uint8Array): PackageInspectionReceipt {
    const security = inspectPptxPackageSecurity(asBuffer(bytes));
    return inspectionReceipt(buildPackageGraph(security.entries));
  }

  importPackage(bytes: Uint8Array): ImportedDocumentReceipt {
    const originalArchive = Buffer.from(asBuffer(bytes));
    const security = inspectPptxPackageSecurity(originalArchive);
    const entries = cloneEntries(security.entries);
    const graph = buildPackageGraph(entries);
    const originalPackage = originalPackageInventory(originalArchive, graph);
    const document = randomUUID() as OoxmlDocumentHandle;
    const session: Session = {
      document,
      origin: "IMPORTED",
      originalArchiveBase64: originalArchive.toString("base64"),
      originalPackage,
      originalPreservationManifest: graph.preservationManifest,
      baselineDigests: graph.partDigests,
      generatedParts: new Set(),
      patchSliceParts: new Set(),
      ownedMasterParts: new Set(),
      entries,
      revision: 0,
      masterContract: null,
      masterRelationshipId: presentationMasterRelationshipId(entries),
    };
    this.#sessions.set(document, session);
    return Object.freeze({
      document,
      origin: session.origin,
      revision: 0 as const,
      originalPackage,
      originalPreservationManifest: graph.preservationManifest,
      ...inspectionReceipt(graph),
    });
  }

  readOriginalPackage(document: OoxmlDocumentHandle): Uint8Array {
    const session = this.#session(document);
    return Uint8Array.from(Buffer.from(session.originalArchiveBase64, "base64"));
  }

  inventoryMasters(document: OoxmlDocumentHandle): MasterLayoutInventory {
    return inventoryMasterLayouts(this.#session(document).entries);
  }

  captureCorporateMaster(
    document: OoxmlDocumentHandle,
    selector: CorporateMasterSelector,
  ): CorporateMasterOpaqueBundle {
    const session = this.#session(document);
    const graph = graphFor(session);
    if (graph.classification === "QUARANTINED") {
      throw new Error("Corporate masters cannot be captured from a quarantined package");
    }
    return captureCorporateMaster(session.entries, selector);
  }

  createGenerated(
    contract: DeckMasterContract,
    assets: readonly DeckMasterBinaryAsset[] = [],
  ): ImportedDocumentReceipt {
    const document = randomUUID() as OoxmlDocumentHandle;
    const entries = createEmptyPresentationPackage();
    const baseline = buildPackageGraph(entries);
    const originalArchive = writePptxArchive(entries);
    const originalPackage = originalPackageInventory(originalArchive, baseline);
    const session: Session = {
      document,
      origin: "GENERATED",
      originalArchiveBase64: originalArchive.toString("base64"),
      originalPackage,
      originalPreservationManifest: baseline.preservationManifest,
      baselineDigests: baseline.partDigests,
      generatedParts: new Set(entries.keys()),
      patchSliceParts: new Set(),
      ownedMasterParts: new Set(),
      entries,
      revision: 0,
      masterContract: null,
      masterRelationshipId: null,
    };
    this.#sessions.set(document, session);
    const before = graphFor(session);
    this.#compileMaster(session, contract, before, assets);
    session.revision = 0;
    const graph = graphFor(session);
    return Object.freeze({
      document,
      origin: session.origin,
      revision: 0 as const,
      originalPackage,
      originalPreservationManifest: baseline.preservationManifest,
      ...inspectionReceipt(graph),
    });
  }

  compileMaster(
    document: OoxmlDocumentHandle,
    contract: DeckMasterContract,
    precondition: PackageDigestPrecondition,
    assets: readonly DeckMasterBinaryAsset[] = [],
  ): OoxmlMutationReceipt {
    const session = this.#session(document);
    const before = graphFor(session);
    assertOperationPrecondition(session, before, precondition);
    if (session.origin !== "GENERATED") {
      throw new Error("Imported packages support exact preservation and targeted slice patches only");
    }
    this.#compileMaster(session, contract, before, assets);
    return this.#mutationReceipt(session, "COMPILE_MASTER", before);
  }

  importCorporateMaster(
    document: OoxmlDocumentHandle,
    bundle: CorporateMasterOpaqueBundle,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt {
    const session = this.#session(document);
    const before = graphFor(session);
    assertOperationPrecondition(session, before, precondition);
    if (session.origin !== "GENERATED") {
      throw new Error("Corporate masters can be attached only to adapter-generated packages");
    }
    if (session.masterRelationshipId || session.ownedMasterParts.size > 0) {
      throw new Error("Delete the current master before importing a corporate master");
    }
    if (slidePartNames(session.entries).length > 0) {
      throw new Error("Corporate master import requires a generated package with no slides");
    }
    const members = materializeCorporateMasterBundle(bundle, session.entries);
    return this.#transaction(session, () => {
      for (const member of bundle.members) {
        const bytes = members.get(member.partName);
        if (!bytes) throw new Error(`Corporate master member is missing: ${member.partName}`);
        session.entries.set(member.partName, Buffer.from(bytes));
        session.generatedParts.add(member.partName);
        session.ownedMasterParts.add(member.partName);
        if (member.contentType) {
          ensureContentTypeOverride(session.entries, member.partName, member.contentType);
        }
      }
      const relationshipId = addRelationship(session.entries, PRESENTATION_RELS, {
        type: OOXML_RELATIONSHIP_TYPES.master,
        target: bundle.masterPartName.replace(/^ppt\//, ""),
      });
      const presentationXml = session.entries.get(PRESENTATION_PART)!.toString("utf8");
      addPresentationMasterReference(
        session.entries,
        nextNumericId(presentationXml, "sldMasterId"),
        relationshipId,
      );
      session.masterRelationshipId = relationshipId;
      session.masterContract = null;
      for (const name of ["[Content_Types].xml", PRESENTATION_PART, PRESENTATION_RELS]) {
        session.generatedParts.add(name);
      }
      const inventory = inventoryMasterLayouts(session.entries);
      if (
        inventory.masters.length !== 1 ||
        inventory.masters[0].partName !== bundle.masterPartName ||
        inventory.layouts.length !== bundle.layoutPartNames.length ||
        inventory.issueCodes.length !== 0
      ) {
        throw new Error("Corporate master import read-back did not match its opaque bundle");
      }
      return this.#mutationReceipt(session, "IMPORT_CORPORATE_MASTER", before);
    });
  }

  deleteMaster(
    document: OoxmlDocumentHandle,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt {
    const session = this.#session(document);
    const before = graphFor(session);
    assertOperationPrecondition(session, before, precondition);
    if (session.origin !== "GENERATED" || !session.masterRelationshipId) {
      throw new Error("Only an adapter-generated master can be deleted");
    }
    if (slidePartNames(session.entries).length > 0) {
      throw new Error("A master with materialized slides cannot be deleted");
    }
    return this.#transaction(session, () => {
      removePresentationReference(session.entries, "sldMasterId", session.masterRelationshipId!);
      removeRelationship(session.entries, PRESENTATION_RELS, session.masterRelationshipId!);
      for (const partName of session.ownedMasterParts) {
        session.entries.delete(partName);
        session.generatedParts.delete(partName);
        removeContentTypeOverride(session.entries, partName);
      }
      session.ownedMasterParts.clear();
      session.masterContract = null;
      session.masterRelationshipId = null;
      return this.#mutationReceipt(session, "DELETE_MASTER", before);
    });
  }

  materializeSlide(
    document: OoxmlDocumentHandle,
    request: SlideMaterializationRequest,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt {
    const session = this.#session(document);
    const before = graphFor(session);
    assertOperationPrecondition(session, before, precondition);
    if (!session.masterContract) throw new Error("A compiled master is required");
    const metadata = readDeckMasterParts(session.entries).semanticMetadata;
    const layoutMetadata = metadata.layouts.find(({ id }) => id === request.layoutId);
    const layoutContract = session.masterContract.layouts.find(({ id }) => id === request.layoutId);
    if (!layoutMetadata || !layoutContract) throw new Error(`Unknown layout: ${request.layoutId}`);
    const slideNumber = nextPartNumber(
      session.entries,
      /^ppt\/slides\/slide(\d+)\.xml$/,
    );
    const slidePart = `ppt/slides/slide${slideNumber}.xml`;
    const slideRels = `ppt/slides/_rels/slide${slideNumber}.xml.rels`;
    const placeholders = layoutContract.placeholders.map((placeholder) => ({
      role: deckMasterRole(placeholder.id),
      type: placeholder.type === "content" ? ("obj" as const) : placeholder.type,
      idx: placeholder.index,
      fontFamily: session.masterContract!.fontManifest.primary,
      fontSizePt: placeholder.textStyle.fontPt,
      bold: placeholder.type === "title",
    }));
    const compiled = materializeSlideParts({
      slideId: request.slideId,
      layoutId: request.layoutId,
      layoutPartName: layoutMetadata.partName,
      placeholders,
      values: request.values,
      masterDigest: metadata.contractDigest,
      contractVersion: metadata.version,
    });
    return this.#transaction(session, () => {
      session.entries.set(slidePart, compiled.slideXml);
      session.entries.set(slideRels, compiled.relationshipsXml);
      ensureContentTypeOverride(session.entries, slidePart, OOXML_CONTENT_TYPES.slide);
      const relationshipId = addRelationship(session.entries, PRESENTATION_RELS, {
        type: OOXML_RELATIONSHIP_TYPES.slide,
        target: `slides/slide${slideNumber}.xml`,
      });
      const presentationXml = session.entries.get(PRESENTATION_PART)!.toString("utf8");
      addPresentationSlideReference(
        session.entries,
        nextNumericId(presentationXml, "sldId"),
        relationshipId,
      );
      setApplicationSlideCount(session.entries, slidePartNames(session.entries).length);
      for (const name of [slidePart, slideRels, "[Content_Types].xml", PRESENTATION_PART, PRESENTATION_RELS, "docProps/app.xml"]) {
        session.generatedParts.add(name);
      }
      return this.#mutationReceipt(session, "MATERIALIZE_SLIDE", before);
    });
  }

  targetedTextOperation(
    document: OoxmlDocumentHandle,
    request: TextSlicePatchRequest,
    precondition: PackageDigestPrecondition,
  ): OoxmlMutationReceipt {
    const session = this.#session(document);
    const before = graphFor(session);
    assertOperationPrecondition(session, before, precondition);
    if (session.patchSliceParts.has(request.partName)) {
      throw new Error(
        "An imported OOXML part may receive only one approved PATCH_SLICE operation per publication",
      );
    }
    const source = session.entries.get(request.partName);
    if (!source) throw new Error(`Targeted text part is missing: ${request.partName}`);
    const result = patchTextSlice(source, request);
    return this.#transaction(session, () => {
      session.entries.set(request.partName, Buffer.from(result.bytes));
      if (!session.generatedParts.has(request.partName)) session.patchSliceParts.add(request.partName);
      return this.#mutationReceipt(session, "PATCH_TEXT_SLICE", before, result.receipt);
    });
  }

  readBack(document: OoxmlDocumentHandle): OoxmlReadBackReceipt {
    return readBackSession(this.#session(document));
  }

  async saveBuffered(
    document: OoxmlDocumentHandle,
    destinationPath: string,
    precondition: PackageDigestPrecondition,
    expectedDestinationSha256?: string | null,
  ): Promise<OoxmlSaveReceipt> {
    return saveBufferedSession(
      this.#session(document),
      destinationPath,
      precondition,
      expectedDestinationSha256,
    );
  }

  #session(document: OoxmlDocumentHandle): Session {
    const session = this.#sessions.get(document);
    if (!session) throw new Error("Unknown OOXML document handle");
    return session;
  }

  #compileMaster(
    session: Session,
    contract: DeckMasterContract,
    before: PackageGraph,
    assets: readonly DeckMasterBinaryAsset[],
  ): void {
    const normalized = normalizeDeckMasterContract(contract);
    const compiled = compileDeckMasterParts(normalized, assets);
    const updating = session.entries.has(MASTER_PART);
    this.#transaction(session, () => {
      const compiledPartNames = new Set(compiled.parts.keys());
      if (updating) {
        for (const partName of session.ownedMasterParts) {
          if (compiledPartNames.has(partName)) continue;
          session.entries.delete(partName);
          session.generatedParts.delete(partName);
          removeContentTypeOverride(session.entries, partName);
          session.ownedMasterParts.delete(partName);
        }
      }
      for (const [partName, bytes] of compiled.parts) {
        if (!updating && session.entries.has(partName)) {
          throw new Error(`Generated master part collides with an existing package part: ${partName}`);
        }
        session.entries.set(partName, Buffer.from(bytes));
        session.generatedParts.add(partName);
        session.ownedMasterParts.add(partName);
      }
      for (const override of compiled.contentTypeOverrides) {
        ensureContentTypeOverride(session.entries, override.partName, override.contentType);
      }
      session.generatedParts.add("[Content_Types].xml");
      if (!updating) {
        const relationshipId = addRelationship(session.entries, PRESENTATION_RELS, {
          type: OOXML_RELATIONSHIP_TYPES.master,
          target: "slideMasters/slideMaster1.xml",
        });
        const presentationXml = session.entries.get(PRESENTATION_PART)!.toString("utf8");
        addPresentationMasterReference(
          session.entries,
          nextNumericId(presentationXml, "sldMasterId"),
          relationshipId,
        );
        session.masterRelationshipId = relationshipId;
        session.generatedParts.add(PRESENTATION_RELS);
        session.generatedParts.add(PRESENTATION_PART);
      }
      session.masterContract = normalized;
      assertDeckMasterReadBack(normalized, session.entries);
      const after = graphFor(session);
      if (before.classification === "QUARANTINED" || after.classification === "QUARANTINED") {
        throw new Error("Master compilation cannot cross a quarantine boundary");
      }
    });
  }

  #transaction<T>(session: Session, operation: () => T): T {
    const entries = cloneEntries(session.entries);
    const generatedParts = new Set(session.generatedParts);
    const patchSliceParts = new Set(session.patchSliceParts);
    const ownedMasterParts = new Set(session.ownedMasterParts);
    const masterContract = session.masterContract;
    const masterRelationshipId = session.masterRelationshipId;
    try {
      return operation();
    } catch (error) {
      session.entries = entries;
      session.generatedParts.clear();
      for (const name of generatedParts) session.generatedParts.add(name);
      session.patchSliceParts.clear();
      for (const name of patchSliceParts) session.patchSliceParts.add(name);
      session.ownedMasterParts.clear();
      for (const name of ownedMasterParts) session.ownedMasterParts.add(name);
      session.masterContract = masterContract;
      session.masterRelationshipId = masterRelationshipId;
      throw error;
    }
  }

  #mutationReceipt(
    session: Session,
    operation: OoxmlMutationReceipt["operation"],
    before: PackageGraph,
    textPatch?: TextSlicePatchReceipt,
  ): OoxmlMutationReceipt {
    const after = graphFor(session);
    session.revision += 1;
    return Object.freeze({
      document: session.document,
      revision: session.revision,
      operation,
      package: inspectionReceipt(after),
      changedParts: changedPartNames(before, after),
      ...(textPatch ? { textPatch } : {}),
    });
  }
}

export function createOoxmlMasterCompilerPort(): OoxmlMasterCompilerPort {
  return new OoxmlMasterCompilerAdapter();
}
