import { getHeaderForFormData } from "../header";
import { ApiResponseHandler } from "../api-error-handler";
import { getApiUrl } from "@/utils/api";
import { fetchWithNetworkRetry } from "@/utils/fetchWithNetworkRetry";

export const SOURCE_CONTENT_RESTYLE_CONTRACT_VERSION =
  "source-content-analysis-request/v1" as const;

export type SourceContentManifestPin = {
  manifestId: string;
  digest: `sha256:${string}`;
};

export type SourceContentAnalysisSummary = {
  sourceType: "pptx";
  sourceUnits: number;
  textBlocks: number;
  tables: number;
  charts: number;
  uniqueImages: number;
  imageOccurrences: number;
  rasterFallbackBlocks: number;
};

export type SourceContentAnalysisResponseV1 = {
  contractVersion: "source-content-analysis/v1";
  status: "ready";
  capabilities: {
    pptx: true;
    pdf: false;
    docx: false;
    ppt: false;
  };
  manifestPin: SourceContentManifestPin;
  referenceSnapshotBinding: {
    referenceSnapshotId: string;
    providerId: string;
    sourceRevision: string;
  };
  summary: SourceContentAnalysisSummary;
  objectInventory: {
    discoveredObjectCount: number;
    classifiedObjectCount: number;
    contentBearingObjectCount: number;
    unsupportedContentObjectIds: string[];
    unclassifiedObjectIds: string[];
    externalRelationshipCount: number;
  };
};

export type SourceContentPolicy = {
  text: "verbatim";
  preserveAllTableCells: true;
  preserveAllChartValues: true;
  preserveAllImageOccurrences: true;
  preserveSourceOrder: true;
  preserveSourceSlideCount: true;
  allowSlideExpansion: false;
  allowSummarization: false;
  overflowPolicy: "same_page_recompose_or_fail";
};

export const SOURCE_CONTENT_POLICY: SourceContentPolicy = {
  text: "verbatim",
  preserveAllTableCells: true,
  preserveAllChartValues: true,
  preserveAllImageOccurrences: true,
  preserveSourceOrder: true,
  preserveSourceSlideCount: true,
  allowSlideExpansion: false,
  allowSummarization: false,
  overflowPolicy: "same_page_recompose_or_fail",
};

export type SourceContentSelectionError =
  | "source_content_requires_exactly_one_file"
  | "source_content_rejects_file_type";

export const validateSourceContentFiles = (
  files: readonly File[],
): SourceContentSelectionError | null => {
  if (files.length !== 1) return "source_content_requires_exactly_one_file";
  return /\.pptx$/i.test(files[0]?.name ?? "")
    ? null
    : "source_content_rejects_file_type";
};

export const sourceContentSelectionErrorMessage = (
  error: SourceContentSelectionError,
) => error === "source_content_requires_exactly_one_file"
  ? "원본 콘텐츠 유지는 PPTX 파일 한 개만 허용합니다."
  : "원본 콘텐츠 유지는 현재 PPTX만 지원합니다. PDF, DOCX, legacy PPT는 아직 사용할 수 없습니다.";

export class SourceContentApiV1 {
  static async analyze({
    referenceSnapshotId,
    idempotencyKey,
  }: {
    referenceSnapshotId: string;
    idempotencyKey: string;
  }): Promise<SourceContentAnalysisResponseV1> {
    const formData = new FormData();
    formData.append("referenceSnapshotId", referenceSnapshotId);
    formData.append("contractVersion", SOURCE_CONTENT_RESTYLE_CONTRACT_VERSION);
    const response = await fetchWithNetworkRetry(
      getApiUrl("/api/v1/ppt/source-content/v1/analyze"),
      {
        method: "POST",
        headers: {
          ...getHeaderForFormData(),
          "Idempotency-Key": idempotencyKey,
        },
        body: formData,
        cache: "no-cache",
      },
    );
    return (await ApiResponseHandler.handleResponse(
      response,
      "Failed to analyze source content",
    )) as SourceContentAnalysisResponseV1;
  }
}
