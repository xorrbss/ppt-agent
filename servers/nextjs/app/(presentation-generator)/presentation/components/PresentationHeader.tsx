"use client";
import { Loader2, AlertTriangle } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useDispatch, useSelector } from "react-redux";

import { RootState } from "@/store/store";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { usePresentationUndoRedo } from "../hooks/PresentationUndoRedo";
import ToolTip from "@/components/ToolTip";
import {
  clearPresentationData,
  updateTitle,
} from "@/store/slices/presentationGeneration";
import { clearHistory } from "@/store/slices/undoRedoSlice";
import type { PresentationExportQualityReport } from "@/lib/presentation-export-quality";
import GenerationInfoDialog from "./GenerationInfoDialog";
import { cn } from "@/lib/utils";
import { notify } from "@/components/ui/sonner";
import { KeyboardShortcutsDialog } from "./KeyboardShortcutsDialog";
import AuthoredHeaderActions from "./header/AuthoredHeaderActions";
import ExportMenu from "./header/ExportMenu";
import ExportQualityDialog from "./header/ExportQualityDialog";
import HybridCoverageDialog, {
  type HybridCoverageManifest,
} from "./header/HybridCoverageDialog";
import HybridExportHistoryDialog from "./header/HybridExportHistoryDialog";
import HeaderToolbar from "./header/HeaderToolbar";
import DashboardNavigationButton from "./header/DashboardNavigationButton";
import PresentationTitleEditor from "./header/PresentationTitleEditor";
import RegenerateConfirmDialog from "./header/RegenerateConfirmDialog";
import type { PptxExportMode } from "./header/types";
import AuthoredEmissionCapabilityStatusView, {
  authoredHybridUnavailableReason,
  type AuthoredEmissionCapabilityStatus,
} from "./header/AuthoredEmissionCapabilityStatus";
import MasterLayoutBadge from "./header/MasterLayoutBadge";
import {
  masterPinCommandContext,
  presentationEditableHybridWorkbench,
  presentationMasterPin,
} from "../utils/masterPresentation";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../workspace";
import {
  getHybridExportHistory,
  type HybridExportHistory,
} from "../../services/api/hybrid-export-history";
import {
  getAuthoredEmissionReadinessStatus,
} from "../../services/api/authored-emission-capability";

export interface PresentationHeaderFeatureSlots {
  /** Computed by PresentationShell's server-issued capability projection. */
  readonly showAuthoredActions: boolean;
  readonly exportActions: boolean;
}

const PresentationHeader = ({
  presentation_id,
  isPresentationSaving,
  isPresentationSaveFailed = false,
  currentSlide,
  isAuthoredDeck = false,
  mutationsEnabled = true,
  workspaceCommandsEnabled = false,
  onWorkspaceCommand,
  onWorkspaceQualityReviewRequest,
  onWorkspacePageRemakeRequest,
  onCanonicalSaved,
  headerFeatures,
}: {
  presentation_id: string;
  isPresentationSaving: boolean;
  isPresentationSaveFailed?: boolean;
  currentSlide?: number;
  isAuthoredDeck?: boolean;
  mutationsEnabled?: boolean;
  workspaceCommandsEnabled?: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onWorkspaceQualityReviewRequest?: WorkspaceQualityReviewRequester;
  onWorkspacePageRemakeRequest?: WorkspacePageRemakeRequester;
  onCanonicalSaved?: () => void | Promise<void>;
  headerFeatures?: PresentationHeaderFeatureSlots;
}) => {
  const [shortcutsDialogOpen, setShortcutsDialogOpen] = useState(false);
  const router = useRouter();
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [isRegenerateConfirmOpen, setIsRegenerateConfirmOpen] = useState(false);
  const [exportQuality, setExportQuality] =
    useState<PresentationExportQualityReport | null>(null);
  const [hybridCoverage, setHybridCoverage] =
    useState<HybridCoverageManifest | null>(null);
  const [hybridExportHistory, setHybridExportHistory] =
    useState<HybridExportHistory | null>(null);
  const [hybridExportHistoryLoading, setHybridExportHistoryLoading] =
    useState(false);
  const [hybridExportHistoryError, setHybridExportHistoryError] =
    useState<string | null>(null);
  const [authoredCapabilityStatus, setAuthoredCapabilityStatus] =
    useState<AuthoredEmissionCapabilityStatus>({ state: "checking" });
  const [directExportInFlight, setDirectExportInFlight] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  /** Avoid committing on blur when Save/Cancel was used (focus/click ordering) */
  const titleBlurIntentRef = useRef<"none" | "save" | "cancel">("none");

  const pathname = usePathname();
  const dispatch = useDispatch();

  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration,
  );
  const appliedMasterPin = isAuthoredDeck
    ? presentationMasterPin(presentationData)
    : null;
  const editableHybridWorkbench = isAuthoredDeck
    ? presentationEditableHybridWorkbench(presentationData)
    : null;
  const expectedRevision =
    typeof presentationData?.revision === "number"
      ? presentationData.revision
      : undefined;

  useEffect(() => {
    if (!isAuthoredDeck) return;
    const controller = new AbortController();
    setAuthoredCapabilityStatus({ state: "checking" });
    getAuthoredEmissionReadinessStatus(presentation_id, controller.signal)
      .then((readiness) => {
        if (!controller.signal.aborted) {
          setAuthoredCapabilityStatus({ state: "ready", readiness });
        }
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setAuthoredCapabilityStatus({
          state: "unavailable",
          reason: error instanceof Error ? error.message : undefined,
        });
      });
    return () => controller.abort();
  }, [expectedRevision, isAuthoredDeck, presentation_id]);

  const hybridDisabledReason = isAuthoredDeck
    ? authoredHybridUnavailableReason(authoredCapabilityStatus, expectedRevision)
    : undefined;
  // Compatibility facade for isolated header consumers. The presentation page
  // always supplies the server-derived projection; this fallback is removable
  // once those stories/tests consume PresentationShell as well.
  const resolvedHeaderFeatures: PresentationHeaderFeatureSlots =
    headerFeatures ?? {
      showAuthoredActions: isAuthoredDeck,
      exportActions: true,
    };

  const workspaceHistoryEnabled =
    workspaceCommandsEnabled && Boolean(onWorkspaceCommand);
  const historyEnabled = workspaceHistoryEnabled;
  const { onUndo, onRedo, canUndo, canRedo } = usePresentationUndoRedo(
    false,
    workspaceHistoryEnabled ? onWorkspaceCommand : undefined,
  );

  useEffect(() => {
    if (isEditingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [isEditingTitle]);

  useEffect(() => {
    if (!mutationsEnabled) {
      setIsEditingTitle(false);
      setIsRegenerateConfirmOpen(false);
    }
  }, [mutationsEnabled]);

  const beginTitleEdit = () => {
    if (!mutationsEnabled || isStreaming || !presentationData) return;
    setDraftTitle(presentationData.title || "");
    setIsEditingTitle(true);
  };

  const commitTitleEdit = () => {
    if (!mutationsEnabled) {
      setIsEditingTitle(false);
      return;
    }
    if (!presentationData) {
      setIsEditingTitle(false);
      return;
    }
    const trimmed = draftTitle.trim();
    const next = trimmed || presentationData.title || "발표자료";
    if (next !== presentationData.title) {
      dispatch(updateTitle(next));
      trackEvent(MixpanelEvent.Presentation_Title_Updated, {
        pathname,
        presentation_id,
        previous_title_length: (presentationData.title || "").length,
        next_title_length: next.length,
      });
    }
    setIsEditingTitle(false);
  };

  const cancelTitleEdit = () => {
    setDraftTitle(presentationData?.title || "");
    setIsEditingTitle(false);
  };

  const handleTitleBlur = () => {
    queueMicrotask(() => {
      const intent = titleBlurIntentRef.current;
      titleBlurIntentRef.current = "none";
      if (intent === "cancel" || intent === "save") return;
      commitTitleEdit();
    });
  };

  const onTitleSaveMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    titleBlurIntentRef.current = "save";
  };

  const onTitleCancelMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    titleBlurIntentRef.current = "cancel";
  };

  const handleExportPptx = async (
    pptxMode?: PptxExportMode,
    fontEmbedding?: boolean,
  ) => {
    if (
      isStreaming ||
      directExportInFlight ||
      !workspaceCommandsEnabled ||
      !onWorkspaceCommand
    )
      return;
    if (pptxMode === "hybrid" && hybridDisabledReason !== undefined) return;
    if (isAuthoredDeck && (pptxMode === "hybrid" || pptxMode === "fidelity")) {
      setDirectExportInFlight(true);
      const exportLabel = pptxMode === "hybrid" ? "Hybrid PPTX" : "Fidelity PPTX";
      const notificationId = notify.loading(
        `${exportLabel}를 준비하고 있습니다`,
        "완료되면 다운로드가 자동으로 시작됩니다.",
      );
      try {
        // A caller must explicitly choose false to request the compatibility
        // path. Hybrid exports otherwise request embedding by default.
        const requestedFontEmbedding =
          pptxMode === "hybrid" && fontEmbedding !== false;
        const response = await fetch("/api/export-presentation", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            format: "pptx",
            id: presentation_id,
            title: presentationData?.title ?? "presentation",
            pptxMode,
            ...(pptxMode === "hybrid"
              ? { fontEmbedding: requestedFontEmbedding }
              : {}),
            ...(expectedRevision !== undefined
              ? { expectedPresentationRevision: expectedRevision }
              : {}),
          }),
        });
        const result = (await response.json().catch(() => null)) as {
          success?: boolean;
          path?: string;
          quality?: PresentationExportQualityReport;
          powerPointQa?: PresentationExportQualityReport["powerPointQa"];
          exportMetadata?: { coverageManifest?: HybridCoverageManifest };
          error?: string;
        } | null;
        const coverage = result?.exportMetadata?.coverageManifest;
        const quality =
          result?.quality && result.powerPointQa
            ? { ...result.quality, powerPointQa: result.powerPointQa }
            : result?.quality;
        if (!response.ok || result?.success !== true || !result.path) {
          if (quality) setExportQuality(quality);
          else if (coverage?.mode === "hybrid") setHybridCoverage(coverage);
          throw new Error(result?.error ?? "hybrid_export_failed");
        }
        const embedding = quality?.fontEmbeddingStatus;
        if (
          pptxMode === "hybrid" &&
          requestedFontEmbedding &&
          (!embedding?.requested || !embedding.applied)
        ) {
          // Never treat an unaudited/failed embedding result as a successful
          // font-preserving download, even if an older server returned a path.
          if (quality) setExportQuality(quality);
          throw new Error("font_embedding_not_applied");
        }
        if (quality) setExportQuality(quality);
        if (coverage?.mode === "hybrid") setHybridCoverage(coverage);
        const anchor = window.document.createElement("a");
        anchor.href = result.path;
        anchor.download = "";
        window.document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        notify.success(
          `${exportLabel} 다운로드를 시작했습니다`,
          "브라우저의 다운로드 목록에서 파일을 확인하세요.",
          { id: notificationId },
        );
      } catch (error) {
        const errorMessage =
          error instanceof Error && error.message === "font_embedding_not_applied"
            ? "글꼴 포함 검증에 실패해 다운로드를 시작하지 않았습니다."
            : error instanceof Error &&
                error.message &&
                error.message !== "hybrid_export_failed"
              ? error.message
              : "파일을 만들지 못했습니다. 잠시 후 다시 시도해 주세요.";
        notify.error("PPTX 내보내기 실패", errorMessage, {
          id: notificationId,
        });
      } finally {
        setDirectExportInFlight(false);
      }
      return;
    }
    try {
      const option = pptxMode === "hybrid" ? "편집 가능한 요소를 분리해 " : "";
      const requestedFontEmbedding = pptxMode === "hybrid" && fontEmbedding !== false;
      const fontOption = requestedFontEmbedding ? "(글꼴 포함 요청) " : "";
      await onWorkspaceCommand(`${option}${fontOption}PPTX로 내보내기`, {
        commandType: "export.pptx",
        context: {
          format: "pptx",
          ...(pptxMode ? { pptx_mode: pptxMode } : {}),
          ...(pptxMode ? { font_embedding: requestedFontEmbedding } : {}),
          ...masterPinCommandContext(appliedMasterPin),
        },
      });
    } catch {
      // The durable timeline owns request errors and explicit retry UI.
    }
  };

  const handleExportPdf = async () => {
    if (isStreaming || !workspaceCommandsEnabled || !onWorkspaceCommand) return;
    try {
      await onWorkspaceCommand("PDF로 내보내기", {
        commandType: "export.pdf",
        context: {
          format: "pdf",
          ...masterPinCommandContext(appliedMasterPin),
        },
      });
    } catch {
      // The durable timeline owns request errors and explicit retry UI.
    }
  };

  const openHybridExportHistory = async () => {
    if (!isAuthoredDeck || isStreaming) return;
    setHybridExportHistory(null);
    setHybridExportHistoryError(null);
    setHybridExportHistoryLoading(true);
    try {
      setHybridExportHistory(await getHybridExportHistory(presentation_id));
    } catch {
      setHybridExportHistoryError("Hybrid 내보내기 이력을 불러오지 못했습니다. 기존 내보내기 파일은 변경하지 않았습니다.");
    } finally {
      setHybridExportHistoryLoading(false);
    }
  };

  const handleQualityReview = () => {
    if (
      !workspaceCommandsEnabled ||
      !onWorkspaceQualityReviewRequest ||
      isStreaming
    )
      return;
    onWorkspaceQualityReviewRequest(currentSlide);
  };
  const handleReGenerate = () => {
    if (!mutationsEnabled) return;
    setIsRegenerateConfirmOpen(false);
    dispatch(clearPresentationData());
    dispatch(clearHistory());
    trackEvent(MixpanelEvent.Presentation_Regenerated, {
      pathname,
      presentation_id,
      slide_count: presentationData?.slides?.length || 0,
    });
    // regenerate=true forces the stream to re-run generation; without it the
    // stream now replays existing slides idempotently (so a refresh can't wipe edits).
    router.push(
      `/presentation?id=${presentation_id}&stream=true&regenerate=true`,
    );
  };

  const handleStartPresentation = () => {
    const to = `?id=${presentation_id}&mode=present&slide=${currentSlide || 0}`;
    trackEvent(MixpanelEvent.Presentation_Mode_Entered, {
      pathname,
      presentation_id,
      slide_index: currentSlide || 0,
      slide_count: presentationData?.slides?.length || 0,
    });
    trackEvent(MixpanelEvent.Navigation, { from: pathname, to });
    router.push(to);
  };

  const exportDisabled =
    !resolvedHeaderFeatures.exportActions ||
    isStreaming === true ||
    directExportInFlight ||
    !workspaceCommandsEnabled ||
    !onWorkspaceCommand;

  const titleBlock = (
    <div
      className={cn(
        "min-w-0 flex-1 transition-[box-shadow] duration-200",
        isEditingTitle && "relative z-[60]",
      )}
    >
      <PresentationTitleEditor
        isEditingTitle={isEditingTitle}
        draftTitle={draftTitle}
        setDraftTitle={setDraftTitle}
        titleInputRef={titleInputRef}
        titleBlurIntentRef={titleBlurIntentRef}
        handleTitleBlur={handleTitleBlur}
        commitTitleEdit={commitTitleEdit}
        cancelTitleEdit={cancelTitleEdit}
        beginTitleEdit={beginTitleEdit}
        onTitleSaveMouseDown={onTitleSaveMouseDown}
        onTitleCancelMouseDown={onTitleCancelMouseDown}
        mutationsEnabled={mutationsEnabled}
        isStreaming={isStreaming}
        presentationData={presentationData}
      />
    </div>
  );

  return (
    <>
      <div className="sticky top-0 z-50 flex w-full min-w-0 items-center justify-between gap-2 bg-white px-3 py-[18px] font-syne shadow-sm sm:gap-4 sm:px-4">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <DashboardNavigationButton onNavigate={() => router.push("/dashboard")} />
          {mutationsEnabled &&
          presentationData &&
          !isStreaming &&
          !isEditingTitle ? (
            <ToolTip content="발표자료 이름 변경">{titleBlock}</ToolTip>
          ) : (
            titleBlock
          )}
          {presentationData && isAuthoredDeck && (
            <MasterLayoutBadge
              presentationId={presentation_id}
              presentation={presentationData}
              currentSlide={currentSlide}
            />
          )}
        </div>

        <div className="flex max-w-[58vw] shrink-0 items-center gap-2.5 overflow-x-auto pb-1 pr-1 sm:max-w-none">
          {isPresentationSaving && (
            <div className="flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            </div>
          )}
          {isPresentationSaveFailed && !isPresentationSaving && (
            <div
              className="flex items-center gap-1.5 text-red-600"
              role="status"
            >
              <AlertTriangle className="w-3.5 h-3.5" />
              <span className="text-xs font-medium">저장 안 됨</span>
            </div>
          )}

          {presentationData && resolvedHeaderFeatures.showAuthoredActions && (
            <AuthoredHeaderActions
              presentation_id={presentation_id}
              theme={presentationData.theme}
              currentMasterPin={appliedMasterPin}
              currentSlide={currentSlide}
              isStreaming={isStreaming}
              isPresentationSaving={isPresentationSaving}
              workspaceCommandsEnabled={workspaceCommandsEnabled}
              onWorkspaceCommand={onWorkspaceCommand}
              onWorkspaceQualityReviewRequest={onWorkspaceQualityReviewRequest}
              onWorkspacePageRemakeRequest={onWorkspacePageRemakeRequest}
              onCanonicalSaved={onCanonicalSaved}
              handleQualityReview={handleQualityReview}
            />
          )}

          <HeaderToolbar
            presentation_id={presentation_id}
            presentationData={presentationData}
            isStreaming={isStreaming}
            mutationsEnabled={mutationsEnabled}
            historyEnabled={historyEnabled}
            canUndo={canUndo}
            canRedo={canRedo}
            onUndo={onUndo}
            onRedo={onRedo}
            setIsRegenerateConfirmOpen={setIsRegenerateConfirmOpen}
            handleStartPresentation={handleStartPresentation}
            workspaceCommandsEnabled={workspaceCommandsEnabled}
            onWorkspaceCommand={onWorkspaceCommand}
          />

          {presentationData && (
            <GenerationInfoDialog
              presentationId={presentation_id}
              disabled={isStreaming === true}
            />
          )}

          {resolvedHeaderFeatures.exportActions && (
            <ExportMenu
              isAuthoredDeck={isAuthoredDeck}
              hasMasterPin={appliedMasterPin !== null}
              exportDisabled={exportDisabled}
              workspaceCommandsEnabled={workspaceCommandsEnabled}
              onExportPptx={handleExportPptx}
              onExportPdf={handleExportPdf}
              onOpenHybridExportHistory={openHybridExportHistory}
              historyDisabled={isStreaming === true}
              hybridDisabledReason={hybridDisabledReason}
              manifestBindingLabel={
                authoredCapabilityStatus.state === "ready" && authoredCapabilityStatus.readiness.version === "v1-fallback"
                  ? `동일 manifest · revision ${authoredCapabilityStatus.readiness.projection.presentationRevision} · ${authoredCapabilityStatus.readiness.projection.manifestDigest ?? "미기록"}`
                  : authoredCapabilityStatus.state === "ready" && authoredCapabilityStatus.readiness.version === "v2"
                    ? `v2 release profile · revision ${authoredCapabilityStatus.readiness.projection.presentationRevision} · ${authoredCapabilityStatus.readiness.projection.releaseProfile.profileId}`
                    : undefined
              }
            />
          )}
        </div>
      </div>
      {isAuthoredDeck ? (
        <div className="border-t border-slate-100 px-4 py-2 sm:px-6">
          {editableHybridWorkbench && (
            <div className="mb-2 flex flex-wrap items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-950" data-testid="editable-hybrid-workbench-bridge">
              <span className="font-semibold">편집 가능한 Hybrid MVP</span>
              <span>{editableHybridWorkbench.status === "ready" ? "준비됨" : editableHybridWorkbench.status === "shadow" ? "Shadow" : "차단됨"}</span>
              {editableHybridWorkbench.status === "blocked" ? <span>{editableHybridWorkbench.reasonCode ?? "editable_hybrid_mvp_blocked"}</span> : <a className="font-semibold underline underline-offset-2" data-testid="editable-hybrid-workbench-link" href={`/authored-vnext/workbench?authoredDeckId=${encodeURIComponent(editableHybridWorkbench.deckId)}`}>Workbench에서 편집</a>}
            </div>
          )}
          <AuthoredEmissionCapabilityStatusView
            status={authoredCapabilityStatus}
            expectedRevision={expectedRevision}
          />
        </div>
      ) : null}
      <ExportQualityDialog
        exportQuality={exportQuality}
        setExportQuality={setExportQuality}
      />
      <HybridCoverageDialog
        manifest={hybridCoverage}
        exportQuality={exportQuality}
        onClose={() => setHybridCoverage(null)}
      />
      <HybridExportHistoryDialog
        history={hybridExportHistory}
        loading={hybridExportHistoryLoading}
        error={hybridExportHistoryError}
        onClose={() => {
          setHybridExportHistory(null);
          setHybridExportHistoryError(null);
          setHybridExportHistoryLoading(false);
        }}
      />
      <RegenerateConfirmDialog
        open={mutationsEnabled && isRegenerateConfirmOpen}
        setIsRegenerateConfirmOpen={setIsRegenerateConfirmOpen}
        handleReGenerate={handleReGenerate}
      />
      <KeyboardShortcutsDialog
        open={shortcutsDialogOpen}
        onOpenChange={setShortcutsDialogOpen}
      />
    </>
  );
};

export default PresentationHeader;
