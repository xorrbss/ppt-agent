"use client";
import {
  Loader2,
  AlertTriangle,
} from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import Image from "next/image";
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
import { KeyboardShortcutsDialog } from "./KeyboardShortcutsDialog";
import AuthoredHeaderActions from "./header/AuthoredHeaderActions";
import ExportMenu from "./header/ExportMenu";
import ExportQualityDialog from "./header/ExportQualityDialog";
import HeaderToolbar from "./header/HeaderToolbar";
import PresentationTitleEditor from "./header/PresentationTitleEditor";
import RegenerateConfirmDialog from "./header/RegenerateConfirmDialog";
import type { PptxExportMode } from "./header/types";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../workspace";

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
}) => {
  const [shortcutsDialogOpen, setShortcutsDialogOpen] = useState(false);
  const router = useRouter();
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [isRegenerateConfirmOpen, setIsRegenerateConfirmOpen] = useState(false);
  const [exportQuality, setExportQuality] =
    useState<PresentationExportQualityReport | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  /** Avoid committing on blur when Save/Cancel was used (focus/click ordering) */
  const titleBlurIntentRef = useRef<"none" | "save" | "cancel">("none");

  const pathname = usePathname();
  const dispatch = useDispatch();

  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );

  const workspaceHistoryEnabled =
    workspaceCommandsEnabled && Boolean(onWorkspaceCommand);
  const historyEnabled = workspaceHistoryEnabled;
  const { onUndo, onRedo, canUndo, canRedo } = usePresentationUndoRedo(
    false,
    workspaceHistoryEnabled ? onWorkspaceCommand : undefined
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
    fontEmbedding = false
  ) => {
    if (isStreaming || !workspaceCommandsEnabled || !onWorkspaceCommand) return;
    const option = pptxMode === "hybrid" ? "편집 가능한 요소를 분리해 " : "";
    const fontOption = fontEmbedding ? "(허용된 글꼴 포함) " : "";
    try {
      await onWorkspaceCommand(`${option}${fontOption}PPTX로 내보내기`, {
        commandType: "export.pptx",
        context: {
          format: "pptx",
          ...(pptxMode ? { pptx_mode: pptxMode } : {}),
          ...(pptxMode ? { font_embedding: fontEmbedding } : {}),
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
    ) return;
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
    router.push(`/presentation?id=${presentation_id}&stream=true&regenerate=true`);
  };

  const handleStartPresentation = () => {
    const to = `?id=${presentation_id}&mode=present&slide=${
      currentSlide || 0
    }`;
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
    isStreaming === true || !workspaceCommandsEnabled || !onWorkspaceCommand;

  const titleBlock = (
    <div
      className={cn(
        "min-w-0 flex-1 transition-[box-shadow] duration-200",
        isEditingTitle && "relative z-[60]"
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
      <div className="sticky top-0 z-50 flex w-full min-w-0 items-center justify-between gap-4 bg-white px-4 py-[18px] font-syne shadow-sm">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Image
            onClick={() => {
              router.push("/dashboard");
            }}
            src="/logo-with-bg.png"
            alt=""
            width={40}
            height={40}
            className="h-10 w-10 shrink-0 cursor-pointer object-contain"
          />
          {mutationsEnabled && presentationData && !isStreaming && !isEditingTitle ? (
            <ToolTip content="발표자료 이름 변경">{titleBlock}</ToolTip>
          ) : (
            titleBlock
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2.5">
          {isPresentationSaving && (
            <div className="flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            </div>
          )}
          {isPresentationSaveFailed && !isPresentationSaving && (
            <div className="flex items-center gap-1.5 text-red-600" role="status">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span className="text-xs font-medium">저장 안 됨</span>
            </div>
          )}

          {presentationData && isAuthoredDeck && (
            <AuthoredHeaderActions
              presentation_id={presentation_id}
              theme={presentationData.theme}
              currentSlide={currentSlide}
              isStreaming={isStreaming}
              isPresentationSaving={isPresentationSaving}
              workspaceCommandsEnabled={workspaceCommandsEnabled}
              onWorkspaceCommand={onWorkspaceCommand}
              onWorkspaceQualityReviewRequest={onWorkspaceQualityReviewRequest}
              onWorkspacePageRemakeRequest={onWorkspacePageRemakeRequest}
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

          <ExportMenu
            isAuthoredDeck={isAuthoredDeck}
            exportDisabled={exportDisabled}
            workspaceCommandsEnabled={workspaceCommandsEnabled}
            onExportPptx={handleExportPptx}
            onExportPdf={handleExportPdf}
          />
        </div>
      </div>
      <ExportQualityDialog
        exportQuality={exportQuality}
        setExportQuality={setExportQuality}
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
