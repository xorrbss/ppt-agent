/** Owner-scoped review data for screenshot-preserving A4 reconstruction. */
import { getHeader } from "../header";
import { ApiResponseHandler } from "../api-error-handler";
import { getApiUrl } from "@/utils/api";
import { pageUnresolvedIssues, visionReaderUnavailable } from "./raster-unresolved";

const ROOT = "/api/v1/ppt/source-content/raster/analysis";

export type RasterPageMode = "reconstruct" | "full_page_preserve" | "redesign";

/** Every source-content analysis mode the backend `analysis_mode` field accepts. */
export type RasterAnalysisMode = "auto_preserve" | "reconstruct" | "auto_redesign";

export type RasterAnalysisWire = {
  contract_version: "raster-reconstruction-analysis/v1";
  analysis_id: string;
  revision: number;
  digest: string;
  source: { source_sha256: string; page_count: number; canvas_width_emu: number; canvas_height_emu: number };
  target_canvas: { format: "A4" | "SVG"; document_canvas?: import("@/lib/document-canvas").DocumentCanvas; aspect_ratio?: import("../../../upload/type").PresentationConfig["aspectRatio"]; svg_canvas?: { width_mm: number | string; height_mm: number | string }; orientation: "landscape" | "portrait"; width_units: number; height_units: number; emu_per_unit: number };
  pages: Array<{
    page_number: number;
    render_mode: RasterPageMode;
    full_page_preserve_acknowledgements: Array<"image_only" | "legibility">;
    recommended_render_mode?: RasterPageMode | null;
    recommendation_reason?: string | null;
    full_page_region: (Omit<RasterRegionWire, "role"> & { role: "full_page_preserve" }) | null;
    regions: RasterRegionWire[];
    ocr_inventory: Array<{ text: string; bbox: RasterPixelBox; review_required: boolean }>;
  }>;
  unresolved: string[];
};

export type RasterRegionWire = {
  region_id: string;
  page_number: number;
  role: RasterRegionKind;
  bbox: RasterPixelBox;
  source_pixel_bbox?: [number, number, number, number] | null;
  z_order: number;
  text: string | null;
  ocr: { text: string; review_required: boolean } | null;
  crop_artifact_ref?: object | null;
  review_required: boolean;
};

export type RasterApprovalPinWire = {
  contract_version: "raster-reconstruction-approval/v1";
  operation_id: string;
  analysis_id: string;
  analysis_revision: number;
  analysis_digest: string;
  source_sha256: string;
  approved_region_ids: string[];
  full_page_preserve_pages: number[];
  automatic_preserve_pages?: number[];
  automatic_redesign_pages?: number[];
  approval_digest: string;
};

export type RasterOperationWire = {
  analysis: RasterAnalysisWire;
  operation_id: string;
  operation_version: number;
  status: "awaiting_approval" | "succeeded";
  reference_snapshot_id?: string | null;
  can_auto_migrate?: boolean;
  can_approve: boolean;
  review_blockers: string[];
  /** Non-blocking suggestion count (advisory: entries); never gates approval. */
  advisory_count?: number;
  approval_pin: RasterApprovalPinWire | null;
};

/** Set by the analysis operation handler while work is in flight (elapsed-time text). */
export type RasterAnalysisProgress = {
  stage?: string;
  percent?: number;
  message?: string;
};

export type RasterPendingOperationWire = {
  analysis: null;
  operation_id: string;
  operation_version: number;
  status: "queued" | "running" | "failed" | "cancelled";
  progress: RasterAnalysisProgress | null;
  error_code: string | null;
  approval_pin: null;
};

export type RasterOperationResponse = RasterOperationWire | RasterPendingOperationWire;

export type RasterPageWire = {
  page: RasterAnalysisWire["pages"][number];
  source_canvas: RasterAnalysisWire["source"];
  source_pixel_size?: { width: number; height: number };
  preview_url: string;
};

export class RasterSourceApi {
  static async start(request: { reference_snapshot_id: string; presentation_id: string; orientation?: "landscape" | "portrait"; aspect_ratio?: import("../../../upload/type").PresentationConfig["aspectRatio"]; svg_canvas?: import("../../../upload/utils/svgCanvasSelection").SvgCanvasSelection; analysis_mode: RasterAnalysisMode; style_id: string }): Promise<RasterOperationResponse> {
    return RasterSourceApi.request<RasterOperationResponse>(ROOT, "POST", request);
  }

  static async get(operationId: string): Promise<RasterOperationResponse> {
    return RasterSourceApi.request<RasterOperationResponse>(`${ROOT}/${encodeURIComponent(operationId)}`, "GET");
  }

  static async page(operationId: string, pageNumber: number): Promise<RasterPageWire> {
    return RasterSourceApi.request<RasterPageWire>(`${ROOT}/${encodeURIComponent(operationId)}/pages/${pageNumber}`, "GET");
  }

  static async correct(operationId: string, request: {
    expected_operation_version: number;
    expected_revision: number;
    analysis_digest: string;
    region_edits: Array<{ region_id: string; role?: RasterRegionKind; text?: string; source_pixel_bbox?: [number, number, number, number]; reviewed: boolean }>;
    page_edits: Array<{ page_number: number; render_mode: RasterPageMode; reviewed: boolean; acknowledge_image_only: boolean; acknowledge_legibility: boolean }>;
    review_ocr: boolean;
    resolve_unresolved: boolean;
  }): Promise<RasterOperationWire> {
    return RasterSourceApi.request<RasterOperationWire>(`${ROOT}/${encodeURIComponent(operationId)}`, "PATCH", request);
  }

  static async approve(operationId: string, request: {
    expected_operation_version: number;
    expected_revision: number;
    analysis_digest: string;
  }): Promise<RasterOperationWire> {
    return RasterSourceApi.request<RasterOperationWire>(`${ROOT}/${encodeURIComponent(operationId)}/approve`, "POST", request);
  }

  static async waitForReady(
    initial: RasterOperationResponse,
    onProgress?: (status: RasterPendingOperationWire) => void,
    signal?: AbortSignal,
  ): Promise<RasterOperationWire> {
    const deadline = Date.now() + 30 * 60 * 1000;
    let current = initial;
    while (current.analysis === null) {
      if (current.status === "failed" || current.status === "cancelled") {
        throw new Error(current.error_code ?? `raster_analysis_${current.status}`);
      }
      if (Date.now() >= deadline) throw new Error("raster_analysis_timed_out");
      onProgress?.(current);
      await new Promise<void>((resolve) => window.setTimeout(resolve, 2500));
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      current = await RasterSourceApi.get(current.operation_id);
    }
    return current;
  }

  private static async request<T>(path: string, method: "GET" | "POST" | "PATCH", body?: object): Promise<T> {
    const response = await fetch(getApiUrl(path), {
      method,
      headers: getHeader(),
      ...(body ? { body: JSON.stringify(body) } : {}),
      cache: "no-store",
    });
    return await ApiResponseHandler.handleResponse(response, "Raster source review request failed") as T;
  }
}
export type RasterAnalysisPin = {
  operationId: string;
  operationVersion: number;
  analysisId: string;
  revision: number;
  analysisDigest: `sha256:${string}`;
};

export type RasterRegionKind =
  | "title"
  | "body"
  | "screenshot"
  | "table"
  | "logo"
  | "decoration"
  | "unknown";

export type RasterPixelBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type RasterReviewIssue = {
  code: string;
  message: string;
  regionId?: string;
  severity: "blocking" | "warning";
};

export type RasterReviewRegion = {
  regionId: string;
  kind: RasterRegionKind;
  sourceBox: RasterPixelBox;
  sourcePixelBox: RasterPixelBox | null;
  cropPreviewUrl: string | null;
  candidateText: string | null;
  confirmedText: string | null;
  issues: RasterReviewIssue[];
  reviewState: "verified" | "needs_review" | "unmapped";
};

export type RasterReviewPageSummary = {
  pageIndex: number;
  renderMode: RasterPageMode;
  recommendedRenderMode: RasterPageMode | null;
  status: "analyzing" | "needs_review" | "ready" | "layout_review" | "verified";
  issueCount: number;
  blockingIssueCount: number;
};

export type RasterReviewPageDetail = {
  pageIndex: number;
  renderMode: RasterPageMode;
  fullPageAcknowledgements: Array<"image_only" | "legibility">;
  recommendedRenderMode: RasterPageMode | null;
  recommendationReason: string | null;
  fullPageRegion: { regionId: string; cropPreviewUrl: string | null; reviewState: "verified" | "needs_review" } | null;
  sourceWidthUnits: number;
  sourceHeightUnits: number;
  sourcePixelSize: { width: number; height: number } | null;
  sourcePreviewUrl: string;
  regions: RasterReviewRegion[];
  ocrInventory: Array<{ text: string; sourceBox: RasterPixelBox; reviewRequired: boolean }>;
  issues: RasterReviewIssue[];
};

export type RasterAnalysisReview = {
  pin: RasterAnalysisPin;
  status: "analyzing" | "awaiting_review" | "ready" | "approved" | "generating" | "complete";
  sourcePageCount: number;
  targetCanvas: "a4-landscape" | "a4-portrait" | "svg";
  targetWidth?: number;
  targetHeight?: number;
  pages: RasterReviewPageSummary[];
  canApprove: boolean;
  blockingIssueCount: number;
  /** Non-blocking suggestion count; 0 when absent. Never affects canApprove. */
  advisoryCount: number;
  /** No vision reader was configured: every page keeps its text as pixels (deck-level notice). */
  visionReaderUnavailable: boolean;
};

export type RasterReviewRegionEdit = {
  regionId: string;
  kind?: RasterRegionKind;
  sourcePixelBox?: RasterPixelBox;
  confirmedText?: string;
  reviewed: boolean;
};

export type RasterReviewSaveRequest = RasterAnalysisPin & {
  pageIndex: number;
  edits: RasterReviewRegionEdit[];
};

export type RasterPageModeRequest = RasterAnalysisPin & {
  pageIndex: number;
  renderMode: RasterPageMode;
  reviewed: boolean;
  acknowledgeImageOnly: boolean;
  acknowledgeLegibility: boolean;
};

export type RasterReviewApprovalRequest = RasterAnalysisPin;

export type RasterReviewApprovalPin = RasterAnalysisPin & {
  approvalDigest: `sha256:${string}`;
};

/** Review images must be fetched through the application's authenticated origin. */
export function rasterReviewImageUrl(path: string | null): string | null {
  if (!path || !path.startsWith("/api/v1/ppt/") || path.startsWith("//")) {
    return null;
  }
  return path;
}

const digestForUi = (digest: string): `sha256:${string}` => `sha256:${digest}`;
const digestForApi = (digest: `sha256:${string}`): string => digest.slice(7);

export function rasterReviewPin(value: RasterOperationWire): RasterAnalysisPin {
  return {
    operationId: value.operation_id,
    operationVersion: value.operation_version,
    analysisId: value.analysis.analysis_id,
    revision: value.analysis.revision,
    analysisDigest: digestForUi(value.analysis.digest),
  };
}

export function rasterCasBody(pin: RasterAnalysisPin) {
  return {
    expected_operation_version: pin.operationVersion,
    expected_revision: pin.revision,
    analysis_digest: digestForApi(pin.analysisDigest),
  };
}

export function rasterReviewSummary(value: RasterOperationWire): RasterAnalysisReview {
  const pageBlocks = value.analysis.pages.map((page) => page.render_mode === "full_page_preserve"
    ? page.full_page_region?.review_required === false ? 0 : 1
    : page.regions.filter((region) => region.review_required || region.role === "unknown").length +
      page.ocr_inventory.filter((item) => item.review_required).length);
  const reconstructedPageCount = value.analysis.pages.filter((page) => page.render_mode !== "full_page_preserve").length;
  const inferredBlockingIssueCount = pageBlocks.reduce((sum, count) => sum + count, 0) +
    (reconstructedPageCount > 0 ? value.analysis.unresolved.length : 0);
  const blockingIssueCount = value.review_blockers?.length ?? inferredBlockingIssueCount;
  const status = value.approval_pin ? "approved" : blockingIssueCount === 0 ? "ready" : "awaiting_review";
  return {
    pin: rasterReviewPin(value),
    status,
    sourcePageCount: value.analysis.source.page_count,
    targetWidth: value.analysis.target_canvas.width_units,
    targetHeight: value.analysis.target_canvas.height_units,
    targetCanvas: value.analysis.target_canvas.format === "SVG" ? "svg" : value.analysis.target_canvas.orientation === "portrait" ? "a4-portrait" : "a4-landscape",
    pages: value.analysis.pages.map((page, index) => {
      const count = pageBlocks[index];
      return {
        pageIndex: page.page_number - 1,
        renderMode: page.render_mode,
        recommendedRenderMode: page.recommended_render_mode ?? null,
        status: count > 0 ? "needs_review" : "ready",
        issueCount: count,
        blockingIssueCount: count,
      };
    }),
    canApprove: value.can_approve === true && !value.approval_pin,
    blockingIssueCount,
    advisoryCount: value.advisory_count ?? 0,
    visionReaderUnavailable: visionReaderUnavailable(value.analysis.unresolved),
  };
}

export function rasterPageDetail(value: RasterOperationWire, detail: RasterPageWire): RasterReviewPageDetail {
  const pageNumber = detail.page.page_number;
  const source = value.analysis.source;
  // Only this page's own entries (and deck-level blocking ones), not every page's.
  const issues = detail.page.render_mode === "full_page_preserve"
    ? []
    : pageUnresolvedIssues(value.analysis.unresolved, pageNumber);
  return {
    pageIndex: pageNumber - 1,
    renderMode: detail.page.render_mode,
    fullPageAcknowledgements: detail.page.full_page_preserve_acknowledgements ?? [],
    recommendedRenderMode: detail.page.recommended_render_mode ?? null,
    recommendationReason: detail.page.recommendation_reason ?? null,
    fullPageRegion: detail.page.full_page_region ? {
      regionId: detail.page.full_page_region.region_id,
      cropPreviewUrl: `${ROOT}/${encodeURIComponent(value.operation_id)}/regions/${encodeURIComponent(detail.page.full_page_region.region_id)}/crop?revision=${value.analysis.revision}`,
      reviewState: detail.page.full_page_region.review_required ? "needs_review" : "verified",
    } : null,
    sourceWidthUnits: source.canvas_width_emu,
    sourceHeightUnits: source.canvas_height_emu,
    sourcePixelSize: detail.source_pixel_size ?? null,
    sourcePreviewUrl: detail.preview_url,
    regions: detail.page.regions.map((region) => ({
      regionId: region.region_id,
      kind: region.role,
      sourceBox: region.bbox,
      sourcePixelBox: region.source_pixel_bbox ? {
        x: region.source_pixel_bbox[0],
        y: region.source_pixel_bbox[1],
        width: region.source_pixel_bbox[2],
        height: region.source_pixel_bbox[3],
      } : null,
      cropPreviewUrl: region.crop_artifact_ref
        ? `${ROOT}/${encodeURIComponent(value.operation_id)}/regions/${encodeURIComponent(region.region_id)}/crop?revision=${value.analysis.revision}`
        : null,
      candidateText: region.ocr?.text ?? null,
      confirmedText: region.text,
      issues: region.review_required ? [{
        code: "region_review_required",
        message: "이 영역을 원본 이미지와 대조해 확인해 주세요.",
        regionId: region.region_id,
        severity: "blocking" as const,
      }] : [],
      reviewState: region.role === "unknown" ? "unmapped" : region.review_required ? "needs_review" : "verified",
    })),
    ocrInventory: detail.page.ocr_inventory.map((item) => ({
      text: item.text,
      sourceBox: item.bbox,
      reviewRequired: item.review_required,
    })),
    issues,
  };
}

export function rasterApprovalForUi(value: RasterOperationWire): RasterReviewApprovalPin {
  if (!value.approval_pin) throw new Error("raster_approval_missing");
  return {
    ...rasterReviewPin(value),
    approvalDigest: digestForUi(value.approval_pin.approval_digest),
  };
}
