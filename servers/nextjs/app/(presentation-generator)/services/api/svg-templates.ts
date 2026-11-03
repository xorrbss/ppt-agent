import { getApiUrl } from "@/utils/api";
import { getHeader } from "./header";
import { ApiResponseHandler } from "./api-error-handler";
import { documentCanvasRequestForConfig, type PresentationConfig, type DocumentCanvasRequest } from "../../upload/type";

export type SvgTemplateMode = "free-design" | "template-redesign";
export interface SvgTemplatePin { templateId: string; revision: number; digest: string }
export interface SvgTemplateIssue { code?: string; reason_code?: string; message?: string; severity?: "blocked" | "review-required" }
export interface SvgTemplateReviewEvidence {
  status?: string; reason?: string;
  renderer?: { kind?: string; version?: string; approximate?: boolean };
  frames?: Array<{ frame_id: string; status?: string; reason?: string; frame_digest?: string; source_image_digest?: string; frame_image_digest?: string; font_manifest_digest?: string }>;
}
export interface SvgTemplateCard {
  templateId: string; name: string; provider: "builtin" | "user";
  kind: "style" | "framed"; supportedModes: SvgTemplateMode[]; defaultMode: SvgTemplateMode;
  registrationState: "analyzing" | "review-required" | "ready" | "failed" | "archived";
  fontState: string; sourceCanvas: import("@/lib/document-canvas").DocumentCanvas | null;
  pin: SvgTemplatePin | null; previewUrl?: string; styleId?: string;
  issues: Array<string | SvgTemplateIssue>; operationId?: string;
}
export interface SvgTemplateOperation { state: string; progress?: unknown; result?: unknown; errorCode?: string }
export interface SvgTemplateCapability {
  state: "available" | "preparation-required" | "blocked";
  reasonCode: string | null; nextAction: string | null; contractDigest?: string;
}
/** Fixed decoration the converter cannot draw, kept as an image cut from a source render. */
export interface SvgTemplateRasterPreservation { renderer: string; renderer_version?: string; approximate: boolean; reasons: string[]; shape_refs: string[] }
/** An explicit replacement for a source font the server cannot use (wire shape). */
export interface SvgTemplateFontSubstitution { sourceFamily: string; targetFamily: string; targetWeight: number }
export interface SvgTemplateUnavailableFont { source_family: string; source_weight: number; suggested_family: string; suggested_weight: number }
export interface SvgTemplateFrameReview {
  frameId: string; fixedShapeIds: string[]; bodyRegion: { x: number; y: number; width: number; height: number }; role: "cover" | "section" | "body";
}
export interface SvgTemplateFrame {
  issues?: SvgTemplateIssue[];
  frame_id: string; source_page?: number; role?: "cover" | "section" | "body";
  elements?: Array<{ element_id: string; source_part: string; source_shape_id: string; kind: string; layer: string; bounds: { x: number; y: number; width: number; height: number }; raster_preservation?: SvgTemplateRasterPreservation }>;
  source_canvas?: import("@/lib/document-canvas").DocumentCanvas;
  preview_url?: string; body_region?: { x: number; y: number; width: number; height: number };
}
export interface SvgTemplateDraft {
  template: SvgTemplateCard; operationId: string;
  state?: string; errorCode?: string;
  source_previews?: Record<string, string>;
  reviewEvidence?: SvgTemplateReviewEvidence;
  review_evidence?: SvgTemplateReviewEvidence;
  package: { digest?: string; frames: SvgTemplateFrame[]; body_evidence?: Array<{ source_page: number; body_shape_ids: string[]; body_bounds?: Array<{ x: number; y: number; width: number; height: number }>; body_kinds?: string[] }>; issues?: Array<string | SvgTemplateIssue>; font_manifest?: unknown;
    unavailable_fonts?: SvgTemplateUnavailableFont[]; font_substitutions?: Array<{ source_family: string; target_family: string; target_weight: number }> } | null;
}
const base = "/api/v1/ppt/svg-templates/v1";
async function request<T>(path: string, body?: unknown, method?: "PATCH"): Promise<T> {
  const response = await fetch(getApiUrl(base + path), {
    method: method ?? (body === undefined ? "GET" : "POST"), headers: getHeader(),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), cache: "no-store",
  });
  return ApiResponseHandler.handleResponse(response, "템플릿 요청을 처리하지 못했습니다.") as Promise<T>;
}
export const SvgTemplatesApi = {
  availability: () => request<{ enabled: boolean }>("/availability"),
  list: () => request<{ templates: SvgTemplateCard[] }>(""),
  register: (body: { name: string; referenceSnapshotId: string; registrationMode: "framed" | "style-only"; conversationId?: string }) => request<{ template: SvgTemplateCard; operationId: string }>("/register", body),
  version: (pin: SvgTemplatePin) => request<{ template: SvgTemplateCard }>(`/${encodeURIComponent(pin.templateId)}/versions/${pin.revision}`),
  draft: (id: string) => request<SvgTemplateDraft>(`/${encodeURIComponent(id)}/draft`),
  review: (id: string, body: { operationId: string; expectedHeadRevision: number; frameReviews: SvgTemplateFrameReview[]; representativeFrameId?: string; fontSubstitutions?: SvgTemplateFontSubstitution[] }) => request<{ operationId: string; registrationState: string }>(`/${encodeURIComponent(id)}/draft`, body, "PATCH"),
  publish: (id: string, body: { operationId: string; expectedHeadRevision: number; selectedFrameIds: string[]; confirmReview: boolean; styleOnly?: boolean }) => request<{ template: SvgTemplateCard }>(`/${encodeURIComponent(id)}/publish`, body),
  prepare: (body: { templatePin: SvgTemplatePin; canvas: DocumentCanvasRequest }) => request<{ operationId: string; state: string }>("/adaptations", body),
  operation: (id: string) => request<SvgTemplateOperation>(`/operations/${encodeURIComponent(id)}`),
  capability: (body: { templatePin: SvgTemplatePin; mode: SvgTemplateMode; canvas: DocumentCanvasRequest; sourceMode?: string; hasCompanyMaster?: boolean; hasCommonTitleDesign?: boolean }) => request<SvgTemplateCapability>("/capability", body),
};
export function svgTemplateCanvas(config: PresentationConfig): DocumentCanvasRequest {
  return documentCanvasRequestForConfig(config);
}
