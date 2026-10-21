"use client";
import { useTranslations as useA2bTranslations } from "next-intl";

import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { RootState } from "@/store/store";
import { useDispatch, useSelector } from "react-redux";
import { setPresentationData, setPresentationId, setOutlines } from "@/store/slices/presentationGeneration";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { Button } from "@/components/ui/button";
import { GenerationPreparation } from "../../generation-workspace/GenerationPreparation";
import { useGenerationWorkspace } from "../../generation-workspace/GenerationWorkspaceProvider";
import OutlineContent from "./OutlineContent";
import EmptyStateView from "./EmptyStateView";

import { useOutlineStreaming } from "../hooks/useOutlineStreaming";
import { useOutlineManagement } from "../hooks/useOutlineManagement";
import { usePresentationGeneration } from "../hooks/usePresentationGeneration";
import {
  clearDurableGenerationTask,
  type DurableGenerationTask,
  findDurableGenerationTask,
  findResumableGenerationTask,
} from "../hooks/durableGenerationTask";
import MasterSelectionSummary from "./MasterSelectionSummary";
import ResumeGenerationBanner from "./ResumeGenerationBanner";
import { existingMasterApplyCas, record } from "./masterApplyCas";
import MasterDesignerDialog from "../../authored-vnext/master-setup/MasterDesignerDialog";
import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";
import type { MasterRevisionEnvelope } from "@/lib/authored-vnext/master/contracts";
import {
  generationMasterPin,
  masterSelectionFromPublished,
} from "@/lib/authored-vnext/master/selection-storage";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import { DashboardApi } from "../../services/api/dashboard";

const masterClient = new AuthoredVnextMasterClient();

const OutlinePage: React.FC<{
  auto?: boolean;
  initialPresentationId?: string;
  initialMasterSettingsOpen?: boolean;
}> = ({ auto = false, initialPresentationId, initialMasterSettingsOpen = false }) => {
  const tA2b = useA2bTranslations("outlineExtras");
  const { presentation_id, outlines } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const resolvedPresentationId = presentation_id || initialPresentationId || null;
  const workspace = useGenerationWorkspace();
  const [editingOutline, setEditingOutline] = useState(false);
  const outlineSnapshotRef = useRef<{ content: string }[]>([]);
  useEffect(() => {
    if (resolvedPresentationId && workspace?.session?.presentationId === resolvedPresentationId) workspace.bindPresentation(resolvedPresentationId);
  }, [resolvedPresentationId, workspace?.bindPresentation, workspace?.session?.presentationId]);
  const companyStandard = useSelector(
    (state: RootState) => state.pptGenUpload.companyStandard
  );
  const masterEnabled = isAuthoredVnextMasterEnabled();

  const [masterDesignerOpen, setMasterDesignerOpen] = useState(
    masterEnabled && initialMasterSettingsOpen
  );
  const [masterDefaultResolved, setMasterDefaultResolved] = useState(
    !masterEnabled
  );
  const [autoIntentState, setAutoIntentState] = useState<
    "checking" | "pending" | "none"
  >(auto ? "checking" : "none");
  const [autoIntentPresentationId, setAutoIntentPresentationId] = useState<string | null>(null);
  const autoIntentChecking = auto && (autoIntentState === "checking" ||
    autoIntentPresentationId !== resolvedPresentationId);
  const [sourceSvgAutoPresentationId, setSourceSvgAutoPresentationId] = useState<string | null>(null);
  const sourceSvgAutoReady = auto && !autoIntentChecking && autoIntentState === "pending" &&
    sourceSvgAutoPresentationId === resolvedPresentationId;
  const masterSelectionEpochRef = useRef(0);
  const openMasterDesigner = useCallback(() => setMasterDesignerOpen(true), []);

  // Custom hooks
  const dispatch = useDispatch();
  const router = useRouter();
  const handleMasterRequired = useCallback(() => {
    if (auto && resolvedPresentationId) {
      router.replace(`/outline?id=${encodeURIComponent(resolvedPresentationId)}`);
      return;
    }
    openMasterDesigner();
  }, [auto, openMasterDesigner, resolvedPresentationId, router]);
  // Resolve the immutable source intent before opening an outline stream.
  // Source SVG reads its content from the manifest, including after a reload.
  const streamState = useOutlineStreaming(
    autoIntentChecking || sourceSvgAutoReady
      ? null : resolvedPresentationId,
  );
  const { handleDragEnd, handleAddSlide } = useOutlineManagement(outlines);
  const { loadingState, handleSubmit, startRecovery } =
    usePresentationGeneration(
      resolvedPresentationId,
      outlines,
      handleMasterRequired,
    );

  useEffect(() => {
    if (!auto || !resolvedPresentationId) {
      setAutoIntentState("none");
      setAutoIntentPresentationId(null);
      setSourceSvgAutoPresentationId(null);
      return;
    }
    const staged = findDurableGenerationTask(resolvedPresentationId);
    autoSubmittedRef.current = false;
    autoRecoveryStartedRef.current = false;
    outlineStreamStartedRef.current = false;
    setAutoIntentPresentationId(resolvedPresentationId);
    const request = staged?.authoredRequest;
    const pin = request?.sourceContentManifestPin;
    setSourceSvgAutoPresentationId(
      staged?.mode === "svg" && staged.status === "starting" && !staged.taskId &&
      request?.strategy === "svg" && request.sourceMode === "source_content_restyle" &&
      typeof pin?.manifestId === "string" && pin.manifestId.trim().length > 0 &&
      /^sha256:[0-9a-f]{64}$/i.test(pin.digest) &&
      request.contentPolicy?.text === "verbatim" &&
      request.contentPolicy.allowSummarization === false
        ? resolvedPresentationId : null,
    );
    setAutoIntentState(
      staged?.status === "starting" && !staged.taskId ? "pending" : "none"
    );
    if (staged?.mode !== "authored") return;
  }, [auto, dispatch, resolvedPresentationId]);

  useEffect(() => {
    // Upload resolves the current published revision immediately before an
    // auto-generation click. Preserve only that exact document-scoped pin
    // through outline streaming; never recover a browser-wide prior choice.
    if (!masterEnabled || !resolvedPresentationId) {
      setMasterDefaultResolved(true);
      return;
    }
    const staged = auto
      ? findDurableGenerationTask(resolvedPresentationId)
      : null;
    const stagedPin =
      staged?.mode === "authored" ? staged.authoredRequest?.masterPin : undefined;
    if (stagedPin) {
      if (
        companyStandard?.masterId === stagedPin.masterId &&
        companyStandard.revision === stagedPin.revision &&
        companyStandard.digest === stagedPin.digest
      ) {
        setMasterDefaultResolved(true);
        return;
      }
      const epoch = ++masterSelectionEpochRef.current;
      void masterClient
        .getRevision(stagedPin.masterId, stagedPin.revision)
        .then((revision) => {
          if (masterSelectionEpochRef.current !== epoch) return;
          if (revision.digest !== stagedPin.digest) {
            dispatch(setPptGenUploadState({ companyStandard: null }));
            return;
          }
          dispatch(
            setPptGenUploadState({
              companyStandard: masterSelectionFromPublished(revision),
            })
          );
        })
        .catch(() => {
          if (masterSelectionEpochRef.current === epoch) {
            dispatch(setPptGenUploadState({ companyStandard: null }));
          }
        })
        .finally(() => {
          if (masterSelectionEpochRef.current === epoch) {
            setMasterDefaultResolved(true);
          }
        });
      return;
    }
    setMasterDefaultResolved(true);
  }, [auto, companyStandard, dispatch, masterEnabled, resolvedPresentationId]);

  const applyPublishedMaster = useCallback(
    async (published: MasterRevisionEnvelope) => {
      if (!resolvedPresentationId) return;
      const selection = masterSelectionFromPublished(published);
      ++masterSelectionEpochRef.current;
      const staged = findDurableGenerationTask(resolvedPresentationId);
      const stagedPin = staged?.authoredRequest?.masterPin;
      if (
        staged?.mode === "authored" &&
        !staged.taskId &&
        (stagedPin?.masterId !== selection.masterId ||
          stagedPin?.revision !== selection.revision ||
          stagedPin?.digest !== selection.digest)
      ) {
        clearDurableGenerationTask(staged.idempotencyKey);
      }
      if (initialMasterSettingsOpen) {
        const current = await DashboardApi.getPresentation(resolvedPresentationId);
        const cas = existingMasterApplyCas(current);
        if (!cas) {
          throw new Error(
            "현재 문서의 마스터 정보를 확인할 수 없어 적용하지 않았습니다."
          );
        }
        try {
          await masterClient.applyToPresentation(resolvedPresentationId, {
            masterPin: generationMasterPin(selection),
            expectedPresentationRevision: cas.presentationRevision,
            expectedDeckRevision: cas.deckRevision,
            expectedDeckDigest: cas.deckDigest,
            idempotencyKey: `master-apply:${resolvedPresentationId}:${cas.presentationRevision}:${selection.digest}`,
          });
        } catch {
          throw new Error(
            "마스터를 적용하지 못했습니다. 기존 프레젠테이션 버전은 그대로 유지되었습니다."
          );
        }
        const refreshed = await DashboardApi.getPresentation(resolvedPresentationId);
        if (refreshed) dispatch(setPresentationData(refreshed));
      }
      dispatch(setPptGenUploadState({ companyStandard: selection }));
    },
    [dispatch, initialMasterSettingsOpen, resolvedPresentationId]
  );
  const slideProgress = useMemo(() => {
    const progress = loadingState.progress;
    if (
      !progress ||
      !Number.isFinite(progress.completed_slides) ||
      !Number.isFinite(progress.total_slides) ||
      progress.total_slides <= 0
    ) {
      return null;
    }
    const completed = Math.min(
      progress.total_slides,
      Math.max(0, progress.completed_slides)
    );
    return {
      // A deck with every slide persisted is still incomplete while the server
      // is assembling/publishing the PPTX. The completed response navigates away.
      value: Math.min(
        99,
        Math.floor((completed / progress.total_slides) * 100)
      ),
      label: progress.finalizing
        ? `슬라이드 ${completed}/${progress.total_slides} 완료 · 최종 파일 생성 중`
        : `슬라이드 ${completed}/${progress.total_slides} 완료`,
    };
  }, [loadingState.progress]);

  // Auto-bridge: once the outline stream completes, submit exactly once.
  const autoSubmittedRef = useRef(false);
  const autoRecoveryStartedRef = useRef(false);
  const outlineStreamStartedRef = useRef(false);
  // Only auto-submit for a stream that actually ran THIS mount. If the outlines
  // were already in the store when this page mounted (back-navigation into a
  // completed auto flow), don't re-fire generation — show the outline instead.
  const outlinesPreexistingRef = useRef(outlines.length > 0);
  useEffect(() => {
    if (
      !auto ||
      autoIntentChecking ||
      autoIntentState !== "none" ||
      !resolvedPresentationId ||
      autoRecoveryStartedRef.current
    ) return;

    let cancelled = false;
    let recoveryTimer: ReturnType<typeof setTimeout> | null = null;
    const manualOutlineUrl = `/outline?id=${encodeURIComponent(resolvedPresentationId)}`;
    const recoverAutoEntry = () => {
      if (cancelled || autoRecoveryStartedRef.current) return;
      // The durable start marker is cleared as soon as the workspace is opened.
      // A reload/back-navigation can therefore revisit auto=1 with neither a
      // client task nor a selected template even though the server already
      // published the deck. Resolve that state from the canonical presentation
      // before the auto bridge can create another job.
      autoRecoveryStartedRef.current = true;
      autoSubmittedRef.current = true;
      void DashboardApi.getPresentation(resolvedPresentationId)
        .then((presentation) => {
          if (cancelled) return;
          const data = record(presentation);
          const publishedSlides = Array.isArray(data?.slides) ? data.slides : [];
          if (
            data?.lifecycle_status === "published" &&
            publishedSlides.length > 0
          ) {
            router.replace(
              `/presentation?id=${encodeURIComponent(resolvedPresentationId)}`
            );
            return;
          }
          router.replace(manualOutlineUrl);
        })
        .catch(() => {
          if (!cancelled) router.replace(manualOutlineUrl);
        });
    };

    if (streamState.isStreaming) {
      // A silent SSE connection used to keep auto=1 on an unconditional loader
      // forever. A missing one-shot intent is ambiguous, so fail closed after a
      // bounded wait and recover from the canonical presentation instead.
      recoveryTimer = setTimeout(recoverAutoEntry, 8_000);
    } else {
      recoverAutoEntry();
    }

    return () => {
      cancelled = true;
      if (recoveryTimer) clearTimeout(recoveryTimer);
    };
  }, [
    auto,
    autoIntentChecking,
    autoIntentState,
    resolvedPresentationId,
    router,
    streamState.isStreaming,
  ]);

  useEffect(() => {
    if (!auto || autoSubmittedRef.current) return;
    if (autoIntentChecking) return;
    if (!resolvedPresentationId) return;
    const staged = findDurableGenerationTask(resolvedPresentationId);
    const approvedRasterAuto = autoIntentState === "pending" &&
      staged?.status === "starting" && !staged.taskId &&
      staged.authoredRequest?.sourceMode === "raster_reconstruction" &&
      Boolean(staged.authoredRequest.rasterApprovalPin) &&
      staged.authoredRequest.referenceSnapshotIds?.length === 1;
    if (!approvedRasterAuto && !sourceSvgAutoReady) {
      if (outlinesPreexistingRef.current && autoIntentState !== "pending") return;
      if (streamState.isStreaming) return;
      if (!streamState.hasCompleted && autoIntentState !== "pending") return;
      if (!outlines || outlines.length === 0) return;
      if (masterEnabled && !masterDefaultResolved) return;
    }
    autoSubmittedRef.current = true;
    handleSubmit();
  }, [
    auto,
    autoIntentChecking,
    autoIntentState,
    sourceSvgAutoReady,
    resolvedPresentationId,
    streamState.isStreaming,
    streamState.hasCompleted,
    outlines,
    handleSubmit,
    masterEnabled,
    masterDefaultResolved,
  ]);

  useEffect(() => {
    if (
      !auto ||
      autoIntentChecking ||
      autoIntentState === "pending" ||
      !outlinesPreexistingRef.current ||
      !resolvedPresentationId ||
      autoRecoveryStartedRef.current
    ) return;
    router.replace(`/outline?id=${encodeURIComponent(resolvedPresentationId)}`);
  }, [auto, autoIntentChecking, autoIntentState, resolvedPresentationId, router]);

  useEffect(() => {
    if (streamState.isStreaming) {
      if (!autoIntentChecking) outlineStreamStartedRef.current = true;
      return;
    }
    if (
      !auto ||
      !resolvedPresentationId ||
      autoIntentChecking ||
      sourceSvgAutoReady ||
      !outlineStreamStartedRef.current ||
      streamState.hasCompleted ||
      autoRecoveryStartedRef.current
    ) return;
    // A terminal SSE close/error cannot remain on the progress-only route: it
    // has no controls by design. Preserve any partial outline and expose the
    // normal retry/review UI for this same presentation instead of hanging.
    router.replace(`/outline?id=${encodeURIComponent(resolvedPresentationId)}`);
  }, [
    auto,
    autoIntentChecking,
    autoIntentState,
    sourceSvgAutoReady,
    resolvedPresentationId,
    router,
    streamState.hasCompleted,
    streamState.isStreaming,
  ]);

  // Redux can be rebuilt during a reload or development hot update between
  // create admission and this route. Keep the durable presentation identity in
  // the URL and restore the client store without falling back to the empty view.
  useEffect(() => {
    if (presentation_id || !initialPresentationId) return;
    dispatch(setPresentationId(initialPresentationId));
  }, [presentation_id, initialPresentationId, dispatch]);

  // A reload rebuilds Redux from scratch (nothing is persisted and /outline
  // carries no id), which would strand an interrupted-but-resumable generation
  // behind the EmptyStateView. Rehydrate the outline shell from the stored
  // durable task; storage is client-only, so read it in an effect to keep the
  // server and hydration renders identical.
  useEffect(() => {
    if (resolvedPresentationId) return;
    const storedTask = findResumableGenerationTask();
    if (!storedTask?.sourcePresentationId) return;
    // The auto-bridge submit is only for an outline stream run by a fresh
    // compose flow; a rehydrated interrupted task must be resumed explicitly
    // through the banner, never auto-fired.
    autoSubmittedRef.current = true;
    dispatch(setPresentationId(storedTask.sourcePresentationId));
  }, [resolvedPresentationId, dispatch]);

  if (!resolvedPresentationId) {
    return <EmptyStateView />;
  }

  const preparing = streamState.isStreaming || streamState.isLoading || loadingState.isLoading;
  const streamFailed = !streamState.isStreaming && !streamState.isLoading && !outlines?.length;
  const awaitingAuto = auto && !loadingState.message;
  const status = loadingState.message || (streamState.isStreaming
    ? "자료를 분석하고 구성을 작성하고 있습니다."
    : streamFailed
      ? "구성을 불러오지 못했습니다. 다시 확인해 주세요."
      : awaitingAuto
        ? "슬라이드 제작을 준비하고 있습니다."
        : "아래 구성으로 발표자료를 만들까요?");
  const startOutlineEdit = () => {
    outlineSnapshotRef.current = outlines.map((item) => ({ ...item }));
    setEditingOutline(true);
  };
  const cancelOutlineEdit = () => {
    dispatch(setOutlines(outlineSnapshotRef.current));
    setEditingOutline(false);
  };

  return (
    <>
      <GenerationPreparation
        presentationId={resolvedPresentationId}
        status={status}
        busy={preparing || awaitingAuto}
        progressLabel={slideProgress?.label ?? (streamState.isStreaming && outlines.length > 0 ? `구성 ${outlines.length}장 작성됨` : undefined)}
        actions={!preparing && <>
          {streamFailed ? <Button variant="outline" onClick={streamState.retry}>{tA2b("reloadOutline")}</Button> : null}
          {!auto && outlines.length > 0 && !editingOutline && !startRecovery && <>
            <Button variant="outline" onClick={startOutlineEdit}>{tA2b("editOutline")}</Button>
            <Button onClick={() => void handleSubmit()}>{tA2b("createAsShown")}</Button>
          </>}
          {loadingState.message && startRecovery && !auto && <Button onClick={() => void handleSubmit()}>{startRecovery === "receipt" ? tA2b("checkAdmissionForTheSameRequest") : tA2b("retryTheSameRequest")}</Button>}
          {auto && loadingState.message && (startRecovery
            ? <Button onClick={() => void handleSubmit()}>{startRecovery === "receipt" ? tA2b("checkAdmissionForTheSameRequest") : tA2b("retryTheSameRequest")}</Button>
            : <Button variant="outline" onClick={() => router.replace(`/outline?id=${encodeURIComponent(resolvedPresentationId)}`)}>{tA2b("reviewRequest")}</Button>)}
          {editingOutline && <>
            <Button variant="outline" onClick={cancelOutlineEdit}>{tA2b("discardChanges")}</Button>
            <Button onClick={() => setEditingOutline(false)}>{tA2b("applyChanges")}</Button>
          </>}
        </>}
      >
        <ResumeGenerationBanner
          presentationId={resolvedPresentationId}
          suppressed={loadingState.isLoading || preparing}
          onResume={() => void handleSubmit()}
        />
        {!auto && masterEnabled && !preparing && !startRecovery && <MasterSelectionSummary
          templateName="AI 저작 스타일"
          selection={companyStandard}
          onOpen={openMasterDesigner}
        />}
        {editingOutline ? <div className="max-h-[48dvh] overflow-y-auto rounded-2xl border border-slate-200">
          <OutlineContent
            outlines={outlines}
            isLoading={streamState.isLoading}
            isStreaming={streamState.isStreaming}
            activeSlideIndex={streamState.activeSlideIndex}
            highestActiveIndex={streamState.highestActiveIndex}
            onDragEnd={handleDragEnd}
            onAddSlide={handleAddSlide}
            onRetry={streamState.retry}
          />
        </div> : outlines.length > 0 && <details open={!auto} className="rounded-2xl border border-slate-200 px-4 py-3">
          <summary className="cursor-pointer text-sm font-medium">{streamState.isStreaming ? tA2b("outlineInProgress") : tA2b("viewOutline")} · {outlines.length}{tA2b("slides")}</summary>
          <ol className="mt-3 max-h-[40dvh] list-decimal space-y-3 overflow-y-auto pl-5">
            {outlines.map((item, index) => <li key={index} className="whitespace-pre-wrap break-words text-sm leading-6 text-slate-600">{item.content}</li>)}
          </ol>
        </details>}
      </GenerationPreparation>
      {!auto && masterEnabled && <MasterDesignerDialog
        open={masterDesignerOpen}
        onOpenChange={setMasterDesignerOpen}
        onApplied={applyPublishedMaster}
        selectedMasterId={companyStandard?.masterId ?? null}
      />}
    </>
  );
};

export default OutlinePage;
