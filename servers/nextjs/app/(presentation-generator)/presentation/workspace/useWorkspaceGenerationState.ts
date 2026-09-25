import { useEffect, useMemo } from "react";
import type { WorkspaceTimelineAnchor } from "./types";
import { TERMINAL } from "./WorkspaceOperationLabels";

export interface WorkspaceGenerationState {
  loading: boolean;
  operationId: string | null;
  status: string | null;
  resultRevision?: number;
}

export function useWorkspaceGenerationState({
  items,
  loading,
  onChange,
}: {
  items: WorkspaceTimelineAnchor[];
  loading: boolean;
  onChange?: (state: WorkspaceGenerationState) => void;
}) {
  const latestGeneration = useMemo(
    () =>
      items
        .filter(
          (item) =>
            item.anchorType === "operation" &&
            item.operationType === "presentation.generate",
        )
        .sort((left, right) => right.position - left.position)[0],
    [items],
  );
  const state: WorkspaceGenerationState = {
    loading,
    operationId:
      latestGeneration?.anchorType === "operation"
        ? latestGeneration.operationId
        : null,
    status:
      latestGeneration?.anchorType === "operation"
        ? latestGeneration.status
        : null,
    resultRevision:
      latestGeneration?.anchorType === "operation"
        ? latestGeneration.resultRevision
        : undefined,
  };
  useEffect(() => {
    onChange?.(state);
  }, [onChange, state.loading, state.operationId, state.resultRevision, state.status]);

  const mutationActive = useMemo(
    () =>
      items.some(
        (item) =>
          item.anchorType === "operation" &&
          !TERMINAL.has(item.status) &&
          item.operationType !== "export.pptx" &&
          item.operationType !== "export.pdf",
      ),
    [items],
  );
  return { state, mutationActive };
}
