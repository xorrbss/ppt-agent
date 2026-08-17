"use client";
import { ScanSearch } from "lucide-react";

import { Button } from "@/components/ui/button";

import AuthoredPageRemake from "../AuthoredPageRemake";
import AuthoredTemplateChanger from "../AuthoredTemplateChanger";
import type { PresentationMasterPin } from "../../utils/masterPresentation";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../../workspace";

function getAuthoredStyleId(theme: unknown): string | null {
  if (!theme || typeof theme !== "object") return null;
  const style = (theme as Record<string, unknown>).style;
  return typeof style === "string" ? style : null;
}

const AuthoredHeaderActions = ({
  presentation_id,
  theme,
  currentMasterPin,
  currentSlide,
  isStreaming,
  isPresentationSaving,
  workspaceCommandsEnabled,
  onWorkspaceCommand,
  onWorkspaceQualityReviewRequest,
  onWorkspacePageRemakeRequest,
  handleQualityReview,
}: {
  presentation_id: string;
  theme: unknown;
  currentMasterPin?: PresentationMasterPin | null;
  currentSlide?: number;
  isStreaming: boolean | null;
  isPresentationSaving: boolean;
  workspaceCommandsEnabled: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onWorkspaceQualityReviewRequest?: WorkspaceQualityReviewRequester;
  onWorkspacePageRemakeRequest?: WorkspacePageRemakeRequester;
  handleQualityReview: () => void;
}) => (
  <>
    <AuthoredTemplateChanger
      presentationId={presentation_id}
      currentStyleId={getAuthoredStyleId(theme)}
      currentMasterPin={currentMasterPin}
      disabled={
        !workspaceCommandsEnabled ||
        !onWorkspaceCommand ||
        isStreaming === true ||
        isPresentationSaving
      }
      onWorkspaceCommand={onWorkspaceCommand}
    />
    <AuthoredPageRemake
      currentSlide={currentSlide}
      disabled={
        !workspaceCommandsEnabled ||
        !onWorkspaceCommand ||
        isStreaming === true ||
        isPresentationSaving
      }
      workspaceCommandsEnabled={workspaceCommandsEnabled}
      onWorkspacePageRemakeRequest={onWorkspacePageRemakeRequest}
    />
    <Button
      type="button"
      variant="toolbar"
      size="toolbar"
      disabled={
        !workspaceCommandsEnabled ||
        !onWorkspaceQualityReviewRequest ||
        isStreaming === true
      }
      onClick={handleQualityReview}
      data-testid="workspace-quality-review"
      aria-label="채팅에서 고품질 검수 옵션 열기"
    >
      <ScanSearch className="h-4 w-4" aria-hidden="true" />
      고품질 검수
    </Button>
  </>
);

export default AuthoredHeaderActions;
