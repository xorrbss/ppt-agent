"use client";

import AuthoredTemplateChanger from "../AuthoredTemplateChanger";
import AuthoredElementEditor from "../AuthoredElementEditor";
import type { PresentationMasterPin } from "../../utils/masterPresentation";
import type { WorkspaceCommandDispatcher } from "../../workspace";

function getAuthoredStyleId(theme: unknown): string | null {
  if (!theme || typeof theme !== "object") return null;
  const style = (theme as Record<string, unknown>).style;
  return typeof style === "string" ? style : null;
}

const AuthoredHeaderActions = ({
  presentation_id,
  theme,
  currentMasterPin,
  isStreaming,
  isPresentationSaving,
  workspaceCommandsEnabled,
  onWorkspaceCommand,
  onCanonicalSaved,
}: {
  presentation_id: string;
  theme: unknown;
  currentMasterPin?: PresentationMasterPin | null;
  isStreaming: boolean | null;
  isPresentationSaving: boolean;
  workspaceCommandsEnabled: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onCanonicalSaved?: () => void | Promise<void>;
}) => (
  <>
    <AuthoredElementEditor
      presentationId={presentation_id}
      disabled={isStreaming === true || isPresentationSaving}
      onSaved={onCanonicalSaved}
    />
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
  </>
);

export default AuthoredHeaderActions;
