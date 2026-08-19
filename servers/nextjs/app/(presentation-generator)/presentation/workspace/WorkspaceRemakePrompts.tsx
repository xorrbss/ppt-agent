"use client";

import { WorkspacePageRemakePrompt } from "../components/AuthoredPageRemake";
import { WorkspaceBatchRemakeDialog } from "./WorkspaceBatchRemakeDialog";
import type { WorkspaceCommandDispatcher } from "./types";

/**
 * `batch` records how the request arrived rather than being inferred from the
 * length: a one-page selection ticked in the quality card is still a batch and
 * must go through the preview-then-approve dialog.
 */
export interface WorkspaceRemakeRequest {
  slideIndexes: number[];
  batch: boolean;
}

export function WorkspaceRemakePrompts({
  request,
  presentationId,
  disabled,
  onWorkspaceCommand,
  onDismiss,
}: {
  request: WorkspaceRemakeRequest | null;
  presentationId: string;
  disabled?: boolean;
  onWorkspaceCommand: WorkspaceCommandDispatcher;
  onDismiss: () => void;
}) {
  if (!request || request.slideIndexes.length === 0) return null;

  if (request.batch) {
    return (
      <WorkspaceBatchRemakeDialog
        presentationId={presentationId}
        slideIndexes={request.slideIndexes}
        disabled={disabled}
        onWorkspaceCommand={onWorkspaceCommand}
        onDismiss={onDismiss}
      />
    );
  }

  return (
    <WorkspacePageRemakePrompt
      presentationId={presentationId}
      currentSlide={request.slideIndexes[0]}
      disabled={disabled}
      onWorkspaceCommand={onWorkspaceCommand}
      onDismiss={onDismiss}
    />
  );
}
