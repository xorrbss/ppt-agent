"use client";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  AuthoredWorkbench,
  type AuthoredWorkbenchCallbacks,
  type EditableHybridMvpReadiness,
  type NativeCapabilityProjectionV1,
  type WorkbenchPanelData,
  type WorkbenchPanelKind,
  type WorkbenchTypedCommand,
} from "@/components/authored-vnext";
import {
  authoredVnextFlowReducer,
  createAuthoredVnextFlowState,
  digestText,
  publicationPin,
  sceneMutationRequestForOperation,
  sceneForMaster,
  serverOperationId,
  slideLabel,
  generationClient,
  authoredVnextSlideCount,
  ZERO_DIGEST,
  type FlowEvent,
  type GenerationDeckSnapshot,
  type RegenerationCandidate,
} from "@/lib/authored-vnext/flow";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type {
  MasterDigest,
  MasterRevisionEnvelope,
  VisualDirection as MasterVisualDirection,
} from "@/lib/authored-vnext/master/contracts";
import {
  adaptDeckSnapshotToWorkbenchDocument,
  createEmptyWorkbenchState,
  lastKnownGood,
  selectActiveSlide,
  selectInspectorNode,
  selectNextWorkbenchEffect,
  workbenchReducer,
  type DeckDocument,
  type ElementId,
  type OperationId,
  type SlideId,
  type WorkbenchOperation,
} from "@/lib/authored-vnext/workbench";
import AuthoredVnextFlowStage from "./AuthoredVnextFlowStage";
import AuthoredPackageLifecycle from "./AuthoredPackageLifecycle";
import {
  exportDeck as exportDeckAction,
  prepareDeckRegeneration,
  publishDeckCandidate,
  reorderDeck,
  rollbackDeck,
} from "./flow-client-actions";
import {
  conflictView,
  inspectorView,
  regenerationCandidateView,
  restoredVisualDirection,
  roleHintForLayout,
} from "./flow-client-view-model";
import { confirmedDirectionEvents } from "./flow-client-outline";
import { flowClientCallbacks } from "./flow-client-callbacks";
export default function AuthoredVnextFlowClient() {
  const t = useTranslations("authoredVnext");
  const slideCount = authoredVnextSlideCount();
  const [flow, reduceFlow] = useReducer(
    (
      state: ReturnType<typeof createAuthoredVnextFlowState>,
      event: FlowEvent,
    ) => authoredVnextFlowReducer(state, event).state,
    undefined,
    () => createAuthoredVnextFlowState(slideCount),
  );
  const [workbench, dispatchWorkbench] = useReducer(
    workbenchReducer,
    undefined,
    createEmptyWorkbenchState,
  );
  const [prompt, setPrompt] = useState("");
  const [published, setPublished] = useState<MasterRevisionEnvelope | null>(
    null,
  );
  const [direction, setDirection] = useState<MasterVisualDirection | null>(
    null,
  );
  const [canonicalDeck, setCanonicalDeck] =
    useState<GenerationDeckSnapshot | null>(null);
  const [selectedElementId, setSelectedElementId] = useState<ElementId | null>(
    null,
  );
  const [panel, setPanel] = useState<WorkbenchPanelKind>("operations");
  const [notice, setNotice] = useState<string | null>(null);
  const [regenerationBody, setRegenerationBody] = useState("");
  const [candidate, setCandidate] = useState<RegenerationCandidate | null>(
    null,
  );
  const [railStart, setRailStart] = useState(0);
  const [artifact, setArtifact] = useState<{
    revision: number;
    digest: MasterDigest;
  } | null>(null);
  const [editableHybridMvpReadiness, setEditableHybridMvpReadiness] =
    useState<EditableHybridMvpReadiness | null>(null);
  const [nativeCapabilityProjection, setNativeCapabilityProjection] =
    useState<NativeCapabilityProjectionV1 | null>(null);
  const operationSequence = useRef(0);
  const restoreAttempted = useRef(false);
  const contract = published?.contract;
  const deck = lastKnownGood(workbench.deck);
  const activeSlide = selectActiveSlide(workbench);
  const selectedNode = selectedElementId
    ? selectInspectorNode(workbench, selectedElementId)
    : null;
  useEffect(() => {
    if (!canonicalDeck) {
      setEditableHybridMvpReadiness(null);
      setNativeCapabilityProjection(null);
      return;
    }
    const controller = new AbortController();
    void generationClient.editableHybridReadinessV4(canonicalDeck.deckId)
      .then((projection) => {
        if (!controller.signal.aborted) {
          setEditableHybridMvpReadiness(projection.editableHybridMvp);
          setNativeCapabilityProjection(projection.nativeCapabilities);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setEditableHybridMvpReadiness(null);
          setNativeCapabilityProjection(null);
        }
      });
    return () => controller.abort();
  }, [canonicalDeck?.deckId, canonicalDeck?.digest]);
  const scene =
    deck && contract
      ? sceneForMaster(deck, workbench.activeSlideId, contract, {
          header: t("scene.header"),
          footer: t("scene.footer"),
          logo: t("scene.logo"),
          divider: t("scene.divider"),
          pageNumber: t("scene.pageNumber"),
          title: t("scene.title"),
          body: t("scene.body"),
        })
      : null;
  const inspector = inspectorView(selectedNode, activeSlide);
  const panels: WorkbenchPanelData = useMemo(
    () => ({
      sources: [
        { id: "source:prompt", title: t("panels.prompt"), detail: prompt },
      ],
      history: workbench.undoStack.map((entry, index) => ({
        id: entry.operationId,
        title: t("panels.bodyPatch", { ordinal: index + 1 }),
        detail: entry.forward[0]?.kind ?? "patch",
        timestampLabel: t("panels.currentSession"),
      })),
      quality: candidate
        ? [
            {
              id: "quality:candidate",
              title: t("panels.candidateReady"),
              detail: t("panels.candidateReview"),
              severity: "info" as const,
            },
          ]
        : [],
      operations: workbench.outbox.map((entry) => ({
        id: entry.operation.operationId,
        label: entry.operation.kind,
        kind:
          entry.operation.kind === "text.replace"
            ? ("text.patch" as const)
            : entry.operation.kind === "geometry.update"
              ? ("geometry.patch" as const)
              : entry.operation.kind === "asset.set"
                ? ("asset.replace" as const)
                : entry.operation.kind === "data.set"
                  ? ("data.set" as const)
                : ("layout.apply" as const),
        status: entry.status,
        detail: entry.errorCode ?? t("panels.patchOutbox"),
      })),
    }),
    [candidate, prompt, t, workbench.outbox, workbench.undoStack],
  );
  const createOperationId = () =>
    `op:ui-${crypto.randomUUID()}-${++operationSequence.current}`;
  const loadCanonicalDeck = (
    snapshot: GenerationDeckSnapshot,
    resolvedContract = contract,
  ) => {
    if (!resolvedContract)
      throw new Error("authored_vnext_master_contract_missing");
    setCanonicalDeck(snapshot);
    dispatchWorkbench({
      type: "deck/loadSucceeded",
      deck: adaptDeckSnapshotToWorkbenchDocument(snapshot, resolvedContract),
    });
    const url = new URL(window.location.href);
    url.searchParams.set("authoredDeckId", snapshot.deckId);
    window.history.replaceState({}, "", url);
  };

  useEffect(() => {
    if (restoreAttempted.current) return;
    restoreAttempted.current = true;
    const deckId = new URL(window.location.href).searchParams.get(
      "authoredDeckId",
    );
    if (!deckId) return;
    const restore = async () => {
      const deckResponse = await generationClient.getDeck(deckId);
      const master = await new AuthoredVnextMasterClient().getRevision(
        deckResponse.deck.master.masterId,
        deckResponse.deck.master.masterRevision,
      );
      if (master.status !== "published")
        throw new Error("authored_vnext_master_not_published");
      setPublished(master);
      setDirection(master.visualDirection ?? null);
      loadCanonicalDeck(deckResponse.deck, master.contract);
      reduceFlow({
        type: "flow.loaded",
        checkpoint: {
          step: "workbench",
          materials: [],
          publishedMaster: {
            masterId: master.masterId,
            revision: master.revision,
            digest: master.digest,
            status: "published",
          },
          ...(master.visualDirection
            ? {
                visualDirection: restoredVisualDirection(
                  master.visualDirection,
                ),
              }
            : {}),
        },
        refreshedAt: new Date().toISOString(),
      });
    };
    void restore().catch((error) => {
      const code =
        error instanceof Error
          ? error.message
          : "authored_vnext_restore_failed";
      setNotice(t("errors.restore", { code }));
    });
  }, [t]);

  useEffect(() => {
    const effect = selectNextWorkbenchEffect(workbench);
    if (effect?.effect !== "dispatchOperation" || !canonicalDeck || !contract)
      return;
    const operation = effect.operation;
    dispatchWorkbench({
      type: "operation/sendStarted",
      operationId: operation.operationId,
    });
    const execute = async () => {
      if (!canonicalDeck.scene) {
        throw new Error("authored_vnext_scene_mutation_unavailable");
      }
      const mutationId = serverOperationId(
        operation.operationId,
        `-scene-r${canonicalDeck.revision}`,
      );
      const request = sceneMutationRequestForOperation(
        canonicalDeck.deckId,
        mutationId,
        operation,
      );
      const publishedResult = await generationClient.mutateScene(
        canonicalDeck.deckId,
        request.command,
        `${mutationId}:${request.idempotencyFence}`,
      );
      setCanonicalDeck(publishedResult.deck);
      dispatchWorkbench({
        type: "operation/acknowledged",
        operationId: operation.operationId,
        serverDeck: adaptDeckSnapshotToWorkbenchDocument(
          publishedResult.deck,
          contract,
        ),
      });
    };
    void execute().catch(async (error) => {
      const code =
        error instanceof Error
          ? error.message
          : "authored_vnext_typed_operation_failed";
      const status = error && typeof error === "object" && "status" in error
        ? (error as { status?: unknown }).status
        : undefined;
      const isCasConflict = status === 409 && /(?:publication fence|slide CAS|master changed|compatibility slide not found)/iu.test(code);
      if (isCasConflict) {
        dispatchWorkbench({
          type: "operation/conflicted",
          operationId: operation.operationId,
          code,
        });
      } else {
        try {
          const refreshed = await generationClient.getDeck(canonicalDeck.deckId);
          setCanonicalDeck(refreshed.deck);
          dispatchWorkbench({
            type: "operation/failed",
            operationId: operation.operationId,
            code,
            serverDeck: adaptDeckSnapshotToWorkbenchDocument(refreshed.deck, contract),
          });
        } catch {
          dispatchWorkbench({ type: "operation/failed", operationId: operation.operationId, code });
        }
      }
      setNotice(t("errors.operation", { code }));
    });
  }, [canonicalDeck, contract, t, workbench]);

  const dispatchFlow = (event: FlowEvent) => reduceFlow(event);
  const onPublished = (
    master: MasterRevisionEnvelope,
    visual: MasterVisualDirection,
  ) => {
    const draftRevision = Math.max(1, master.revision - 1);
    dispatchFlow({
      type: "master.draftSelected",
      draft: {
        masterId: master.masterId,
        revision: draftRevision,
        digest: master.digest,
        validation: { valid: true, errorCodes: [] },
      },
    });
    dispatchFlow({
      type: "master.publishSucceeded",
      published: {
        masterId: master.masterId,
        revision: master.revision,
        digest: master.digest,
        status: "published",
      },
    });
    setPublished(master);
    setDirection(visual);
  };

  const confirmDirection = () => {
    if (!direction) return;
    confirmedDirectionEvents(direction, prompt, {
      coverPurpose: t("outline.coverPurpose"),
      slidePurpose: (ordinal) => t("outline.slidePurpose", { ordinal }),
    }, slideCount).forEach(dispatchFlow);
  };

  const generate = async () => {
    if (!published || !flow.outlineLayout || !contract) return;
    setNotice(null);
    const operationId = serverOperationId(createOperationId());
    const deckId = `deck:${crypto.randomUUID()}` as const;
    dispatchFlow({
      type: "generation.requested",
      runId: operationId,
      deckId,
      expectedDeckRevision: 1,
      expectedDeckDigest: ZERO_DIGEST,
    });
    try {
      const master = await generationClient.masterPin(
        published.masterId,
        published.revision,
      );
      const sourceDigest = await digestText(prompt);
      const result = await generationClient.generate(
        {
          operationId,
          deckId,
          master,
          outline: {
            schemaVersion: "authored-outline/v1",
            sourceDigest,
            deckTitle: prompt.trim(),
            items: flow.outlineLayout.slides.map((item, index) => ({
              itemId: `outline:${index + 1}`,
              index,
              title: item.title,
              summary: item.purpose,
              roleHint: roleHintForLayout(item.layoutId),
            })),
          },
          qualityPolicyVersion: "authored-vnext-quality/v1",
          publicationFence: {
            expectedDeckRevision: 0,
            expectedActiveRevisionDigest: null,
          },
        },
        `${operationId}:generate`,
      );
      result.deck.slides.forEach((item, index) =>
        dispatchFlow({
          type: "generation.slideProgressed",
          progress: {
            slideId: item.slide.slideId,
            ordinal: index + 1,
            status: "ready",
            percent: 100,
            slideRevision: item.slide.revision,
          },
        }),
      );
      loadCanonicalDeck(result.deck);
      dispatchFlow({ type: "workbench.opened" });
    } catch (error) {
      const code =
        error instanceof Error
          ? error.message
          : "authored_vnext_generation_failed";
      setNotice(t("errors.generation", { code }));
      dispatchFlow({
        type: "generation.failed",
        error: { code, message: code, retryable: true },
      });
    }
  };

  const enqueueCommand = (command: WorkbenchTypedCommand) => {
    if (!deck || !activeSlide) return;
    const targetSlide = deck.slides.find((item) => item.id === command.slideId);
    const targetNode = targetSlide?.nodes.find((item) => item.id === command.elementId);
    if (!targetSlide || !targetNode || command.kind === "layout.apply") {
      setNotice(t("errors.unsupportedEdit"));
      return;
    }
    const common = {
      operationId: command.clientOperationId as OperationId,
      slideId: command.slideId as SlideId,
      elementId: command.elementId as ElementId,
      base: publicationPin(deck, command.slideId as SlideId),
    };
    const mvpMutation = command.kind === "text.style.patch"
      ? targetNode.kind === "text" ? { kind: "text.style.update", payload: command.patch } : null
      : command.kind === "shape.style.patch"
      ? targetNode.kind === "shape" ? { kind: "shape.style.update", payload: command.patch } : null
      : command.kind === "image.crop.patch"
        ? (targetNode.kind === "image" || targetNode.kind === "asset") ? { kind: "image.crop.update", payload: command.patch } : null
        : command.kind === "table.cell.patch"
          ? targetNode.kind === "table" ? { kind: "table.cell.update", payload: command.patch } : null
          : command.kind === "table.column-widths.patch"
            ? targetNode.kind === "table" ? { kind: "table.column-widths.update", payload: command.patch } : null
            : undefined;
    if (mvpMutation !== undefined) {
      if (!mvpMutation || !canonicalDeck) { setNotice(t("errors.unsupportedEdit")); return; }
      const operationId = serverOperationId(command.clientOperationId, "-editable-hybrid");
      void generationClient.mutateScene(canonicalDeck.deckId, {
        schemaVersion: "scene-mutation-command/v1", operationId, deckId: canonicalDeck.deckId,
        slideId: command.slideId, elementId: command.elementId, kind: mvpMutation.kind,
        payload: mvpMutation.payload, expectedSlideRevision: common.base.slideRevision,
        expectedSlideDigest: common.base.slideDigest, expectedSceneDigest: common.base.sceneDigest,
        publicationFence: { expectedDeckRevision: common.base.deckRevision, expectedActiveRevisionDigest: common.base.deckDigest },
      }, `${operationId}:${common.base.sceneDigest}`).then((result) => loadCanonicalDeck(result.deck)).catch((error) => setNotice(t("errors.operation", { code: error instanceof Error ? error.message : "authored_vnext_scene_mutation_failed" })));
      return;
    }
    let operation: WorkbenchOperation;
    if (command.kind === "text.patch" && targetNode.kind === "text") {
      operation = { ...common, kind: "text.replace", text: command.patch.text };
    } else if (command.kind === "geometry.patch") {
      operation = {
        ...common,
        kind: "geometry.update",
        bounds: { ...targetNode.bounds, ...command.patch },
      };
    } else if (
      command.kind === "asset.replace" &&
      (targetNode.kind === "image" || targetNode.kind === "asset")
    ) {
      operation = {
        ...common,
        kind: "asset.set",
        assetId: command.patch.assetId as `asset:${string}`,
        digest: command.patch.assetDigest as `sha256:${string}`,
        altText: command.patch.alt,
        crop: command.patch.crop,
        mediaType: command.patch.mediaType,
        storageReference: command.patch.storageReference,
        sizeBytes: command.patch.sizeBytes,
      };
    } else if (
      command.kind === "data.set" &&
      (targetNode.kind === "table" || targetNode.kind === "chart")
    ) {
      operation = {
        ...common,
        kind: "data.set",
        dataId: command.patch.dataId as `data:${string}`,
        dataDigest: command.patch.dataDigest as `sha256:${string}`,
        dataPayload: command.patch.dataPayload,
        rows: command.patch.rows,
        columns: command.patch.columns,
        chartType: command.patch.chartType,
        accessibilitySummary: command.patch.accessibilitySummary,
      };
    } else {
      setNotice(t("errors.unsupportedEdit"));
      return;
    }
    dispatchWorkbench({
      type: "operation/enqueue",
      operation,
      createdAt: new Date().toISOString(),
    });
  };

  const prepareRegeneration = async () => {
    if (!canonicalDeck || !activeSlide || !regenerationBody.trim()) return;
    if (workbench.outbox.length > 0) {
      setNotice(t("errors.flushBeforeRegenerate"));
      return;
    }
    await prepareDeckRegeneration({
      deck: canonicalDeck,
      activeSlideId: activeSlide.id,
      body: regenerationBody.trim(),
      createOperationId,
      setCandidate,
      notice: (key, values) => setNotice(t(key, values)),
    });
  };

  const rollbackLatest = async () => {
    if (!canonicalDeck || !contract) return;
    await rollbackDeck({
      deck: canonicalDeck,
      createOperationId,
      loadDeck: loadCanonicalDeck,
      notice: (key, values) => setNotice(t(key, values)),
    });
  };

  const acceptCandidate = async () => {
    if (!candidate || !canonicalDeck || !contract) return;
    await publishDeckCandidate({
      deck: canonicalDeck,
      candidate,
      createOperationId,
      loadDeck: loadCanonicalDeck,
      clearCandidate: () => setCandidate(null),
      clearBody: () => setRegenerationBody(""),
      notice: (key, values) => setNotice(t(key, values)),
    });
  };

  const exportDeck = async () => {
    const deckId = canonicalDeck?.deckId ?? new URL(window.location.href)
      .searchParams.get("authoredDeckId");
    if (!deckId) return;
    try {
      // Re-read the publication head at click time.  Workbench commands update
      // React and SQLite asynchronously; exporting a render-time closure can
      // otherwise submit the pre-save revision or return early after restore.
      const current = await generationClient.getDeck(deckId);
      setCanonicalDeck(current.deck);
      const readiness = await generationClient.editableHybridReadinessV4(deckId);
      setEditableHybridMvpReadiness(readiness.editableHybridMvp);
      setNativeCapabilityProjection(readiness.nativeCapabilities);
      await exportDeckAction({
        deck: current.deck,
        contract,
        artifact,
        createOperationId,
        requested: (requestId) =>
          dispatchFlow({ type: "export.requested", requestId, format: "pptx" }),
        queued: (requestId) => dispatchFlow({ type: "export.queued", requestId }),
        failed: (requestId, code) =>
          dispatchFlow({
            type: "export.failed",
            requestId,
            error: { code, message: code, retryable: true },
          }),
        setArtifact,
        notice: (key, values) => setNotice(t(key, values)),
        editableHybridMvpReadiness: readiness.editableHybridMvp,
      });
    } catch (error) {
      setNotice(t("errors.export", {
        code: error instanceof Error ? error.message : "authored_vnext_export_failed",
      }));
    }
  };

  const callbacks = flowClientCallbacks({ accept: () => void acceptCandidate(), canonicalDeck, contract, createOperationId, dispatch: dispatchWorkbench, enqueue: enqueueCommand, exportDeck: () => void exportDeck(), onDiscardError: (code) => setNotice(t("errors.discard", { code })), reject: () => setCandidate(null), reorder: (slideId, target) => { if (canonicalDeck) void reorderDeck({ deck: canonicalDeck, slideId, targetPosition: target, createOperationId, loadDeck: loadCanonicalDeck, notice: (key, values) => setNotice(t(key, values)) }); }, rollback: () => void rollbackLatest(), setCanonicalDeck, setSelectedElementId, workbench });
  if (!deck && flow.step === "workbench") return <AuthoredVnextFlowStage flow={flow} prompt={prompt} notice={notice} direction={direction} regenerationBody={regenerationBody} workbench={null} onPromptChange={setPrompt} onFlowEvent={dispatchFlow} onPublished={onPublished} onConfirmDirection={confirmDirection} onGenerate={() => void generate()} onRegenerationBodyChange={setRegenerationBody} onPrepareRegeneration={() => void prepareRegeneration()} />;
  const activeBodyNode = activeSlide?.nodes.find((node) => node.kind === "text" && node.role === "body");
  const candidateView = regenerationCandidateView(candidate, activeSlide, activeBodyNode?.kind === "text" ? activeBodyNode.text : "", t("regeneration.candidateLabel"), t("regeneration.candidateSummary"));
  const conflict = conflictView(workbench.conflict, canonicalDeck?.revision, deck?.revision, t("conflict.message"));
  const workbenchView = deck ? <AuthoredWorkbench activePanel={panel} activeSlideId={workbench.activeSlideId} callbacks={callbacks} canRedo={workbench.redoStack.length > 0} canUndo={workbench.undoStack.length > 0 || Boolean(canonicalDeck?.parentRevisionDigest)} conflict={conflict} createOperationId={createOperationId} editableHybridMvpReadiness={editableHybridMvpReadiness} featureEnabled inspector={inspector} nativeCapabilityProjection={nativeCapabilityProjection} onPanelChange={setPanel} onRailWindowChange={setRailStart} panels={panels} railWindow={{ startIndex: railStart, visibleCount: 8, rowHeight: 88, overscan: 2 }} regenerationCandidate={candidateView} scene={scene} selectedElementId={selectedElementId} slides={deck.slides.map((slide, index) => ({ id: slide.id, title: slideLabel(slide), position: index, revision: slide.revision, status: "ready" }))} state={workbench.deck.status === "stale" ? { state: "stale", title: t("conflict.staleTitle"), message: workbench.deck.reason } : { state: "ready" }} /> : null;
  return <><AuthoredVnextFlowStage flow={flow} prompt={prompt} notice={notice} direction={direction} regenerationBody={regenerationBody} workbench={workbenchView} onPromptChange={setPrompt} onFlowEvent={dispatchFlow} onPublished={onPublished} onConfirmDirection={confirmDirection} onGenerate={() => void generate()} onRegenerationBodyChange={setRegenerationBody} onPrepareRegeneration={() => void prepareRegeneration()} />{contract && <AuthoredPackageLifecycle masterContract={contract as unknown as Record<string, unknown>} />}</>;
}
