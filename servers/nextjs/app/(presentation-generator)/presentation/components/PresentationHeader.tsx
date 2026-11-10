"use client";
import { Loader2, AlertTriangle } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useDispatch, useSelector } from "react-redux";

import { RootState } from "@/store/store";
import { usePresentationUndoRedo } from "../hooks/PresentationUndoRedo";
import ToolTip from "@/components/ToolTip";
import {
  clearPresentationData,
  updateTitle,
} from "@/store/slices/presentationGeneration";
import { clearHistory } from "@/store/slices/undoRedoSlice";
import NewSlideButton from "./header/NewSlideButton";
import { cn } from "@/lib/utils";
import { KeyboardShortcutsDialog } from "./KeyboardShortcutsDialog";
import CommonHeaderDesignChanger, { existingHeaderDesignReferences, existingHeaderDocumentValues, fullPageHeaderLocked } from "./header/CommonHeaderDesignChanger";
import ExportMenu, { sourcePreservingSvgPdfUnavailable } from "./header/ExportMenu";
import HeaderToolbar from "./header/HeaderToolbar";
import HeaderMoreMenu from "./header/HeaderMoreMenu";
import { isSvgPresentation } from "../utils/presentationSource";
import DashboardNavigationButton from "./header/DashboardNavigationButton";
import PresentationTitleEditor from "./header/PresentationTitleEditor";
import RegenerateConfirmDialog from "./header/RegenerateConfirmDialog";
import ExportReviewConfirmDialog from "./header/ExportReviewConfirmDialog";
import { flaggedSlidePositions, hasUnreviewedDeckFlow } from "../utils/flaggedSlides";
import type { PresentationHeaderFeatureSlots } from "./presentationHeaderTypes";
export type { PresentationHeaderFeatureSlots } from "./presentationHeaderTypes";
import type {
  ExportOperationType,
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../workspace";


const PresentationHeader = ({
  presentation_id,
  isPresentationSaving,
  isPresentationSaveFailed = false,
  currentSlide,
  mutationsEnabled = true,
  workspaceCommandsEnabled = false,
  onWorkspaceCommand,
  onWorkspaceQualityReviewRequest,
  onWorkspacePageRemakeRequest,
  activeExportOperation = null,
  onCanonicalSaved,
  headerFeatures,
}: {
  presentation_id: string;
  isPresentationSaving: boolean;
  isPresentationSaveFailed?: boolean;
  currentSlide?: number;
  mutationsEnabled?: boolean;
  workspaceCommandsEnabled?: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onWorkspaceQualityReviewRequest?: WorkspaceQualityReviewRequester;
  onWorkspacePageRemakeRequest?: WorkspacePageRemakeRequester;
  activeExportOperation?: ExportOperationType | null;
  onCanonicalSaved?: () => void | Promise<void>;
  headerFeatures?: PresentationHeaderFeatureSlots;
}) => {
  const [shortcutsDialogOpen, setShortcutsDialogOpen] = useState(false);
  const router = useRouter();
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [isRegenerateConfirmOpen, setIsRegenerateConfirmOpen] = useState(false);
  const [draftTitle, setDraftTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  /** Avoid committing on blur when Save/Cancel was used (focus/click ordering) */
  const titleBlurIntentRef = useRef<"none" | "save" | "cancel">("none");

  const dispatch = useDispatch();

  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration,
  );
  const expectedRevision =
    typeof presentationData?.revision === "number"
      ? presentationData.revision
      : undefined;

  // Compatibility facade for isolated header consumers. The presentation page
  // always supplies the server-derived projection; this fallback is removable
  // once those stories/tests consume PresentationShell as well.
  const resolvedHeaderFeatures: PresentationHeaderFeatureSlots =
    headerFeatures ?? { exportActions: true };

  const workspaceHistoryEnabled =
    workspaceCommandsEnabled && Boolean(onWorkspaceCommand);
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

  // Exports of a deck with pages still flagged for review ask first.
  const reviewRequiredCount = flaggedSlidePositions(presentationData).length;
  const [pendingExport, setPendingExport] = useState<(() => Promise<void>) | null>(null);
  const requestExport = async (run: () => Promise<void>) => {
    if (reviewRequiredCount > 0) {
      setPendingExport(() => run);
      return;
    }
    await run();
  };

  const runExportPptx = async () => {
    if (
      isStreaming ||
      !workspaceCommandsEnabled ||
      !onWorkspaceCommand
    )
      return;
    try {
      await onWorkspaceCommand("PPTX로 내보내기", {
        commandType: "export.pptx",
        context: { format: "pptx" },
      });
    } catch {
      // The durable timeline owns request errors and explicit retry UI.
    }
  };

  const handleExportPptx = () => requestExport(runExportPptx);

  const handleExportPdf = () => requestExport(runExportPdf);

  const runExportPdf = async () => {
    if (isStreaming || !workspaceCommandsEnabled || !onWorkspaceCommand || sourcePreservingSvgPdfUnavailable(presentationData)) return;
    try {
      await onWorkspaceCommand("PDF로 내보내기", {
        commandType: "export.pdf",
        context: { format: "pdf" },
      });
    } catch {
      // The durable timeline owns request errors and explicit retry UI.
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
    // regenerate=true forces the stream to re-run generation; without it the
    // stream now replays existing slides idempotently (so a refresh can't wipe edits).
    router.push(
      `/presentation?id=${presentation_id}&stream=true&regenerate=true`,
    );
  };

  const handleStartPresentation = () => {
    const to = `?id=${presentation_id}&mode=present&slide=${currentSlide || 0}`;
    router.push(to);
  };

  const exportOperationInFlight = activeExportOperation !== null;
  const exportDisabled =
    !resolvedHeaderFeatures.exportActions ||
    isStreaming === true ||
    !workspaceCommandsEnabled ||
    !onWorkspaceCommand ||
    exportOperationInFlight;

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
      <div className="devax-editor-header sticky top-0 z-50 flex min-h-14 w-full min-w-0 items-center justify-between gap-2 border-b border-[#EBEBEC] bg-white px-3 py-2 font-syne shadow-none sm:gap-4 sm:px-4">
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

          {presentationData && isSvgPresentation(presentationData) ? (
            <CommonHeaderDesignChanger
              key={presentation_id}
              presentationId={presentation_id}
              expectedRevision={expectedRevision}
              disabled={!workspaceCommandsEnabled || isStreaming === true || isPresentationSaving || fullPageHeaderLocked(presentationData)}
              unavailableReason={fullPageHeaderLocked(presentationData) ? "원본 화면 전체를 보존하는 페이지에는 상단을 덧씌울 수 없습니다." : undefined}
              referenceSnapshotIds={existingHeaderDesignReferences(presentationData)}
              documentValues={existingHeaderDocumentValues(presentationData)}
              onSaved={onCanonicalSaved}
            />
          ) : null}

          <HeaderToolbar
            presentationData={presentationData}
            isStreaming={isStreaming}
            historyEnabled={workspaceHistoryEnabled}
            canUndo={canUndo}
            canRedo={canRedo}
            onUndo={onUndo}
            onRedo={onRedo}
            handleStartPresentation={handleStartPresentation}
          />

          {presentationData && (
            <NewSlideButton disabled={isStreaming === true} />
          )}

          {resolvedHeaderFeatures.exportActions && (
            <ExportMenu
              exportDisabled={exportDisabled}
              workspaceCommandsEnabled={workspaceCommandsEnabled}
              onExportPptx={handleExportPptx}
              onExportPdf={handleExportPdf}
              exportOperationInFlight={exportOperationInFlight}
              presentation={presentationData}
            />
          )}

          {presentationData && (
            <HeaderMoreMenu
              presentationId={presentation_id}
              canRemakePage={presentationData?.capabilities?.editor?.slide_image_regenerate === true}
              isSvgDeck={isSvgPresentation(presentationData)}
              currentSlide={currentSlide}
              isStreaming={isStreaming}
              isPresentationSaving={isPresentationSaving}
              mutationsEnabled={mutationsEnabled}
              historyEnabled={workspaceHistoryEnabled}
              workspaceCommandsEnabled={workspaceCommandsEnabled}
              onWorkspaceCommand={onWorkspaceCommand}
              onWorkspaceQualityReviewRequest={onWorkspaceQualityReviewRequest}
              onWorkspacePageRemakeRequest={onWorkspacePageRemakeRequest}
              onQualityReview={handleQualityReview}
              onRegenerate={() => setIsRegenerateConfirmOpen(true)}
            />
          )}
        </div>
      </div>
      <ExportReviewConfirmDialog
        open={pendingExport !== null}
        count={reviewRequiredCount}
        flowUnreviewed={hasUnreviewedDeckFlow(presentationData?.theme)}
        onCancel={() => setPendingExport(null)}
        onConfirm={() => {
          const run = pendingExport;
          setPendingExport(null);
          if (run) void run();
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
