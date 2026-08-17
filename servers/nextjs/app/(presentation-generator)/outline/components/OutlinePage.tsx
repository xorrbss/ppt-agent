"use client";

import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { RootState } from "@/store/store";
import { useDispatch, useSelector } from "react-redux";
import { setPresentationData, setPresentationId } from "@/store/slices/presentationGeneration";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { OverlayLoader } from "@/components/ui/overlay-loader";
import Wrapper from "@/components/Wrapper";
import OutlineContent from "./OutlineContent";
import EmptyStateView from "./EmptyStateView";
import GenerateButton from "./GenerateButton";

import { useOutlineStreaming } from "../hooks/useOutlineStreaming";
import { useOutlineManagement } from "../hooks/useOutlineManagement";
import { usePresentationGeneration } from "../hooks/usePresentationGeneration";
import {
  clearDurableGenerationTask,
  findDurableGenerationTask,
  findResumableGenerationTask,
} from "../hooks/durableGenerationTask";
import MasterSelectionSummary from "./MasterSelectionSummary";
import MasterDesignerDialog from "../../authored-vnext/master-setup/MasterDesignerDialog";
import { isAuthoredVnextMasterEnabled } from "@/lib/authored-vnext/master/contracts";
import type { MasterRevisionEnvelope } from "@/lib/authored-vnext/master/contracts";
import {
  loadLastUsedMasterId,
  generationMasterPin,
  masterSelectionFromPublished,
  saveLastUsedMasterId,
} from "@/lib/authored-vnext/master/selection-storage";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import { DashboardApi } from "../../services/api/dashboard";

const masterClient = new AuthoredVnextMasterClient();

const record = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const existingMasterApplyCas = (presentation: unknown) => {
  const data = record(presentation);
  const metadata = record(record(data?.theme)?.authored_vnext);
  const presentationRevision = data?.revision;
  const deckRevision = metadata?.deckRevision;
  const deckDigest = metadata?.deckDigest;
  if (
    !Number.isInteger(presentationRevision) ||
    !Number.isInteger(deckRevision) ||
    typeof deckDigest !== "string" ||
    !deckDigest.startsWith("sha256:")
  ) {
    return null;
  }
  return {
    presentationRevision: Number(presentationRevision),
    deckRevision: Number(deckRevision),
    deckDigest: deckDigest as `sha256:${string}`,
  };
};

const OutlinePage: React.FC<{
  auto?: boolean;
  initialPresentationId?: string;
  initialMasterSettingsOpen?: boolean;
}> = ({ auto = false, initialPresentationId, initialMasterSettingsOpen = false }) => {
  const { presentation_id, outlines } = useSelector(
    (state: RootState) => state.presentationGeneration
  );
  const resolvedPresentationId = presentation_id || initialPresentationId || null;
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
  const streamState = useOutlineStreaming(resolvedPresentationId);
  const { handleDragEnd, handleAddSlide } = useOutlineManagement(outlines);
  const { loadingState, handleSubmit, cancelGeneration } =
    usePresentationGeneration(
      resolvedPresentationId,
      outlines,
      handleMasterRequired,
    );

  useEffect(() => {
    if (!auto || !resolvedPresentationId) {
      setAutoIntentState("none");
      return;
    }
    const staged = findDurableGenerationTask(resolvedPresentationId);
    setAutoIntentState(
      staged?.status === "starting" && !staged.taskId ? "pending" : "none"
    );
    if (staged?.mode !== "authored") return;
  }, [auto, dispatch, resolvedPresentationId]);

  useEffect(() => {
    // Upload resolves the current published revision immediately before an
    // auto-generation click. Preserve that exact pin through outline streaming.
    // A direct reload has no Redux pin, so recover the preferred identity once
    // before allowing auto-submit instead of stranding the progress-only route.
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
    if (companyStandard) {
      setMasterDefaultResolved(true);
      return;
    }
    const lastUsedMasterId = loadLastUsedMasterId();
    const epoch = ++masterSelectionEpochRef.current;
    if (!lastUsedMasterId) {
      setMasterDefaultResolved(true);
      return;
    }
    void masterClient
      .getPublished(lastUsedMasterId)
      .then((published) => {
        if (!published || masterSelectionEpochRef.current !== epoch) return;
        dispatch(
          setPptGenUploadState({
            companyStandard: masterSelectionFromPublished(published),
          })
        );
      })
      .catch(() => {
        // A deleted, unpublished, or cross-owner id is not a valid default.
        // Keep the selection empty and let the catalog explain the next action.
      })
      .finally(() => {
        if (masterSelectionEpochRef.current === epoch) {
          setMasterDefaultResolved(true);
        }
      });
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
      saveLastUsedMasterId(selection.masterId);
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
  // Only auto-submit for a stream that actually ran THIS mount. If the outlines
  // were already in the store when this page mounted (back-navigation into a
  // completed auto flow), don't re-fire generation — show the outline instead.
  const outlinesPreexistingRef = useRef(outlines.length > 0);
  useEffect(() => {
    if (
      !auto ||
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
    autoIntentState,
    resolvedPresentationId,
    router,
    streamState.isStreaming,
  ]);

  useEffect(() => {
    if (!auto || autoSubmittedRef.current) return;
    if (autoIntentState === "checking") return;
    if (outlinesPreexistingRef.current && autoIntentState !== "pending") return;
    if (!resolvedPresentationId || streamState.isStreaming) return;
    if (!streamState.hasCompleted && autoIntentState !== "pending") return;
    if (!outlines || outlines.length === 0) return;
    if (masterEnabled && !masterDefaultResolved) return;
    autoSubmittedRef.current = true;
    handleSubmit();
  }, [
    auto,
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
      autoIntentState === "checking" ||
      autoIntentState === "pending" ||
      !outlinesPreexistingRef.current ||
      !resolvedPresentationId ||
      autoRecoveryStartedRef.current
    ) return;
    router.replace(`/outline?id=${encodeURIComponent(resolvedPresentationId)}`);
  }, [auto, autoIntentState, resolvedPresentationId, router]);

  useEffect(() => {
    if (
      !auto ||
      !resolvedPresentationId ||
      streamState.isStreaming ||
      streamState.hasCompleted ||
      autoRecoveryStartedRef.current
    ) return;
    // A terminal SSE close/error cannot remain on the progress-only route: it
    // has no controls by design. Preserve any partial outline and expose the
    // normal retry/review UI for this same presentation instead of hanging.
    router.replace(`/outline?id=${encodeURIComponent(resolvedPresentationId)}`);
  }, [
    auto,
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

  // Auto compose never exposes settings/editor controls. A deliberate review
  // uses the same route without auto=1 (including after cancelling generation).
  const autoWorking = auto;
  if (autoWorking) {
    const generating = loadingState.isLoading;
    const streamedOutlineCount = outlines?.length ?? 0;
    return (
      <div className="font-syne pb-9">
        <OverlayLoader
          show={true}
          // Use the real generation message + ETA (authored decks can take many
          // minutes) instead of a fixed 60s bar that parks at 95%.
          text={
            streamState.isStreaming
              ? streamedOutlineCount > 0
                ? `개요를 생성하는 중… ${streamedOutlineCount}장 작성됨`
                : "자료를 분석하고 개요를 구성하는 중…"
              : generating
                ? loadingState.message
                : "발표자료를 준비하는 중…"
          }
          // Outline generation has no deterministic completion percentage.
          // A timed bar used to park at 95% for large decks and looked hung.
          showProgress={generating}
          duration={generating ? loadingState.duration : 60}
          progress={generating ? slideProgress?.value : undefined}
          progressLabel={generating ? slideProgress?.label : undefined}
          // While generating: invalidate the run and fall back to the outline UI
          // so the user can review/edit and regenerate. While the outline is still
          // streaming: go back to compose (unmount closes the SSE stream).
          onCancel={
            generating
              ? () => {
                  cancelGeneration();
                  router.replace(
                    `/outline?id=${encodeURIComponent(resolvedPresentationId)}`
                  );
                }
              : streamedOutlineCount > 0
                ? () => router.replace(
                    `/outline?id=${encodeURIComponent(resolvedPresentationId)}`
                  )
                : () => router.back()
          }
          cancelText={generating ? "생성 취소" : "취소하고 돌아가기"}
        />
      </div>
    );
  }

  return (
    <div className=" font-syne  pb-9">

      <OverlayLoader
        show={loadingState.isLoading}
        text={loadingState.message}
        showProgress={loadingState.showProgress}
        duration={loadingState.duration}
        progress={slideProgress?.value}
        progressLabel={slideProgress?.label}
        onCancel={cancelGeneration}
        cancelText="생성 취소"
      />

      <Wrapper className="flex flex-col w-full relative px-5 sm:px-10 lg:px-20 ">
        <div className="w-full mx-auto">
          <div className="flex w-full flex-col pt-6">
            {!auto && masterEnabled && (
              <div className="mb-5 mt-2">
                <MasterSelectionSummary
                  templateName="AI 저작 스타일"
                  selection={companyStandard}
                  onOpen={openMasterDesigner}
                />
              </div>
            )}

            <div className="w-full mx-auto">
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
            </div>
          </div>

          <div className="fixed bottom-[26px] right-[26px] z-50">
            <GenerateButton
              loadingState={loadingState}
              streamState={streamState}
              onSubmit={handleSubmit}
            />
          </div>
        </div>



      </Wrapper>
      {!auto && masterEnabled && (
        <MasterDesignerDialog
          open={masterDesignerOpen}
          onOpenChange={setMasterDesignerOpen}
          onApplied={applyPublishedMaster}
          selectedMasterId={companyStandard?.masterId ?? null}
        />
      )}
    </div>
  );
};

export default OutlinePage;
