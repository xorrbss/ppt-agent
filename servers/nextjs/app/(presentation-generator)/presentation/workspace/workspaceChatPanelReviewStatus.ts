import { isRecord } from "@/lib/guards";
import type { WorkspaceOperationAnchor } from "./types.ts";

export type FinalPptxReviewStatus = "running" | "deferred" | "passed" | "failed";

export function finalPptxReviewStatusFor(
  items: WorkspaceOperationAnchor[],
  artifactId: string,
): FinalPptxReviewStatus | undefined {
  const matches = items.flatMap((item) => {
    if (item.operationType !== "quality.review") return [];
    const targetContext = item.target?.context;
    const targetArtifactId = typeof targetContext?.artifact_id === "string"
      ? targetContext.artifact_id
      : undefined;
    const isFinalPptxTarget = targetContext?.review_target === "final_pptx";
    const qualityStatus = isRecord(item.result?.qualityStatus)
      ? item.result.qualityStatus
      : undefined;
    const fidelity = qualityStatus && isRecord(qualityStatus.currentArtifactFidelity)
      ? qualityStatus.currentArtifactFidelity
      : undefined;
    const fidelityArtifactId = fidelity && typeof fidelity.artifactId === "string"
      ? fidelity.artifactId
      : undefined;
    if ((!isFinalPptxTarget || targetArtifactId !== artifactId) && fidelityArtifactId !== artifactId) {
      return [];
    }
    const fidelityStatus = fidelity && typeof fidelity.status === "string"
      ? fidelity.status
      : undefined;
    const status: FinalPptxReviewStatus = item.status === "succeeded"
      ? fidelityStatus === "passed" ? "passed" : "failed"
      : item.status === "queued" && item.capacityWaiting && item.waitReason === "svg_final_pptx_office_instance_busy"
        ? "deferred"
        : item.status === "queued" || item.status === "running" ? "running" : "failed";
    return [{ status, updatedAt: item.updatedAt ?? "" }];
  });
  return matches.sort((left, right) => left.updatedAt.localeCompare(right.updatedAt)).at(-1)?.status;
}
