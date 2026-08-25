import { getHeaderForFormData } from "../header";
import { ApiResponseHandler } from "../api-error-handler";
import { getApiUrl } from "@/utils/api";
import { fetchWithNetworkRetry } from "@/utils/fetchWithNetworkRetry";

/**
 * This is intentionally separate from the legacy Presentation codec.  In
 * particular, it must never be mapped to Presentation.generation_mode, whose
 * historic `standard` value has different semantics.
 */
export const SOURCE_LOCKED_ADMISSION_CONTRACT_VERSION =
  "source-locked-admission/v1" as const;

export type SourceLockedGenerationMode = "source_locked";
export type AdaptiveGenerationMode = "adaptive";
export type SourceLockedPageCountPolicy = "source";
export type AdaptivePageCountPolicy = "user_or_auto";

export type SourceLockedSelectionError =
  | "source_locked_requires_exactly_one_file"
  | "source_locked_rejects_pptm"
  | "source_locked_rejects_file_type";

export type SourceLockedAnalysisSummary = {
  source_format: "ppt" | "pptx" | "pdf";
  source_page_count: number;
  output_slide_count: number;
  media_asset_count: number;
  image_occurrence_count: number;
  inherited_image_occurrence_count: number;
};

export type SourceLockedAnalysisResponseV1 = {
  admission_contract: Record<string, unknown>;
  source_snapshot: Record<string, unknown>;
  manifest: Record<string, unknown>;
  analysis_seal: Record<string, unknown>;
  progress_stages: Array<{ stage: string; status: string }>;
  fallback_allowed: false;
  summary: {
    source_type: "ppt" | "pptx" | "pdf";
    page_count: number;
    slide_count: number;
    media_asset_count: number;
    image_occurrence_count: number;
    inherited_occurrence_count: number;
  };
};

const sourceLockedExtension = (file: File) =>
  file.name.trim().toLowerCase().split(".").pop() ?? "";

export function validateSourceLockedFiles(
  files: readonly File[],
): SourceLockedSelectionError | null {
  if (files.length !== 1) return "source_locked_requires_exactly_one_file";
  const extension = sourceLockedExtension(files[0]);
  if (extension === "pptm") return "source_locked_rejects_pptm";
  return ["ppt", "pptx", "pdf"].includes(extension)
    ? null
    : "source_locked_rejects_file_type";
}

export function sourceLockedSelectionErrorMessage(
  error: SourceLockedSelectionError,
): string {
  switch (error) {
    case "source_locked_requires_exactly_one_file":
      return "원본 문서 그대로 생성은 .ppt, .pptx 또는 .pdf 파일 한 개만 허용합니다.";
    case "source_locked_rejects_pptm":
      return "매크로가 포함된 .pptm 파일은 원본 문서 그대로 생성에 사용할 수 없습니다.";
    default:
      return "원본 문서 그대로 생성은 .ppt, .pptx 또는 .pdf 파일만 허용합니다.";
  }
}

export function sourceLockedSummaryFromResponse(
  response: SourceLockedAnalysisResponseV1,
): SourceLockedAnalysisSummary | null {
  const summary = response.summary;
  const pageCount = Number(summary.page_count);
  const slideCount = Number(summary.slide_count);
  const sourceFormat = String(summary.source_type).toLowerCase();
  if (
    !Number.isInteger(pageCount) ||
    pageCount < 1 ||
    !Number.isInteger(slideCount) ||
    slideCount !== pageCount ||
    !["ppt", "pptx", "pdf"].includes(sourceFormat)
  ) {
    return null;
  }
  const count = (value: unknown) => {
    const numericValue = Number(value ?? 0);
    return Number.isInteger(numericValue) && numericValue >= 0 ? numericValue : 0;
  };
  return {
    source_format: sourceFormat as SourceLockedAnalysisSummary["source_format"],
    source_page_count: pageCount,
    output_slide_count: slideCount,
    media_asset_count: count(summary.media_asset_count),
    image_occurrence_count: count(summary.image_occurrence_count),
    inherited_image_occurrence_count: count(summary.inherited_occurrence_count),
  };
}

async function sha256(bytes: BufferSource): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
}

/** Browser-side equivalent of the v1 admission request identity. */
export async function sourceLockedPayloadDigest(files: readonly File[]) {
  const validation = validateSourceLockedFiles(files);
  if (validation) throw new Error(sourceLockedSelectionErrorMessage(validation));
  const file = files[0];
  const sourceHex = await sha256(await file.arrayBuffer());
  const admissionContract = {
    contract_version: SOURCE_LOCKED_ADMISSION_CONTRACT_VERSION,
    generation_mode: "source_locked",
    page_count_policy: "source",
    preserve_all_image_occurrences: true,
    source_count: 1,
    source_filename: file.name,
    source_media_type: sourceLockedExtension(file),
  };
  const canonical = JSON.stringify({
    admission_contract: admissionContract,
    contract_version: "source-locked-idempotency-payload/v1",
    source_snapshot_sha256: `sha256:${sourceHex}`,
  });
  return `sha256:${await sha256(new TextEncoder().encode(canonical))}`;
}

export class SourceLockedGenerationApiV1 {
  static async analyze(
    files: readonly File[],
    idempotencyKey: string,
  ): Promise<SourceLockedAnalysisResponseV1> {
    const error = validateSourceLockedFiles(files);
    if (error) throw new Error(sourceLockedSelectionErrorMessage(error));

    const formData = new FormData();
    for (const file of files) formData.append("files", file);
    formData.append("generation_mode", "source_locked");
    formData.append("page_count_policy", "source");
    formData.append("preserve_all_image_occurrences", "true");
    const payloadDigest = await sourceLockedPayloadDigest(files);
    const response = await fetchWithNetworkRetry(
      getApiUrl("/api/v1/ppt/source-locked/v1/analyze"),
      {
        method: "POST",
        headers: {
          ...getHeaderForFormData(),
          "Idempotency-Key": idempotencyKey,
          "X-Idempotency-Payload-Digest": payloadDigest,
        },
        body: formData,
        cache: "no-cache",
      },
    );
    return (await ApiResponseHandler.handleResponse(
      response,
      "Failed to analyze source-locked document",
    )) as SourceLockedAnalysisResponseV1;
  }
}
