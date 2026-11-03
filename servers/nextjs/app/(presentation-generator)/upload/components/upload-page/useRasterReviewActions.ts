"use client";
import { commonHeaderDesignForConfig } from "@/lib/common-header-design";
import { useTranslations as useA2bTranslations } from "next-intl";
/**
 * Owns the raster review's operation/page state and its per-request actions
 * (page navigation, region/page-mode edits, inventory confirmation,
 * approval, auto-preserve fallback, and retry-after-timeout). The batched
 * "select/preserve the whole deck" actions live in useRasterBatchPreserve.ts.
 * Extracted from useUploadPageState.ts (file-size ratchet split); behaviour
 * is unchanged, only the module boundary moved.
 */
import { useCallback, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";

import { notify } from "@/components/ui/sonner";
import type { PresentationConfig } from "../../type";
import {
  RasterSourceApi,
  rasterApprovalForUi,
  rasterCasBody,
  rasterPageDetail,
  type RasterAnalysisPin,
  type RasterOperationWire,
  type RasterPageModeRequest,
  type RasterReviewPageDetail,
  type RasterReviewSaveRequest,
} from "../../../services/api/source-content/raster-contract";
import {
  forgetRasterReview,
  isRasterPollTimeout,
  notifyRasterAdvisories,
  rasterWaitMessage,
  rememberRasterReview,
  type RasterReviewRecovery,
} from "./raster-review-recovery";
import type { LoadingState } from "./types";

interface RasterReviewActionsDeps {
  rasterRecovery: RasterReviewRecovery | null;
  setRasterRecovery: (value: RasterReviewRecovery | null) => void;
  pendingPresentationId: string;
  config: PresentationConfig;
  authoredStyle: string;
  aliveRef: MutableRefObject<boolean>;
  startInFlightRef: MutableRefObject<boolean>;
  setLoadingState: Dispatch<SetStateAction<LoadingState>>;
  handoffRasterGeneration: (operation: RasterOperationWire, recovery: RasterReviewRecovery) => void;
  handleGenerationError: (error: unknown) => void;
}

export const useRasterReviewActions = ({
  rasterRecovery,
  setRasterRecovery,
  pendingPresentationId,
  config,
  authoredStyle,
  aliveRef,
  startInFlightRef,
  setLoadingState,
  handoffRasterGeneration,
  handleGenerationError,
}: RasterReviewActionsDeps) => {
  const tA2b = useA2bTranslations("uploadExtras");
  const [rasterOperation, setRasterOperation] = useState<RasterOperationWire | null>(null);
  const [rasterPage, setRasterPage] = useState<RasterReviewPageDetail | null>(null);
  const [isRasterPageLoading, setIsRasterPageLoading] = useState(false);
  const [rasterRetryAvailable, setRasterRetryAvailable] = useState(false);

  const loadRasterPage = useCallback(async (pageIndex: number) => {
    if (!rasterOperation) return;
    setIsRasterPageLoading(true);
    try {
      const detail = await RasterSourceApi.page(rasterOperation.operation_id, pageIndex + 1);
      setRasterPage(rasterPageDetail(rasterOperation, detail));
    } catch {
      notify.error(tA2b("couldNotLoadTheSourcePage"), tA2b("pleaseTryAgain"));
    } finally {
      setIsRasterPageLoading(false);
    }
  }, [rasterOperation, tA2b]);

  const saveRasterReview = useCallback(async (request: RasterReviewSaveRequest) => {
    if (!rasterOperation || request.operationId !== rasterOperation.operation_id ||
        request.revision !== rasterOperation.analysis.revision ||
        request.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const updated = await RasterSourceApi.correct(request.operationId, {
      ...rasterCasBody(request),
      region_edits: request.edits.map((edit) => ({
        region_id: edit.regionId,
        ...(edit.kind ? { role: edit.kind } : {}),
        ...(edit.confirmedText !== undefined ? { text: edit.confirmedText } : {}),
        ...(edit.sourcePixelBox ? { source_pixel_bbox: [
          edit.sourcePixelBox.x,
          edit.sourcePixelBox.y,
          edit.sourcePixelBox.width,
          edit.sourcePixelBox.height,
        ] as [number, number, number, number] } : {}),
        reviewed: edit.reviewed,
      })),
      page_edits: [],
      review_ocr: false,
      resolve_unresolved: false,
    });
    const detail = await RasterSourceApi.page(updated.operation_id, request.pageIndex + 1);
    setRasterOperation(updated);
    setRasterPage(rasterPageDetail(updated, detail));
  }, [rasterOperation]);

  const setRasterPageMode = useCallback(async (request: RasterPageModeRequest) => {
    if (!rasterOperation || request.operationId !== rasterOperation.operation_id ||
        request.revision !== rasterOperation.analysis.revision ||
        request.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const updated = await RasterSourceApi.correct(request.operationId, {
      ...rasterCasBody(request),
      region_edits: [],
      page_edits: [{
        page_number: request.pageIndex + 1,
        render_mode: request.renderMode,
        reviewed: request.reviewed,
        acknowledge_image_only: request.acknowledgeImageOnly,
        acknowledge_legibility: request.acknowledgeLegibility,
      }],
      review_ocr: false,
      resolve_unresolved: false,
    });
    const detail = await RasterSourceApi.page(updated.operation_id, request.pageIndex + 1);
    setRasterOperation(updated);
    setRasterPage(rasterPageDetail(updated, detail));
  }, [rasterOperation]);

  const confirmRasterInventory = useCallback(async (pin: RasterAnalysisPin) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const updated = await RasterSourceApi.correct(pin.operationId, {
      ...rasterCasBody(pin),
      region_edits: [],
      page_edits: [],
      review_ocr: true,
      resolve_unresolved: true,
    });
    const detail = await RasterSourceApi.page(updated.operation_id, (rasterPage?.pageIndex ?? 0) + 1);
    setRasterOperation(updated);
    setRasterPage(rasterPageDetail(updated, detail));
  }, [rasterOperation, rasterPage?.pageIndex]);

  const approveRasterReview = useCallback(async (pin: RasterAnalysisPin) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const approved = await RasterSourceApi.approve(pin.operationId, rasterCasBody(pin));
    if (!approved.approval_pin || approved.analysis.digest !== rasterOperation.analysis.digest) {
      throw new Error("raster_approval_pin_invalid");
    }
    setRasterOperation(approved);
    handoffRasterGeneration(approved, {
      ...(rasterRecovery ?? {
      presentationId: pendingPresentationId,
      operationId: approved.operation_id,
      analysisMode: "reconstruct",
      referenceSnapshotId: approved.reference_snapshot_id ?? undefined,
      svgDesignReferenceSnapshotId: config.svgDesignReferenceSnapshotId,
      ...(approved.analysis.target_canvas.svg_canvas ? { svgCanvas: { width_mm: Number(approved.analysis.target_canvas.svg_canvas.width_mm), height_mm: Number(approved.analysis.target_canvas.svg_canvas.height_mm) } }
        : approved.analysis.target_canvas.aspect_ratio ? { aspectRatio: approved.analysis.target_canvas.aspect_ratio } : {}),
      orientation: approved.analysis.target_canvas.orientation,
      authoredStyle,
      instructions: [config.prompt, config.instructions].map((part) => part?.trim()).filter(Boolean).join("\n") || null,
      }),
      commonHeaderMode: config.commonHeaderMode ?? "uniform",
      commonHeaderDesign: commonHeaderDesignForConfig(config),
      commonHeaderCoverException: (config.commonHeaderMode ?? "uniform") === "uniform" && (config.commonHeaderCoverException ?? false),
    });
    return rasterApprovalForUi(approved);
  }, [rasterOperation, rasterRecovery, pendingPresentationId, config, authoredStyle, handoffRasterGeneration]);

  const autoPreserveExistingRaster = async () => {
    const operation = rasterOperation;
    const recovery = rasterRecovery;
    const snapshotId = recovery?.referenceSnapshotId ?? operation?.reference_snapshot_id;
    if (!operation || !recovery || !snapshotId || recovery.analysisMode === "auto_preserve" ||
        startInFlightRef.current) return;
    if ((config.commonHeaderMode ?? "uniform") === "uniform") {
      notify.warning(tA2b("commonHeaderModeRequired"), tA2b("commonHeaderRasterPreservationRequired"));
      return;
    }
    if ((recovery.authoredStyle && recovery.authoredStyle !== "screenshot-blue") || recovery.svgDesignReferenceSnapshotId) {
      notify.warning(tA2b("commonHeaderRedesignRequired"), tA2b("commonHeaderRedesignRequiredBody"));
      return;
    }
    startInFlightRef.current = true;
    setLoadingState({ isLoading: true, message: "원본 화면을 선택한 크기로 보존할 준비를 하는 중…", showProgress: true });
    try {
      const pending = await RasterSourceApi.start({
        reference_snapshot_id: snapshotId,
        presentation_id: recovery.presentationId,
        ...(recovery.svgCanvas ? { svg_canvas: recovery.svgCanvas } : recovery.aspectRatio ? { aspect_ratio: recovery.aspectRatio } : { orientation: recovery.orientation }),
        analysis_mode: "auto_preserve",
        style_id: recovery.authoredStyle || "screenshot-blue",
      });
      const autoRecovery = { ...recovery, commonHeaderMode: "free" as const, commonHeaderCoverException: false, referenceSnapshotId: snapshotId,
        analysisMode: "auto_preserve" as const, operationId: pending.operation_id };
      rememberRasterReview(autoRecovery);
      setRasterRecovery(autoRecovery);
      setRasterOperation(null);
      setRasterRetryAvailable(false);
      const approved = await RasterSourceApi.waitForReady(pending, (progress) => {
        if (aliveRef.current) setLoadingState({ isLoading: true,
          message: rasterWaitMessage(progress, "원본 화면 보존을 기다리는 중…", "원본 화면을 선택한 크기로 보존하는 중…"),
          showProgress: true });
      });
      if (!aliveRef.current) return;
      handoffRasterGeneration(approved, { ...autoRecovery, operationId: approved.operation_id });
    } catch (error) {
      if (isRasterPollTimeout(error)) {
        setRasterRetryAvailable(true);
        setLoadingState({ isLoading: false, message: "원본 화면 보존이 오래 걸리고 있습니다. 상태를 다시 확인해 주세요.", showProgress: false });
      } else {
        rememberRasterReview(recovery);
        setRasterRecovery(recovery);
        setRasterOperation(operation);
        setLoadingState({ isLoading: false, message: "원본 보존으로 전환하지 못했습니다. 기존 검토 상태를 유지합니다.", showProgress: false });
        notify.error(tA2b("couldNotStartSourceScreenPreservation"), tA2b("existingReviewContentRemainsSaved"));
      }
    } finally {
      startInFlightRef.current = false;
    }
  };

  const retryRasterAnalysis = async () => {
    if (!rasterRecovery || startInFlightRef.current) return;
    startInFlightRef.current = true;
    setRasterRetryAvailable(false);
    setLoadingState({ isLoading: true, message: "원본 화면 분석 상태를 확인하는 중…", showProgress: true });
    try {
      const pending = await RasterSourceApi.get(rasterRecovery.operationId);
      const operation = await RasterSourceApi.waitForReady(pending, (progress) => {
        if (!aliveRef.current) return;
        setLoadingState({
          isLoading: true,
          message: rasterWaitMessage(progress, "원본 화면 분석을 기다리는 중…", "원본 화면의 영역과 인식 내용을 분석하는 중…"),
          showProgress: true,
        });
      });
      if (!aliveRef.current) return;
      if (rasterRecovery.analysisMode === "auto_preserve" ||
          (rasterRecovery.analysisMode === "auto_redesign" && operation.approval_pin)) {
        notifyRasterAdvisories(operation);
        handoffRasterGeneration(operation, { ...rasterRecovery, operationId: operation.operation_id });
        return;
      }
      const detail = await RasterSourceApi.page(operation.operation_id, 1);
      if (!aliveRef.current) return;
      setRasterOperation(operation);
      setRasterPage(rasterPageDetail(operation, detail));
      setLoadingState({ isLoading: false, message: "원본 화면과 인식 내용을 검토해 주세요.", showProgress: false });
    } catch (error) {
      if (!aliveRef.current) return;
      if (isRasterPollTimeout(error)) {
        setRasterRetryAvailable(true);
        setLoadingState({ isLoading: false, message: "원본 화면 분석이 오래 걸리고 있습니다. 분석 상태를 다시 확인해 주세요.", showProgress: false });
      } else {
        forgetRasterReview();
        setRasterRecovery(null);
        handleGenerationError(error);
      }
    } finally {
      startInFlightRef.current = false;
    }
  };

  return {
    rasterOperation,
    setRasterOperation,
    rasterPage,
    setRasterPage,
    isRasterPageLoading,
    rasterRetryAvailable,
    setRasterRetryAvailable,
    loadRasterPage,
    saveRasterReview,
    setRasterPageMode,
    confirmRasterInventory,
    approveRasterReview,
    autoPreserveExistingRaster,
    retryRasterAnalysis,
  };
};
