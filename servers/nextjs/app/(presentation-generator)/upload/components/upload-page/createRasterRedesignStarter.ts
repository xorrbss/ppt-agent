"use client";
/**
 * Starts the "image-only source, A4 SVG" raster-redesign flow: creates the
 * presentation, kicks off raster analysis, waits for it, and either hands
 * off straight to generation (zero low-confidence regions) or opens the
 * review screen at page 1. Extracted from useUploadPageState.ts's
 * startPresentation (file-size ratchet split); behaviour unchanged, only the
 * module boundary moved.
 */
import type { Dispatch, MutableRefObject, SetStateAction } from "react";
import type { Dispatch as ReduxDispatch, UnknownAction } from "redux";
import { clearOutlines, setPresentationId } from "@/store/slices/presentationGeneration";
import { notify } from "@/components/ui/sonner";
import type { PresentationConfig } from "../../type";
import { PresentationGenerationApi } from "../../../services/api/presentation-generation";
import {
  RasterSourceApi,
  rasterPageDetail,
  type RasterAnalysisMode,
  type RasterOperationWire,
  type RasterReviewPageDetail,
} from "../../../services/api/source-content/raster-contract";
import { resolveDeckTheme } from "../applyDeckTheme";
import type { SourceContentAdmission } from "./useSourceContentAdmission";
import {
  forgetRasterReview,
  isRasterPollTimeout,
  notifyRasterAdvisories,
  rasterWaitMessage,
  rememberRasterReview,
  type RasterReviewRecovery,
} from "./raster-review-recovery";
import type { LoadingState } from "./types";

interface RasterRedesignStarterDeps {
  config: PresentationConfig;
  authoredStyle: string;
  rasterAdvancedReview: boolean;
  selectedThemeId: string | null;
  pendingPresentationId: string;
  previousPresentationId: string | null | undefined;
  bindPresentation?: (id: string) => void;
  dispatch: ReduxDispatch<UnknownAction>;
  setRasterOperation: Dispatch<SetStateAction<RasterOperationWire | null>>;
  setRasterPage: Dispatch<SetStateAction<RasterReviewPageDetail | null>>;
  setRasterRecovery: Dispatch<SetStateAction<RasterReviewRecovery | null>>;
  setRasterRetryAvailable: Dispatch<SetStateAction<boolean>>;
  setLoadingState: Dispatch<SetStateAction<LoadingState>>;
  aliveRef: MutableRefObject<boolean>;
  handoffRasterGeneration: (operation: RasterOperationWire, recovery: RasterReviewRecovery) => void;
  handleGenerationError: (error: unknown) => void;
  abandonRun: (reason: string) => void;
}

// Every asynchronous branch receives the same click-time canvas configuration.
export function createRasterRedesignStarter({
  config, authoredStyle, rasterAdvancedReview, selectedThemeId, pendingPresentationId,
  previousPresentationId, bindPresentation, dispatch, setRasterOperation, setRasterPage,
  setRasterRecovery, setRasterRetryAvailable, setLoadingState, aliveRef,
  handoffRasterGeneration, handleGenerationError, abandonRun,
}: RasterRedesignStarterDeps) {
  return async function startRasterRedesignFlow(admitted: SourceContentAdmission) {
    if (authoredStyle !== "screenshot-blue") {
      notify.warning("템플릿을 확인해 주세요", "이미지형 A4 SVG 재구성은 현재 screenshot-blue 템플릿에서 지원합니다. 템플릿을 선택한 뒤 다시 시작해 주세요.");
      abandonRun("이미지형 A4 SVG 재구성에 필요한 screenshot-blue 템플릿을 선택해 주세요.");
      return;
    }
    try {
      setRasterOperation(null);
      setRasterPage(null);
      // "auto_redesign" is the product default: a real, editable A4 rebuild.
      // The advanced-options toggle is the only way back to the fully
      // manual, region-by-region "reconstruct" review.
      // Only two of the three backend-accepted modes are reachable from this
      // toggle; the cast keeps `analysisMode` typed as the full union so the
      // "auto_preserve" branches below (shared with the recovery/retry flows)
      // still type-check against every mode the backend accepts.
      const analysisMode = (rasterAdvancedReview ? "reconstruct" : "auto_redesign") as RasterAnalysisMode;
      setLoadingState({
        isLoading: true,
        message: analysisMode === "auto_redesign"
          ? "원본 화면을 편집 가능한 A4 슬라이드로 재구성할 준비를 하는 중…"
          : "원본 화면의 영역과 인식 내용을 분석하는 중…",
        showProgress: true,
      });
      const theme = await resolveDeckTheme(selectedThemeId);
      const created = await PresentationGenerationApi.createPresentation({
        presentation_id: pendingPresentationId,
        previous_presentation_id: previousPresentationId ?? undefined,
        idempotency_key: `presentation-create:${pendingPresentationId}`,
        content: "",
        n_slides: null,
        reference_snapshot_ids: [admitted.documentAdmission.referenceSnapshotId],
        language: null,
        instructions: [config.prompt, config.instructions].map((part) => part?.trim()).filter(Boolean).join("\n") || null,
        include_table_of_contents: false,
        include_title_slide: false,
        web_search: false,
        page_size: "A4",
        orientation: config.orientation ?? "landscape",
        theme,
        strategy: "svg",
      });
      bindPresentation?.(created.id);
      dispatch(setPresentationId(created.id));
      dispatch(clearOutlines());
      const pending = await RasterSourceApi.start({
        reference_snapshot_id: admitted.documentAdmission.referenceSnapshotId,
        presentation_id: created.id,
        orientation: config.orientation ?? "landscape",
        analysis_mode: analysisMode,
        style_id: "screenshot-blue",
      });
      const recovery = {
        presentationId: created.id,
        operationId: pending.operation_id,
        referenceSnapshotId: admitted.documentAdmission.referenceSnapshotId,
        analysisMode,
        orientation: config.orientation ?? "landscape",
        authoredStyle,
        instructions: [config.prompt, config.instructions].map((part) => part?.trim()).filter(Boolean).join("\n") || null,
      } satisfies RasterReviewRecovery;
      rememberRasterReview(recovery);
      setRasterRecovery(recovery);
      const operation = await RasterSourceApi.waitForReady(pending, (progress) => {
        if (!aliveRef.current) return;
        setLoadingState({
          isLoading: true,
          message: analysisMode === "auto_preserve"
            ? rasterWaitMessage(progress, "원본 화면 보존을 기다리는 중…", "원본 화면을 A4로 보존하는 중…")
            : analysisMode === "auto_redesign"
            ? rasterWaitMessage(progress, "원본 화면 재구성을 기다리는 중…", "원본 화면을 편집 가능한 텍스트와 표로 재구성하는 중…")
            : rasterWaitMessage(progress, "원본 화면 분석을 기다리는 중…", "원본 화면의 영역과 인식 내용을 분석하는 중…"),
          showProgress: true,
        });
      });
      if (!aliveRef.current) return;
      rememberRasterReview({ ...recovery, operationId: operation.operation_id });
      setRasterRecovery({ ...recovery, operationId: operation.operation_id });
      if (analysisMode === "auto_preserve" ||
          (analysisMode === "auto_redesign" && operation.approval_pin)) {
        // Zero low-confidence regions: proceed straight to generation with
        // no extra confirmation, exactly as auto_preserve already does.
        notifyRasterAdvisories(operation);
        handoffRasterGeneration(operation, { ...recovery, operationId: operation.operation_id });
        return;
      }
      const detail = await RasterSourceApi.page(operation.operation_id, 1);
      setRasterOperation(operation);
      setRasterPage(rasterPageDetail(operation, detail));
      setRasterRetryAvailable(false);
      setLoadingState({ isLoading: false, message: "원본 화면과 인식 내용을 검토해 주세요.", showProgress: false });
    } catch (error) {
      if (isRasterPollTimeout(error)) {
        setRasterRetryAvailable(true);
        setLoadingState({ isLoading: false, message: "원본 화면 분석이 오래 걸리고 있습니다. 분석 상태를 다시 확인해 주세요.", showProgress: false });
      } else {
        forgetRasterReview();
        setRasterRecovery(null);
        handleGenerationError(error);
      }
    }
  };
}
