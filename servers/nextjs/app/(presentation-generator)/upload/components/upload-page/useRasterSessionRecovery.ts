"use client";
import { commonHeaderDesignForConfig } from "@/lib/common-header-design";
import { useTranslations as useA2bTranslations } from "next-intl";
/**
 * On mount, restores an in-progress raster review from sessionStorage (a
 * browser refresh or back-navigation should not lose it) and either hands it
 * straight off to generation (once its approval pin already exists) or
 * re-opens the review screen at page 1. Extracted from useUploadPageState.ts
 * (file-size ratchet split); behaviour unchanged, only the module boundary
 * moved.
 */
import { useEffect, type Dispatch, type SetStateAction } from "react";
import type { Dispatch as ReduxDispatch, UnknownAction } from "redux";

import { notify } from "@/components/ui/sonner";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import type { PresentationConfig } from "../../type";
import {
  RasterSourceApi,
  rasterPageDetail,
  type RasterOperationWire,
  type RasterReviewPageDetail,
} from "../../../services/api/source-content/raster-contract";
import {
  RASTER_REVIEW_RECOVERY_KEY,
  forgetRasterReview,
  isRasterPollTimeout,
  notifyRasterAdvisories,
  rememberRasterReview,
  type RasterReviewRecovery,
} from "./raster-review-recovery";
import type { LoadingState } from "./types";

interface RasterSessionRecoveryDeps {
  setRasterAdvancedReview: Dispatch<SetStateAction<boolean>>;
  setPendingPresentationId: Dispatch<SetStateAction<string>>;
  setRasterRecovery: Dispatch<SetStateAction<RasterReviewRecovery | null>>;
  setConfig: Dispatch<SetStateAction<PresentationConfig>>;
  dispatch: ReduxDispatch<UnknownAction>;
  setGenerationAttemptActive: Dispatch<SetStateAction<boolean>>;
  setLoadingState: Dispatch<SetStateAction<LoadingState>>;
  setRasterRetryAvailable: Dispatch<SetStateAction<boolean>>;
  setRasterOperation: Dispatch<SetStateAction<RasterOperationWire | null>>;
  setRasterPage: Dispatch<SetStateAction<RasterReviewPageDetail | null>>;
  handoffRasterGeneration: (operation: RasterOperationWire, recovery: RasterReviewRecovery) => void;
}

export const useRasterSessionRecovery = ({
  setRasterAdvancedReview,
  setPendingPresentationId,
  setRasterRecovery,
  setConfig,
  dispatch,
  setGenerationAttemptActive,
  setLoadingState,
  setRasterRetryAvailable,
  setRasterOperation,
  setRasterPage,
  handoffRasterGeneration,
}: RasterSessionRecoveryDeps) => {
  const tA2b = useA2bTranslations("uploadExtras");
  useEffect(() => {
    let saved: RasterReviewRecovery | null = null;
    try {
      const raw = window.sessionStorage.getItem(RASTER_REVIEW_RECOVERY_KEY);
      if (raw) saved = JSON.parse(raw) as RasterReviewRecovery;
    } catch { forgetRasterReview(); }
    if (!saved?.operationId || !saved.presentationId || !saved.authoredStyle) return;
    const recovery = saved;
    const controller = new AbortController();
    setRasterAdvancedReview(recovery.analysisMode === "reconstruct");
    setPendingPresentationId(recovery.presentationId);
    setRasterRecovery(recovery);
    setConfig((previous) => ({
      ...previous, sourceCanvas: undefined, svgDesignReferencePending: false,
      svgDesignReferenceSnapshotId: recovery.svgDesignReferenceSnapshotId,
      commonHeaderMode: recovery.commonHeaderMode ?? "uniform",
      commonHeaderDesign: commonHeaderDesignForConfig(recovery),
      commonHeaderCoverException: recovery.commonHeaderMode === "uniform" && (recovery.commonHeaderCoverException ?? false),
      svgCanvas: recovery.svgCanvas,
      ...(recovery.svgCanvas ? { pageSize: undefined, orientation: undefined }
        : recovery.aspectRatio ? { pageSize: undefined, orientation: undefined, aspectRatio: recovery.aspectRatio }
        : { pageSize: "A4", orientation: recovery.orientation }),
    }));
    dispatch(setPptGenUploadState({ authoredStyle: recovery.authoredStyle, generationStrategy: "svg", sourceContentMode: true }));
    setGenerationAttemptActive(true);
    setLoadingState({ isLoading: true, message: "이전 원본 분석을 다시 불러오는 중…", showProgress: true });
    void (async () => {
      try {
        const pending = await RasterSourceApi.get(recovery.operationId);
        const operation = await RasterSourceApi.waitForReady(pending, undefined, controller.signal);
        if (controller.signal.aborted) return;
        const resumedRecovery = {
          ...recovery,
          operationId: operation.operation_id,
          referenceSnapshotId: recovery.referenceSnapshotId ?? operation.reference_snapshot_id ?? undefined,
        };
        if (recovery.analysisMode === "auto_preserve" ||
            (recovery.analysisMode === "auto_redesign" && operation.approval_pin)) {
          notifyRasterAdvisories(operation);
          handoffRasterGeneration(operation, resumedRecovery);
          return;
        }
        const detail = await RasterSourceApi.page(operation.operation_id, 1);
        if (controller.signal.aborted) return;
        rememberRasterReview(resumedRecovery);
        setRasterRecovery(resumedRecovery);
        setRasterOperation(operation);
        setRasterPage(rasterPageDetail(operation, detail));
        setLoadingState({ isLoading: false, message: "원본 화면과 인식 내용을 검토해 주세요.", showProgress: false });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (isRasterPollTimeout(error)) {
          setRasterRetryAvailable(true);
          setLoadingState({ isLoading: false, message: "원본 화면 분석이 오래 걸리고 있습니다. 분석 상태를 다시 확인해 주세요.", showProgress: false });
          return;
        }
        forgetRasterReview();
        setRasterRecovery(null);
        setGenerationAttemptActive(false);
        setLoadingState({ isLoading: false, message: "", showProgress: false });
        notify.error(tA2b("couldNotLoadThePreviousSourceAnalysis"), tA2b("checkOwnershipPermissionsOrAnalysisStatus"));
      }
    })();
    return () => controller.abort();
    // Recovery is intentionally keyed to this browser tab's saved operation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
};
