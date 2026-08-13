import type {
  DeckMasterContractV1,
  MasterDigest,
} from "@/lib/authored-vnext/master/contracts";
import type {
  GenerationDeckSnapshot,
  RegenerationCandidate,
} from "@/lib/authored-vnext/flow";
import { generationClient, serverOperationId } from "@/lib/authored-vnext/flow";

import { downloadArtifact, masterAssetDigests } from "./flow-client-view-model";

type Notice = (
  key:
    | "errors.reorder"
    | "errors.regeneration"
    | "errors.rollback"
    | "errors.candidate"
    | "errors.export",
  values: { code: string },
) => void;
type LoadDeck = (deck: GenerationDeckSnapshot) => void;
type NewOperationId = () => string;

export async function reorderDeck({
  deck,
  slideId,
  targetPosition,
  createOperationId,
  loadDeck,
  notice,
}: {
  deck: GenerationDeckSnapshot;
  slideId: string;
  targetPosition: number;
  createOperationId: NewOperationId;
  loadDeck: LoadDeck;
  notice: Notice;
}) {
  const ordered = deck.slides.map((item) => item.slide.slideId);
  const from = ordered.indexOf(slideId as `slide:${string}`);
  if (from < 0) return;
  const [moved] = ordered.splice(from, 1);
  ordered.splice(targetPosition, 0, moved);
  const operationId = serverOperationId(createOperationId());
  try {
    const result = await generationClient.reorder(
      deck.deckId,
      {
        operationId,
        deckId: deck.deckId,
        orderedSlideIds: ordered,
        masterDigest: deck.master.masterDigest,
        publicationFence: {
          expectedDeckRevision: deck.revision,
          expectedActiveRevisionDigest: deck.digest,
        },
      },
      `${operationId}:reorder`,
    );
    loadDeck(result.deck);
  } catch (error) {
    notice("errors.reorder", {
      code:
        error instanceof Error
          ? error.message
          : "authored_vnext_reorder_failed",
    });
  }
}

export async function prepareDeckRegeneration({
  deck,
  activeSlideId,
  body,
  createOperationId,
  setCandidate,
  notice,
}: {
  deck: GenerationDeckSnapshot;
  activeSlideId: string;
  body: string;
  createOperationId: NewOperationId;
  setCandidate: (candidate: RegenerationCandidate) => void;
  notice: Notice;
}) {
  const target = deck.slides.find(
    (item) => item.slide.slideId === activeSlideId,
  );
  if (!target) return;
  const operationId = serverOperationId(createOperationId());
  try {
    const response = await generationClient.prepareRegeneration(
      deck.deckId,
      activeSlideId,
      {
        operationId,
        deckId: deck.deckId,
        slideId: activeSlideId,
        expectedSlideRevision: target.slide.revision,
        expectedSlideSpecDigest: target.slide.specDigest,
        master: deck.master,
        sourceDigest: deck.sourceDigest,
        body,
        publicationFence: {
          expectedDeckRevision: deck.revision,
          expectedActiveRevisionDigest: deck.digest,
        },
      },
      `${operationId}:regenerate-prepare`,
    );
    setCandidate(response.candidate);
  } catch (error) {
    notice("errors.regeneration", {
      code:
        error instanceof Error
          ? error.message
          : "authored_vnext_regeneration_failed",
    });
  }
}

export async function rollbackDeck({
  deck,
  createOperationId,
  loadDeck,
  notice,
}: {
  deck: GenerationDeckSnapshot;
  createOperationId: NewOperationId;
  loadDeck: LoadDeck;
  notice: Notice;
}) {
  if (!deck.parentRevisionDigest || deck.revision <= 1) return;
  const operationId = serverOperationId(createOperationId());
  try {
    const result = await generationClient.rollback(
      deck.deckId,
      {
        operationId,
        deckId: deck.deckId,
        targetRevision: deck.revision - 1,
        targetRevisionDigest: deck.parentRevisionDigest,
        masterDigest: deck.master.masterDigest,
        publicationFence: {
          expectedDeckRevision: deck.revision,
          expectedActiveRevisionDigest: deck.digest,
        },
        reason: "workbench_undo",
      },
      `${operationId}:rollback`,
    );
    loadDeck(result.deck);
  } catch (error) {
    notice("errors.rollback", {
      code:
        error instanceof Error
          ? error.message
          : "authored_vnext_rollback_failed",
    });
  }
}

export async function publishDeckCandidate({
  deck,
  candidate,
  createOperationId,
  loadDeck,
  clearCandidate,
  clearBody,
  notice,
}: {
  deck: GenerationDeckSnapshot;
  candidate: RegenerationCandidate;
  createOperationId: NewOperationId;
  loadDeck: LoadDeck;
  clearCandidate: () => void;
  clearBody: () => void;
  notice: Notice;
}) {
  const operationId = serverOperationId(createOperationId());
  try {
    const result = await generationClient.publishRegeneration(
      deck.deckId,
      candidate.candidateDigest,
      operationId,
      `${operationId}:regenerate-publish`,
    );
    loadDeck(result.deck);
    clearCandidate();
    clearBody();
  } catch (error) {
    notice("errors.candidate", {
      code:
        error instanceof Error
          ? error.message
          : "authored_vnext_candidate_publish_failed",
    });
  }
}

export async function exportDeck({
  deck,
  contract,
  artifact,
  createOperationId,
  requested,
  queued,
  failed,
  setArtifact,
  notice,
}: {
  deck: GenerationDeckSnapshot;
  contract: DeckMasterContractV1 | undefined;
  artifact: { revision: number; digest: MasterDigest } | null;
  createOperationId: NewOperationId;
  requested: (id: string) => void;
  queued: (id: string) => void;
  failed: (id: string, code: string) => void;
  setArtifact: (artifact: { revision: number; digest: MasterDigest }) => void;
  notice: Notice;
}) {
  const operationId = serverOperationId(createOperationId());
  requested(operationId);
  try {
    const job = await generationClient.createExport({
      deckId: deck.deckId,
      expectedDeckRevision: deck.revision,
      expectedDeckDigest: deck.digest,
      expectedMasterRevision: deck.master.masterRevision,
      expectedMasterDigest: deck.master.masterDigest,
      expectedActiveArtifactRevision: artifact?.revision ?? 0,
      expectedActiveArtifactDigest: artifact?.digest ?? null,
      expectedAssetDigests: masterAssetDigests(contract),
      expectedFontDigest: deck.master.fontManifestDigest,
      idempotencyKey: `${operationId}:export`,
      operationFence: 1,
    });
    queued(operationId);
    const completed = await generationClient.runExport(job.jobId);
    if (completed.state !== "succeeded" || !completed.artifact)
      throw new Error(completed.errorCode ?? "authored_vnext_export_failed");
    setArtifact({
      revision: completed.artifact.publicationRevision,
      digest: completed.artifact.digest,
    });
    downloadArtifact(generationClient.artifactUrl(deck.deckId));
  } catch (error) {
    const code =
      error instanceof Error ? error.message : "authored_vnext_export_failed";
    notice("errors.export", { code });
    failed(operationId, code);
  }
}
