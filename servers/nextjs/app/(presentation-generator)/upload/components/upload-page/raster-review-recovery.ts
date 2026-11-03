/**
 * Session-storage recovery bookkeeping for an in-progress raster review, plus
 * the small polling/notice helpers shared across the raster-redesign flow.
 * Extracted from useUploadPageState.ts (file-size ratchet split); behaviour
 * is unchanged, only the module boundary moved.
 */
import { createTranslator } from "next-intl";
import { notify } from "@/components/ui/sonner";
import { resolveUiLocale } from "@/i18n/locale";
import { getUiMessages } from "@/i18n/messages";
import { uploadExtraTranslator } from "./uploadExtraTranslator";
import type {
  RasterAnalysisMode,
  RasterOperationWire,
  RasterPendingOperationWire,
} from "../../../services/api/source-content/raster-contract";
import { otherAdvisoryCount, visionReaderUnavailable } from "../../../services/api/source-content/raster-unresolved";

export const RASTER_REVIEW_RECOVERY_KEY = "presenton:raster-review-operation:v1";

export type RasterReviewRecovery = {
  presentationId: string;
  operationId: string;
  referenceSnapshotId?: string;
  analysisMode?: RasterAnalysisMode;
  svgDesignReferenceSnapshotId?: string;
  commonHeaderDesign?: import("@/lib/common-header-design").CommonHeaderDesign;
  commonHeaderMode?: "uniform" | "free";
  commonHeaderCoverException?: boolean;
  aspectRatio?: import("../../type").PresentationConfig["aspectRatio"];
  svgCanvas?: import("../../utils/svgCanvasSelection").SvgCanvasSelection;
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

// Called outside React (flow helpers): the page's rendered UI locale picks the catalog.
const rasterReviewTranslator = () => {
  const locale = resolveUiLocale(typeof document === "undefined" ? undefined : document.documentElement.lang);
  return createTranslator({ locale, messages: getUiMessages(locale), namespace: "upload.rasterReview" });
};

export const notifyRasterAdvisories = (operation: RasterOperationWire) => {
  const unresolved = operation.analysis.unresolved;
  if (visionReaderUnavailable(unresolved)) {
    // Deck-level, shown once: every page keeps its text as pixels.
    const t = rasterReviewTranslator();
    notify.warning(t("visionUnavailableTitle"), t("visionUnavailable"));
  }
  const count = otherAdvisoryCount(operation.advisory_count ?? 0, unresolved);
  if (count <= 0 || count > RASTER_ADVISORY_NOTICE_MAX) return;
  const t = uploadExtraTranslator();
  notify.info(
    t("reviewItemsRecommended"),
    t("uncertainItemsNotice", { count }),
  );
};
