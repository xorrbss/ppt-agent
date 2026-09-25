"use client";
/**
 * Batch page-mode edits for the raster review: selecting the server's
 * recommended full-page-preserve pages, and driving the "preserve the whole
 * deck" select/confirm stages five pages at a time. Extracted from
 * useUploadPageState.ts (file-size ratchet split); behaviour unchanged.
 */
import { useCallback, type Dispatch, type SetStateAction } from "react";

import {
  RasterSourceApi,
  rasterCasBody,
  rasterPageDetail,
  rasterReviewPin,
  type RasterAnalysisPin,
  type RasterOperationWire,
  type RasterReviewPageDetail,
} from "../../../services/api/source-content/raster-contract";

interface RasterBatchPreserveDeps {
  rasterOperation: RasterOperationWire | null;
  setRasterOperation: Dispatch<SetStateAction<RasterOperationWire | null>>;
  rasterPage: RasterReviewPageDetail | null;
  setRasterPage: Dispatch<SetStateAction<RasterReviewPageDetail | null>>;
}

export const useRasterBatchPreserve = ({
  rasterOperation,
  setRasterOperation,
  rasterPage,
  setRasterPage,
}: RasterBatchPreserveDeps) => {
  const selectRecommendedRasterPages = useCallback(async (
    pin: RasterAnalysisPin,
    pageIndexes: number[],
    onProgress: (selected: number, total: number) => void,
  ) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const eligible = new Set(rasterOperation.analysis.pages
      .filter((page) => page.render_mode === "reconstruct" && page.recommended_render_mode === "full_page_preserve")
      .map((page) => page.page_number - 1));
    const targets = [...new Set(pageIndexes)].filter((index) => eligible.has(index)).sort((a, b) => a - b);
    let current = rasterOperation;
    let selected = 0;
    let failure: unknown = null;
    try {
      for (let index = 0; index < targets.length; index += 5) {
        const batch = targets.slice(index, index + 5);
        current = await RasterSourceApi.correct(current.operation_id, {
          ...rasterCasBody(rasterReviewPin(current)),
          region_edits: [],
          page_edits: batch.map((pageIndex) => ({
            page_number: pageIndex + 1,
            render_mode: "full_page_preserve" as const,
            reviewed: false,
            acknowledge_image_only: false,
            acknowledge_legibility: false,
          })),
          review_ocr: false,
          resolve_unresolved: false,
        });
        selected += batch.length;
        onProgress(selected, targets.length);
      }
    } catch (error) {
      failure = error;
    }
    try {
      const latest = await RasterSourceApi.get(current.operation_id);
      if (latest.analysis) current = latest;
      const detail = await RasterSourceApi.page(current.operation_id, (rasterPage?.pageIndex ?? 0) + 1);
      setRasterOperation(current);
      setRasterPage(rasterPageDetail(current, detail));
    } catch (error) {
      if (!failure) failure = error;
    }
    if (failure) throw failure;
  }, [rasterOperation, rasterPage?.pageIndex, setRasterOperation, setRasterPage]);

  const preserveRasterDeck = useCallback(async (
    pin: RasterAnalysisPin,
    stage: "select" | "confirm",
    onProgress: (completed: number, total: number) => void,
  ) => {
    if (!rasterOperation || pin.operationId !== rasterOperation.operation_id ||
        pin.revision !== rasterOperation.analysis.revision ||
        pin.operationVersion !== rasterOperation.operation_version) {
      throw new Error("raster_review_stale");
    }
    const pages = rasterOperation.analysis.pages;
    const total = rasterOperation.analysis.source.page_count;
    if (pages.length !== total || new Set(pages.map((page) => page.page_number)).size !== total ||
        pages.some((page) => page.page_number < 1 || page.page_number > total) ||
        (stage === "confirm" && pages.some((page) =>
          page.render_mode !== "full_page_preserve" || !page.full_page_region))) {
      throw new Error("raster_deck_pages_incomplete");
    }
    const isConfirmed = (page: typeof pages[number]) =>
      page.render_mode === "full_page_preserve" && page.full_page_region?.review_required === false &&
      page.full_page_preserve_acknowledgements.includes("image_only") &&
      page.full_page_preserve_acknowledgements.includes("legibility");
    const targets = pages.filter((page) => stage === "select"
      ? page.render_mode !== "full_page_preserve"
      : !isConfirmed(page)).map((page) => page.page_number);
    let current = rasterOperation;
    let failure: unknown = null;
    onProgress(total - targets.length, total);
    try {
      for (let index = 0; index < targets.length; index += 5) {
        const batch = targets.slice(index, index + 5);
        current = await RasterSourceApi.correct(current.operation_id, {
          ...rasterCasBody(rasterReviewPin(current)),
          region_edits: [],
          page_edits: batch.map((pageNumber) => ({
            page_number: pageNumber,
            render_mode: "full_page_preserve" as const,
            reviewed: stage === "confirm",
            acknowledge_image_only: stage === "confirm",
            acknowledge_legibility: stage === "confirm",
          })),
          review_ocr: false,
          resolve_unresolved: false,
        });
        const verified = current.analysis.pages.filter((page) => stage === "select"
          ? page.render_mode === "full_page_preserve" && Boolean(page.full_page_region)
          : isConfirmed(page)).length;
        if (batch.some((pageNumber) => {
          const page = current.analysis.pages.find((item) => item.page_number === pageNumber);
          return !page || (stage === "select"
            ? page.render_mode !== "full_page_preserve" || !page.full_page_region
            : !isConfirmed(page));
        })) throw new Error("raster_deck_batch_incomplete");
        onProgress(verified, total);
      }
      if (current.analysis.pages.length !== total || current.analysis.pages.some((page) => stage === "select"
        ? page.render_mode !== "full_page_preserve" || !page.full_page_region
        : !isConfirmed(page)) || (stage === "confirm" && !current.can_approve)) {
        throw new Error("raster_deck_review_incomplete");
      }
    } catch (error) {
      failure = error;
    }
    try {
      const latest = await RasterSourceApi.get(current.operation_id);
      if (latest.analysis) current = latest;
      const detail = await RasterSourceApi.page(current.operation_id, (rasterPage?.pageIndex ?? 0) + 1);
      setRasterOperation(current);
      setRasterPage(rasterPageDetail(current, detail));
    } catch (error) {
      if (!failure) failure = error;
    }
    if (failure) throw failure;
  }, [rasterOperation, rasterPage?.pageIndex, setRasterOperation, setRasterPage]);

  return { selectRecommendedRasterPages, preserveRasterDeck };
};
