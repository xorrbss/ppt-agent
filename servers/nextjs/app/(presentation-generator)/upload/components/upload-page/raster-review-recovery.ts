/**
 * Session-storage recovery bookkeeping for an in-progress raster review, plus
 * the small polling/notice helpers shared across the raster-redesign flow.
 * Extracted from useUploadPageState.ts (file-size ratchet split); behaviour
 * is unchanged, only the module boundary moved.
 */
import { notify } from "@/components/ui/sonner";
import type {
  RasterAnalysisMode,
  RasterOperationWire,
  RasterPendingOperationWire,
} from "../../../services/api/source-content/raster-contract";

export const RASTER_REVIEW_RECOVERY_KEY = "presenton:raster-review-operation:v1";

export type RasterReviewRecovery = {
  presentationId: string;
  operationId: string;
  referenceSnapshotId?: string;
  analysisMode?: RasterAnalysisMode;
  orientation: "landscape" | "portrait";
  authoredStyle: string;
  instructions: string | null;
};

export const rememberRasterReview = (value: RasterReviewRecovery) => {
  try {
    window.sessionStorage.setItem(RASTER_REVIEW_RECOVERY_KEY, JSON.stringify(value));
  } catch {
    /* Browser storage may be unavailable. */
  }
};

export const forgetRasterReview = () => {
  try {
    window.sessionStorage.removeItem(RASTER_REVIEW_RECOVERY_KEY);
  } catch {
    /* Browser storage may be unavailable. */
  }
};

export const isRasterPollTimeout = (error: unknown) =>
  error instanceof Error && error.message === "raster_analysis_timed_out";

// The server reports incremental, elapsed-time progress (analyze_redesign_pptx
// runs OCR plus a vision-LLM call per page and can take minutes on a large
// deck); prefer that live text over the generic queued/running fallback so a
// long wait keeps showing real progress instead of one static message.
export const rasterWaitMessage = (
  pending: RasterPendingOperationWire,
  queuedText: string,
  runningText: string,
) => pending.progress?.message || (pending.status === "queued" ? queuedText : runningText);

// Non-blocking: advisory items never gate approval (raster_review_blockers
// excludes them) and generation proceeds regardless. Only surfaced when the
// count is small enough to skim as a toast, not a review workflow.
export const RASTER_ADVISORY_NOTICE_MAX = 20;

export const notifyRasterAdvisories = (operation: RasterOperationWire) => {
  const count = operation.advisory_count ?? 0;
  if (count <= 0 || count > RASTER_ADVISORY_NOTICE_MAX) return;
  notify.info(
    "검토를 권장하는 항목이 있습니다",
    `서버가 확신하지 못한 항목 ${count}개가 있습니다. 생성은 그대로 진행되며, 결과에서 해당 부분을 확인해 주세요.`,
  );
};
