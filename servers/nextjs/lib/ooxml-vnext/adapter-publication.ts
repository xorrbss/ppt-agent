import { readFile } from "node:fs/promises";

import { readPptxArchive, writePptxArchive } from "../authored-hybrid/pptx-archive.ts";
import type {
  OoxmlAdapterSession,
  OoxmlReadBackReceipt,
  OoxmlSaveReceipt,
  PackageDigestPrecondition,
} from "./adapter-contracts.ts";
import {
  assertOperationPrecondition,
  graphFor,
  inspectionReceipt,
  preservationReceipt,
  slidePartNames,
} from "./adapter-session.ts";
import {
  assertDeckMasterReadBack,
  readDeckMasterParts,
  type DeckMasterReadBackReceipt,
} from "./master-compiler.ts";
import { readSlideSemanticMetadata } from "./materializer.ts";
import {
  assertPackagePreservation,
  buildPackageGraph,
  type PackageGraph,
} from "./package-graph.ts";
import { publishBufferedArtifact } from "./publication.ts";
import { inspectPptxPackageSecurity } from "./security.ts";

const MASTER_PART = "ppt/slideMasters/slideMaster1.xml";

export function readBackSession(session: OoxmlAdapterSession): OoxmlReadBackReceipt {
  const graph = graphFor(session);
  const preservation = preservationReceipt(session, graph);
  const slides = slidePartNames(session.entries).map((partName) => {
    try {
      const metadata = readSlideSemanticMetadata(session.entries.get(partName)!);
      return {
        partName,
        ...metadata,
      };
    } catch {
      return { partName, slideId: null, layoutId: null, masterDigest: null };
    }
  });
  let master: DeckMasterReadBackReceipt | null = null;
  if (session.entries.has(MASTER_PART)) {
    if (session.masterContract) {
      master = session.authoredMasterContract
        ? readDeckMasterParts(session.entries)
        : assertDeckMasterReadBack(session.masterContract, session.entries);
    } else {
      // Imported masters without vNext metadata stay opaque PRESERVE_EXACT parts.
      try {
        master = readDeckMasterParts(session.entries);
      } catch {
        master = null;
      }
    }
  }
  return Object.freeze({
    document: session.document,
    origin: session.origin,
    revision: session.revision,
    package: inspectionReceipt(graph),
    precondition: Object.freeze({
      revision: session.revision,
      packageDigest: graph.packageDigest,
      relationshipDigest: graph.relationshipDigest,
      closureDigest: graph.closureDigest,
    }),
    preservation,
    master,
    slides: Object.freeze(slides),
  });
}

export async function saveBufferedSession(
  session: OoxmlAdapterSession,
  destinationPath: string,
  precondition: PackageDigestPrecondition,
  expectedDestinationSha256?: string | null,
): Promise<OoxmlSaveReceipt> {
  const graph = graphFor(session);
  assertOperationPrecondition(session, graph, precondition);
  const preservation = preservationReceipt(session, graph);
  if (preservation.silentLossCount !== 0) {
    throw new Error("Imported package preservation precondition failed");
  }
  assertPackagePreservation(session.originalPreservationManifest, session.entries, {
    generatedParts: session.generatedParts,
    patchSliceParts: session.patchSliceParts,
  });
  // A true imported no-op retains source ZIP ordering and compression metadata.
  const bytes = session.origin === "IMPORTED" && session.revision === 0
    ? Buffer.from(session.originalArchiveBase64, "base64")
    : writePptxArchive(session.entries);
  let validatedGraph: PackageGraph | null = null;
  const publication = await publishBufferedArtifact({
    destinationPath,
    bytes,
    expectedDestinationSha256,
    validateAndReopen: async ({ path }) => {
      const reopened = inspectPptxPackageSecurity(await readFile(path));
      validatedGraph = buildPackageGraph(reopened.entries, {
        generatedParts: session.generatedParts,
        patchSliceParts: session.patchSliceParts,
      });
      if (validatedGraph.packageDigest !== graph.packageDigest) {
        throw new Error("Buffered OOXML candidate changed during archive serialization");
      }
      const readBackEntries = readPptxArchive(await readFile(path));
      if (session.masterContract) {
        if (session.authoredMasterContract) readDeckMasterParts(readBackEntries);
        else assertDeckMasterReadBack(session.masterContract, readBackEntries);
      }
    },
  });
  if (!validatedGraph) throw new Error("Buffered OOXML validation did not produce a graph");
  return Object.freeze({
    document: session.document,
    revision: session.revision,
    publication,
    readBack: readBackSession(session),
  });
}
