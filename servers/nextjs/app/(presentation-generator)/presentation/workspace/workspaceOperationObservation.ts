import type { WorkspaceOperationAnchor, WorkspaceProjection } from "./types";
import { operationProgressEvidence } from "./WorkspaceOperationPresentation";

export interface WorkspaceOperationObservation {
  lastConfirmedAt?: string;
  lastProgressAt?: string;
  fingerprint: string;
}

function progressEvidence(operation: WorkspaceOperationAnchor) {
  const evidence = operationProgressEvidence(operation);
  return {
    // Percent may be advanced by a provider heartbeat without completed work.
    fingerprint: JSON.stringify([operation.status, operation.stage,
      operation.current, operation.total, evidence.messages.at(-1)]),
    at: evidence.lastProgressAt,
  };
}

/** Only authoritative snapshots or accepted operation events confirm a job. */
export function observeWorkspaceOperations(
  current: Record<string, WorkspaceOperationObservation>,
  projection: WorkspaceProjection,
  confirmed: "snapshot" | string[],
  now: string,
) {
  const observed = { ...current };
  const ids = confirmed === "snapshot" ? null : new Set(confirmed);
  for (const item of projection.items) {
    if (item.anchorType !== "operation" || (ids && !ids.has(item.operationId))) continue;
    const evidence = progressEvidence(item);
    const previous = current[item.operationId];
    observed[item.operationId] = {
      lastConfirmedAt: now,
      lastProgressAt: previous?.fingerprint === evidence.fingerprint
        ? previous.lastProgressAt
        : previous
          ? evidence.at && Date.parse(evidence.at) > Date.parse(previous.lastProgressAt ?? "")
            ? evidence.at : now
          : evidence.at,
      fingerprint: evidence.fingerprint,
    };
  }
  return observed;
}
