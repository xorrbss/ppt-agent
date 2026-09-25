"use client";
/**
 * Hands a fully-approved raster review off to the SVG generation flow: it
 * verifies the approval pin against the requested analysis mode, stages the
 * durable generation task, and navigates to the outline screen. Extracted
 * from useUploadPageState.ts (file-size ratchet split); behaviour unchanged.
 */
import { useCallback } from "react";
import type { useRouter } from "next/navigation";

import type { RasterOperationWire } from "../../../services/api/source-content/raster-contract";
import {
  createDurableGenerationTask,
  saveDurableGenerationTask,
} from "../../../outline/hooks/durableGenerationTask";
import {
  clearRasterOriginalPreserveResult,
  rememberRasterOriginalPreserveResult,
} from "../../../services/api/source-content/raster-result-hint";
import { forgetRasterReview, type RasterReviewRecovery } from "./raster-review-recovery";

export const useRasterHandoff = (
  router: ReturnType<typeof useRouter>,
  setRasterRecovery: (value: RasterReviewRecovery | null) => void,
) =>
  useCallback(
    (operation: RasterOperationWire, recovery: RasterReviewRecovery) => {
      const pin = operation.approval_pin;
      const referenceSnapshotId = recovery.referenceSnapshotId ?? operation.reference_snapshot_id;
      if (!pin || pin.operation_id !== operation.operation_id ||
          pin.analysis_id !== operation.analysis.analysis_id ||
          pin.analysis_revision !== operation.analysis.revision ||
          pin.analysis_digest !== operation.analysis.digest ||
          pin.source_sha256 !== operation.analysis.source.source_sha256 || !referenceSnapshotId) {
        throw new Error("raster_approval_pin_invalid");
      }
      if (recovery.analysisMode === "auto_preserve") {
        const pages = operation.analysis.pages;
        const expectedPageNumbers = pages.map((page) => page.page_number).sort((a, b) => a - b);
        const approvedPageNumbers = [...pin.full_page_preserve_pages].sort((a, b) => a - b);
        const automaticPageNumbers = [...(pin.automatic_preserve_pages ?? [])].sort((a, b) => a - b);
        const expectedRegionIds = pages.map((page) => page.full_page_region?.region_id ?? "").sort();
        const approvedRegionIds = [...pin.approved_region_ids].sort();
        if (pages.length !== operation.analysis.source.page_count ||
            pages.some((page) => page.render_mode !== "full_page_preserve" || !page.full_page_region) ||
            approvedPageNumbers.length !== pages.length || automaticPageNumbers.length !== pages.length ||
            approvedRegionIds.length !== pages.length ||
            expectedPageNumbers.some((number, index) => number !== index + 1 ||
              number !== approvedPageNumbers[index] || number !== automaticPageNumbers[index]) ||
            expectedRegionIds.some((id, index) => id !== approvedRegionIds[index])) {
          throw new Error("raster_auto_preserve_incomplete");
        }
      } else if (recovery.analysisMode === "auto_redesign") {
        // Only reachable once every page cleared analysis with zero
        // low-confidence regions; the server never issues this pin otherwise.
        const pages = operation.analysis.pages;
        const expectedPageNumbers = pages.map((page) => page.page_number).sort((a, b) => a - b);
        const automaticPageNumbers = [...(pin.automatic_redesign_pages ?? [])].sort((a, b) => a - b);
        if (pages.length !== operation.analysis.source.page_count ||
            pages.some((page) => page.render_mode !== "redesign") ||
            automaticPageNumbers.length !== pages.length ||
            expectedPageNumbers.some((number, index) => number !== index + 1 || number !== automaticPageNumbers[index])) {
          throw new Error("raster_auto_redesign_incomplete");
        }
      }
      saveDurableGenerationTask({
        ...createDurableGenerationTask({
          mode: "svg",
          sourcePresentationId: recovery.presentationId,
          timeoutMs: 30 * 60 * 1000,
          progressDurationSeconds: Math.min(600, operation.analysis.source.page_count * 25),
        }),
        authoredRequest: {
          strategy: "svg",
          language: null,
          instructions: recovery.instructions,
          visionQa: false,
          authoredStyle: recovery.authoredStyle,
          pageSize: "A4",
          orientation: recovery.orientation,
          sourceMode: "raster_reconstruction",
          rasterApprovalPin: pin,
          referenceSnapshotIds: [referenceSnapshotId],
        },
      });
      if (recovery.analysisMode === "auto_preserve") {
        rememberRasterOriginalPreserveResult(recovery.presentationId);
      } else {
        clearRasterOriginalPreserveResult(recovery.presentationId);
      }
      forgetRasterReview();
      setRasterRecovery(null);
      router.push(`/outline?auto=1&id=${encodeURIComponent(recovery.presentationId)}`);
    },
    [router, setRasterRecovery],
  );
