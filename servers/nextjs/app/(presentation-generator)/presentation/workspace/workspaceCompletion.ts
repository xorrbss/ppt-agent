import type {
  WorkspaceOperationAnchor,
  WorkspacePresentationChange,
} from "./types";

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

/** Returns unique, zero-based editor slide indexes from the durable result. */
export function fixedWorkspaceSlideIndexes(
  result?: Record<string, unknown>,
): number[] {
  const zeroBasedValues = Array.isArray(result?.fixed_slide_indices)
    ? result.fixed_slide_indices
    : Array.isArray(result?.fixedSlideIndices)
      ? result.fixedSlideIndices
      : [];
  const values =
    zeroBasedValues.length > 0
      ? zeroBasedValues
      : Array.isArray(result?.fixed_pages)
        ? result.fixed_pages.map((page) =>
            typeof page === "number" ? page - 1 : page,
          )
        : Array.isArray(result?.modified_pages)
          ? result.modified_pages.map((page) =>
              typeof page === "number" ? page - 1 : page,
            )
          : [];
  return [
    ...new Set(
      values.filter(
        (value): value is number =>
          typeof value === "number" && Number.isInteger(value) && value >= 0,
      ),
    ),
  ].sort((left, right) => left - right);
}

/**
 * Only a durably committed quality revision may refresh the editor canvas.
 * This keeps an optimistic terminal event from being presented as completion.
 */
export function workspacePresentationChangeFor(
  operation: WorkspaceOperationAnchor,
): WorkspacePresentationChange | undefined {
  if (
    operation.status !== "succeeded" ||
    operation.operationType === "export.pptx" ||
    operation.operationType === "export.pdf"
  ) {
    return undefined;
  }

  if (operation.operationType === "quality.review") {
    const fixedCount = numberValue(
      operation.result?.fixed_count ?? operation.result?.fixedCount,
    );
    const resultRevision =
      operation.resultRevision ??
      numberValue(
        operation.result?.result_revision ?? operation.result?.resultRevision,
      );
    if (
      !operation.result ||
      resultRevision === undefined ||
      fixedCount === undefined ||
      fixedCount <= 0
    ) {
      return undefined;
    }
    return {
      operationId: operation.operationId,
      operationType: operation.operationType,
      result: operation.result,
      resultRevision,
      fixedSlideIndexes: fixedWorkspaceSlideIndexes(operation.result),
    };
  }

  return {
    operationId: operation.operationId,
    operationType: operation.operationType,
    result: operation.result,
    resultRevision: operation.resultRevision,
  };
}
