"use client";
import { Play, Redo2, RotateCcw, Undo2 } from "lucide-react";

import ToolTip from "@/components/ToolTip";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import type { PresentationData } from "@/store/slices/presentationGeneration";

import VersionHistoryPopover from "../VersionHistoryPopover";
import type { WorkspaceCommandDispatcher } from "../../workspace";

const HeaderToolbar = ({
  presentation_id,
  presentationData,
  isStreaming,
  mutationsEnabled,
  historyEnabled,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  setIsRegenerateConfirmOpen,
  handleStartPresentation,
  workspaceCommandsEnabled,
  onWorkspaceCommand,
}: {
  presentation_id: string;
  presentationData: PresentationData | null;
  isStreaming: boolean | null;
  mutationsEnabled: boolean;
  historyEnabled: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  setIsRegenerateConfirmOpen: (open: boolean) => void;
  handleStartPresentation: () => void;
  workspaceCommandsEnabled: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
}) => (
  <div className="flex items-center gap-2 bg-[#F6F6F9] px-3.5 h-[38px] border border-[#EDECEC] rounded-[80px]">
    <ToolTip content="발표자료 재생성">
      <button
        type="button"
        onClick={() => setIsRegenerateConfirmOpen(true)}
        data-testid="presentation-regenerate"
        aria-label="발표자료 재생성"
        className={cn("group", !mutationsEnabled && "hidden")}
      >
        <RotateCcw className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
      </button>
    </ToolTip>
    <Separator orientation="vertical" className="h-4" />
    <ToolTip content="실행 취소">
      <button
        type="button"
        disabled={!canUndo}
        data-testid="presentation-undo"
        aria-label="실행 취소"
        className={cn(
          "disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer group",
          !historyEnabled && "hidden",
        )}
        onClick={() => {
          onUndo();
        }}
      >
        <Undo2 className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
      </button>
    </ToolTip>
    <Separator orientation="vertical" className="h-4" />
    <ToolTip content="다시 실행">
      <button
        type="button"
        disabled={!canRedo}
        data-testid="presentation-redo"
        aria-label="다시 실행"
        className={cn(
          "disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer group",
          !historyEnabled && "hidden",
        )}
        onClick={() => {
          onRedo();
        }}
      >
        <Redo2 className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
      </button>
    </ToolTip>
    <Separator orientation="vertical" className="h-4 w-[2px]" />
    <ToolTip content="발표 시작">
      <button
        type="button"
        onClick={handleStartPresentation}
        disabled={
          isStreaming ||
          !presentationData?.slides ||
          presentationData?.slides.length === 0
        }
        aria-label="발표 시작"
        className="cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed group"
      >
        <Play className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
      </button>
    </ToolTip>
    <Separator orientation="vertical" className="h-4" />
    <VersionHistoryPopover
      presentationId={presentation_id}
      disabled={isStreaming === true}
      allowRestore={historyEnabled}
      workspaceCommandsEnabled={workspaceCommandsEnabled}
      onWorkspaceCommand={onWorkspaceCommand}
    />
  </div>
);

export default HeaderToolbar;
