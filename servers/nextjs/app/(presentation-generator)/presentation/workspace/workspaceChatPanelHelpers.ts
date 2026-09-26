"use client";
import { useEffect, type Dispatch, type SetStateAction } from "react";
import { deriveFlaggedSlideMap, flaggedSlideKey } from "../utils/flaggedSlides";
import { DashboardApi, type PresentationGenerationInfo } from "../../services/api/dashboard";
import type { WorkspaceAgentSlideFocus } from "./useWorkspaceAgentFocus";
import type { WorkspaceCommandDispatcher, WorkspacePageRemakeRequester, WorkspacePresentationChangedHandler, WorkspaceQualityReviewRequester } from "./types";
import type { ExportOperationType } from "./workspaceChatPanelUtils";
import { TERMINAL } from "./WorkspaceOperationLabels";
import type { WorkspaceProjection, WorkspaceOperationAnchor } from "./types";
import type { useWorkspaceTimeline, WorkspaceRetryReason } from "./useWorkspace";

export interface WorkspaceChatPanelProps {
  presentationId: string;
  presentation?: Parameters<typeof deriveFlaggedSlideMap>[0];
  currentSlide?: number; currentSlideId?: string; generationPending?: boolean;
  commandsEnabled?: boolean;
  onPresentationChanged?: WorkspacePresentationChangedHandler;
  onSlideFocus?: (slideIndex: number) => void;
  /** True while the runner still owns the deck, for canvas activity affordances. */
  onAgentActivityChange?: (active: boolean) => void;
  /** Emitted only while "AI 따라가기" is on, so the canvas may move itself. */
  onAgentSlideFocus?: (focus: WorkspaceAgentSlideFocus) => void;
  onCommandDispatcherReady?: (
    dispatcher: WorkspaceCommandDispatcher | null,
  ) => void;
  onQualityReviewRequesterReady?: (
    requester: WorkspaceQualityReviewRequester | null,
  ) => void;
  onPageRemakeRequesterReady?: (
    requester: WorkspacePageRemakeRequester | null,
  ) => void;
  onExportOperationStateChange?: (operationType: ExportOperationType | null) => void;
  onGenerationStateChange?: (state: { loading: boolean; operationId: string | null; status: string | null; resultRevision?: number }) => void;
  onEditCurrentSlideReady?: (handler: (() => void) | null) => void;
}

export function reviewRequiredIndicesFor(presentation: WorkspaceChatPanelProps["presentation"]): number[] {
  const flags = deriveFlaggedSlideMap(presentation);
  const slides = Array.isArray(presentation?.slides) ? presentation.slides : [];
  return slides.flatMap((slide, position) =>
    flags[flaggedSlideKey(slide, position)] ? [position] : [],
  );
}

export interface GenerationInfoResult {
  presentationId: string;
  info: PresentationGenerationInfo | null;
}
export function useWorkspaceGenerationInfoResult(
  presentationId: string,
  generationInfoResult: GenerationInfoResult | null,
  setGenerationInfoResult: Dispatch<SetStateAction<GenerationInfoResult | null>>,
) {
  useEffect(() => {
    let active = true;

    void DashboardApi.getPresentationGenerationInfo(presentationId)
      .then((info) => {
        if (active) setGenerationInfoResult({ presentationId, info });
      })
      .catch(() => {
        if (active) setGenerationInfoResult({ presentationId, info: null });
      });

    return () => {
      active = false;
    };
  }, [presentationId]);

  const generationInfo =
    generationInfoResult?.presentationId === presentationId
      ? generationInfoResult.info
      : null;
  const generationInfoLoading =
    generationInfoResult?.presentationId !== presentationId;
  return { generationInfo, generationInfoLoading };
}

export function activeWorkspaceMutationState(
  items: WorkspaceProjection["items"],
  writesEnabled: boolean,
  submitting: boolean,
  performAction: ReturnType<typeof useWorkspaceTimeline>["performAction"],
  generationPending: boolean,
  loading: boolean,
  mutationActive: boolean,
  retryReason: WorkspaceRetryReason | undefined,
) {
  const activeMutations = items.filter((item): item is WorkspaceOperationAnchor =>
    item.anchorType === "operation" && !TERMINAL.has(item.status) &&
    !item.operationType.startsWith("export."));
  const activeMutation = activeMutations.sort((left, right) => right.position - left.position)[0];
  const cancelOperation = activeMutation && writesEnabled &&
    (activeMutation.status === "cancelling" || activeMutation.allowedActions.some((action) => action.id === "cancel" && action.enabled))
    ? { operationId: activeMutation.operationId, pending: submitting || activeMutation.status === "cancelling",
        onCancel: (operationId: string) => performAction(operationId, "cancel") }
    : undefined;
  const showFollowControl = Boolean(activeMutation?.status === "running" && activeMutation.operationType !== "presentation.generate" && !generationPending);
  const restartBlockedReason = loading || submitting || mutationActive
    ? "현재 작업 상태가 확정된 뒤 다시 만들 수 있습니다."
    : retryReason === "ambiguous"
      ? "이전 요청의 접수 상태를 먼저 확인해 주세요."
      : !writesEnabled ? "이 프레젠테이션을 변경할 권한이 없습니다." : null;
  return { activeMutation, cancelOperation, showFollowControl, restartBlockedReason };
}
