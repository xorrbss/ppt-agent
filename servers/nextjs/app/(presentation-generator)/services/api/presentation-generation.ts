import { getHeader, getHeaderForFormData } from "./header";
import {
  IconSearch,
  ImageGenerate,
  PreviousGeneratedImagesResponse,
} from "./params";
import { apiErrorFromPayload, ApiResponseHandler } from "./api-error-handler";
import { getApiUrl, resolveBackendAssetUrl } from "@/utils/api";
import { fetchWithNetworkRetry } from "@/utils/fetchWithNetworkRetry";
import {
  canonicalUploadAdmissionKey,
  cleanupCanonicalDocumentAdmission,
  forgetCanonicalAdmission,
  rememberCanonicalAdmission,
} from "./canonical-document-admission";
import type {
  GenerationPreferences,
  PresentationThemePayload,
} from "./presentation-generation-options";

export type { GenerationPreferences, PresentationThemePayload } from "./presentation-generation-options";

export type BlankPresentationResponse = {
  id: string;
  version: number;
  title: string | null;
  n_slides: number;
  language: string;
  fonts?: Record<string, string> | null;
  slides: Array<Record<string, unknown>>;
};

export type GenerationStage =
  | "author"
  | "illustrate"
  | "render"
  | "persist"
  | "finalize"
  | "template_v2_layout_selection"
  | "template_v2_slide_generation"
  | "template_v2_pptx_validation"
  | "template_v2_saving"
  | "template_v2_exporting"
  | "template_v2_publishing"
  | "completed";

export type GenerationProgress = {
  completed_slides: number;
  total_slides: number;
  percent?: number;
  current_slide?: number | null;
  current_stage?: GenerationStage | null;
  stage_counts?: Partial<Record<GenerationStage, number>>;
  finalizing?: boolean;
};

export type GenerationFailure = {
  code?: string;
  message?: string;
  stage?: GenerationStage;
  slide_number?: number;
  exception_type?: string;
  fingerprint?: string;
  retryable?: boolean;
  request_id?: string | null;
  status_code?: number;
  detail?: unknown;
};

export type GenerationStatusResponse = {
  id?: string;
  status: string;
  message?: string | null;
  error?: GenerationFailure | Record<string, unknown> | null;
  // Free-form task payload. Left as `any` values on purpose: consumers read
  // fields such as `data.edit_path` directly, and narrowing them here would
  // require edits in `outline/hooks/usePresentationGeneration.ts`.
  data?: Record<string, any> | null;
  request_id?: string;
  progress?: GenerationProgress | null;
  failure?: GenerationFailure | null;
};

export type StartGenerationResponse = {
  id?: string;
  status?: string;
  operation_id?: string;
  request_id?: string;
};

export type DocumentDecomposeProgress = {
  completed_files: number;
  total_files: number;
  current_file?: string | null;
};

export type DocumentDecomposeFailure = {
  code?: string;
  message?: string;
  filename?: string;
  retryable?: boolean;
};

export type DecomposedDocument = {
  name: string;
  asset_id?: string;
  content_sha256?: string;
  /** Legacy synchronous decomposition facade only. */
  file_path?: string;
  [key: string]: unknown;
};

export type CanonicalDocumentAsset = {
  asset_id: string;
  kind: "document";
  state: string;
  media_type?: string | null;
  size_bytes: number;
  sha256: string;
  version: number;
  expires_at?: string | null;
};

export type DocumentUploadAdmission = {
  assets: CanonicalDocumentAsset[];
  assetIds: string[];
  referenceSnapshotId: string;
};

type DecomposeDocumentSource =
  | { referenceSnapshotId: string; documentCount: number }
  | string[];

export type DocumentDecomposeJob = {
  job_id: string;
  status:
    | "queued"
    | "running"
    | "cancelling"
    | "succeeded"
    | "failed"
    | "cancelled";
  progress: DocumentDecomposeProgress;
  result: DecomposedDocument[] | null;
  error: DocumentDecomposeFailure | null;
  created_at?: string;
  updated_at?: string;
  expires_at?: string;
};

export type DecomposeDocumentsOptions = {
  signal?: AbortSignal;
  onProgress?: (job: DocumentDecomposeJob) => void;
  pollIntervalMs?: number;
  idempotencyKey?: string;
};

const DOCUMENT_DECOMPOSE_POLL_INTERVAL_MS = 1_000;

function abortError() {
  return new DOMException("Document processing was cancelled", "AbortError");
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function waitForDecomposePoll(delayMs: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const cleanup = () => signal?.removeEventListener("abort", onAbort);
    const timeout = window.setTimeout(() => {
      cleanup();
      resolve();
    }, delayMs);
    const onAbort = () => {
      window.clearTimeout(timeout);
      cleanup();
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

const generationPreferencesPayload = (body: GenerationPreferences) => ({ language: body.language ?? null, ...(body.tone ? { tone: body.tone } : {}), ...(body.verbosity ? { verbosity: body.verbosity } : {}), ...(body.instructions !== undefined ? { instructions: body.instructions } : {}), ...(body.include_table_of_contents !== undefined ? { include_table_of_contents: body.include_table_of_contents } : {}), ...(body.include_title_slide !== undefined ? { include_title_slide: body.include_title_slide } : {}), ...(body.web_search !== undefined ? { web_search: body.web_search } : {}) });

export class PresentationGenerationApi {
  static async uploadDoc(
    documents: File[],
    admissionScope: string,
  ): Promise<DocumentUploadAdmission> {
    // Reference snapshots are single-workspace resources. Scope idempotency to
    // the destination presentation so another deck cannot replay assets that
    // an earlier workspace already claimed.
    const admissionKey = await canonicalUploadAdmissionKey(
      documents,
      admissionScope,
    );
    const admittedAssets: CanonicalDocumentAsset[] = [];
    try {
      const settled = await Promise.allSettled(
        documents.map(async (document, index) => {
          const formData = new FormData();
          formData.append("file", document);
          const response = await fetchWithNetworkRetry(
            getApiUrl(`/api/v1/ppt/assets/documents`),
            {
              method: "POST",
              headers: {
                ...getHeaderForFormData(),
                "Idempotency-Key": `document-upload:v2:${admissionKey}:${index}`,
              },
              body: formData,
              cache: "no-cache",
            },
          );
          const asset = (await ApiResponseHandler.handleResponse(
            response,
            "Failed to upload document",
          )) as CanonicalDocumentAsset;
          admittedAssets.push(asset);
          return asset;
        }),
      );
      const rejected = settled.find(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      if (rejected) throw rejected.reason;
      const assets = settled.map(
        (result) => (result as PromiseFulfilledResult<CanonicalDocumentAsset>).value,
      );
      const assetIds = assets.map((asset) => asset.asset_id);
      const snapshotResponse = await fetchWithNetworkRetry(
        getApiUrl(`/api/v1/ppt/references/snapshots`),
        {
          method: "POST",
          headers: {
            ...getHeader(),
            "Idempotency-Key": `document-snapshot:v2:${admissionKey}`,
          },
          body: JSON.stringify({
            provider_id: "uploaded_document",
            source: { asset_ids: assetIds },
          }),
          cache: "no-cache",
        },
      );
      const snapshot = (await ApiResponseHandler.handleResponse(
        snapshotResponse,
        "Failed to admit uploaded documents",
      )) as { snapshot_id: string };
      rememberCanonicalAdmission(snapshot.snapshot_id, assetIds);
      return {
        assets,
        assetIds,
        referenceSnapshotId: snapshot.snapshot_id,
      };
    } catch (error) {
      if (admittedAssets.length > 0) {
        await PresentationGenerationApi.cleanupDocumentAdmission({
          assetIds: admittedAssets.map((asset) => asset.asset_id),
        });
      }
      // This error is handled by the upload page and shown as a toast. Logging
      // it as an error makes Next.js display its development runtime overlay.
      console.warn("Upload error:", error);
      throw error;
    }
  }

  static async cleanupDocumentAdmission({
    referenceSnapshotId,
    assetIds,
  }: {
    referenceSnapshotId?: string;
    assetIds?: string[];
  }): Promise<void> {
    await cleanupCanonicalDocumentAdmission({ referenceSnapshotId, assetIds });
  }

  static async decomposeDocuments(
    source: DecomposeDocumentSource,
    language?: string | null,
    options: DecomposeDocumentsOptions = {},
  ) {
    let jobId: string | undefined;
    const cancelOnAbort = () => {
      if (jobId) {
        void PresentationGenerationApi.cancelDecomposeDocuments(jobId).catch(
          (error) => console.warn("Failed to cancel document processing", error),
        );
      }
    };
    options.signal?.addEventListener("abort", cancelOnAbort, { once: true });

    try {
      const started = await PresentationGenerationApi.startDecomposeDocuments(
        source,
        language,
        options,
      );
      jobId = started.job_id;
      options.onProgress?.(started);

      let job = started;
      while (job.status === "queued" || job.status === "running" || job.status === "cancelling") {
        await waitForDecomposePoll(
          options.pollIntervalMs ?? DOCUMENT_DECOMPOSE_POLL_INTERVAL_MS,
          options.signal,
        );
        job = await PresentationGenerationApi.getDecomposeDocumentsJob(
          jobId,
          options.signal,
        );
        options.onProgress?.(job);
      }

      if (job.status === "succeeded" && Array.isArray(job.result)) {
        return job.result;
      }
      if (job.status === "cancelled") {
        throw abortError();
      }
      throw apiErrorFromPayload(
        job.error,
        "Failed to decompose documents",
        422,
        job.job_id,
      );
    } catch (error) {
      if (!Array.isArray(source)) {
        await PresentationGenerationApi.cleanupDocumentAdmission({
          referenceSnapshotId: source.referenceSnapshotId,
        });
      }
      // Navigation, an explicit cancel, and development refreshes all abort the
      // polling request intentionally. Logging that expected control flow with
      // console.error makes Next.js render a misleading runtime-error overlay.
      if (!isAbortError(error)) {
        console.error("Error in Decompose Files", error);
      }
      throw error;
    } finally {
      options.signal?.removeEventListener("abort", cancelOnAbort);
    }
  }

  static async startDecomposeDocuments(
    source: DecomposeDocumentSource,
    language?: string | null,
    options: Pick<DecomposeDocumentsOptions, "signal" | "idempotencyKey"> = {},
  ): Promise<DocumentDecomposeJob> {
    const response = await fetch("/api/decompose-documents/jobs", {
      method: "POST",
      headers: {
        ...getHeader(),
        ...(options.idempotencyKey
          ? { "Idempotency-Key": options.idempotencyKey }
          : {}),
      },
      body: JSON.stringify({
        ...(Array.isArray(source)
          ? { file_paths: source }
          : { reference_snapshot_id: source.referenceSnapshotId }),
        language: language ?? null,
      }),
      cache: "no-store",
      signal: options.signal,
    });

    // A desktop/web frontend can briefly be newer than the non-reloading
    // FastAPI process after an update. Older backends do not expose the async
    // job endpoint, but they can still decompose the same uploaded documents
    // through the legacy synchronous route. Keep uploads working during that
    // version-skew window instead of surfacing a raw 404 in the UI.
    if (response.status === 404 && Array.isArray(source)) {
      const fallbackResponse = await fetch("/api/decompose-documents", {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify({
          file_paths: source,
          language: language ?? null,
        }),
        cache: "no-store",
        signal: options.signal,
      });
      const result = (await ApiResponseHandler.handleResponse(
        fallbackResponse,
        "Failed to decompose documents",
      )) as DecomposedDocument[];
      return {
        job_id: "legacy-sync",
        status: "succeeded",
        progress: {
          completed_files: source.length,
          total_files: source.length,
        },
        result,
        error: null,
      };
    }

    return (await ApiResponseHandler.handleResponse(
      response,
      "Failed to start document processing",
    )) as DocumentDecomposeJob;
  }

  static async getDecomposeDocumentsJob(
    jobId: string,
    signal?: AbortSignal,
  ): Promise<DocumentDecomposeJob> {
    const response = await fetch(
      `/api/decompose-documents/jobs/${encodeURIComponent(jobId)}`,
      {
        method: "GET",
        headers: getHeader(),
        cache: "no-store",
        signal,
      },
    );
    return (await ApiResponseHandler.handleResponse(
      response,
      "Failed to get document processing status",
    )) as DocumentDecomposeJob;
  }

  static async cancelDecomposeDocuments(
    jobId: string,
  ): Promise<DocumentDecomposeJob> {
    const response = await fetch(
      `/api/decompose-documents/jobs/${encodeURIComponent(jobId)}`,
      {
        method: "DELETE",
        headers: getHeader(),
        cache: "no-store",
        keepalive: true,
      },
    );
    return (await ApiResponseHandler.handleResponse(
      response,
      "Failed to cancel document processing",
    )) as DocumentDecomposeJob;
  }
  static async createPresentation({
    presentation_id,
    idempotency_key,
    content,
    n_slides,
    file_paths,
    reference_snapshot_ids,
    language,
    tone,
    verbosity,
    instructions,
    include_table_of_contents,
    include_title_slide,
    web_search,
    theme,
  }: {
    presentation_id: string;
    idempotency_key: string;
    content: string;
    n_slides: number | null;
    file_paths?: string[];
    reference_snapshot_ids?: string[];
    language: string | null;
    tone?: string | null;
    verbosity?: string | null;
    instructions?: string | null;
    include_table_of_contents?: boolean;
    include_title_slide?: boolean;
    web_search?: boolean;
    theme?: PresentationThemePayload | null;
  }) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/create`),
        {
          method: "POST",
          headers: {
            ...getHeader(),
            "Idempotency-Key": idempotency_key,
          },
          body: JSON.stringify({
            presentation_id,
            content,
            n_slides,
            file_paths,
            reference_snapshot_ids,
            language,
            tone,
            verbosity,
            instructions,
            include_table_of_contents,
            include_title_slide,
            web_search,
            theme,
          }),
          cache: "no-cache",
        },
      );

      const created = await ApiResponseHandler.handleResponse(
        response,
        "Failed to create presentation",
      );
      for (const snapshotId of reference_snapshot_ids ?? []) {
        forgetCanonicalAdmission(snapshotId);
      }
      return created;
    } catch (error) {
      for (const snapshotId of reference_snapshot_ids ?? []) {
        await PresentationGenerationApi.cleanupDocumentAdmission({
          referenceSnapshotId: snapshotId,
        });
      }
      console.error("error in presentation creation", error);
      throw error;
    }
  }

  static async createBlankPresentation(): Promise<BlankPresentationResponse> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/create/blank`),
        {
          method: "POST",
          headers: getHeader(),
          cache: "no-cache",
        },
      );
      const presentation = (await ApiResponseHandler.handleResponse(
        response,
        "Failed to create blank presentation",
      )) as BlankPresentationResponse;

      if (!presentation || typeof presentation.id !== "string") {
        throw new Error("Blank presentation response did not include an id");
      }

      return presentation;
    } catch (error) {
      console.error("error in blank presentation creation", error);
      throw error;
    }
  }

  static async editSlide(slide_id: string, prompt: string) {
    try {
      const response = await fetch(getApiUrl(`/api/v1/ppt/slide/edit`), {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify({
          id: slide_id,
          prompt,
        }),
        cache: "no-cache",
      });

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to update slide",
      );
    } catch (error) {
      console.error("error in slide update", error);
      throw error;
    }
  }

  // The payload is a partial presentation snapshot (full deck state from the
  // editor, or just `{ id, theme }` from the theme picker); it is forwarded to
  // the backend verbatim, so it is serialized rather than inspected here.
  static async updatePresentationContent(body: unknown) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/update`),
        {
          method: "PATCH",
          headers: getHeader(),
          body: JSON.stringify(body),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to update presentation content",
      );
    } catch (error) {
      console.error("error in presentation content update", error);
      throw error;
    }
  }

  static async getPresentationVersions(presentationId: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/versions`),
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to load version history",
      );
    } catch (error) {
      console.error("error loading presentation versions", error);
      throw error;
    }
  }

  static async restorePresentationVersion(
    presentationId: string,
    versionId: string,
  ) {
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/presentation/${presentationId}/versions/${versionId}/restore`,
        ),
        {
          method: "POST",
          headers: getHeader(),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to restore version",
      );
    } catch (error) {
      console.error("error restoring presentation version", error);
      throw error;
    }
  }

  static async getShareStatus(presentationId: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/share`),
        { method: "GET", headers: getHeader(), cache: "no-cache" },
      );
      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to load share status",
      );
    } catch (error) {
      console.error("error loading share status", error);
      throw error;
    }
  }

  static async enableShare(presentationId: string, regenerate = false) {
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/presentation/${presentationId}/share${regenerate ? "?regenerate=true" : ""}`,
        ),
        { method: "POST", headers: getHeader(), cache: "no-cache" },
      );
      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to enable sharing",
      );
    } catch (error) {
      console.error("error enabling share", error);
      throw error;
    }
  }

  static async disableShare(presentationId: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/share`),
        { method: "DELETE", headers: getHeader(), cache: "no-cache" },
      );
      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to disable sharing",
      );
    } catch (error) {
      console.error("error disabling share", error);
      throw error;
    }
  }

  // Authored-vNext runs through the canonical Scene document. The same stable
  // elements drive preview, Workbench CAS edits, persistence/publication and
  // native PPTX export; raster/template-only generation is not a success path.
  // The reviewed outline is passed as slides_markdown so user edits are honoured.
  static async generateAuthoredAsync(
    body: GenerationPreferences & {
      presentation_id: string;
      content: string;
      slides_markdown: string[];
      vision_qa?: boolean;
      authored_style?: string;
      masterPin?: {
        masterId: string;
        revision: number;
        digest: `sha256:${string}`;
      };
      idempotency_key?: string;
    },
  ): Promise<StartGenerationResponse> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/generate/async`),
        {
          method: "POST",
          headers: {
            ...getHeader(),
            ...(body.idempotency_key
              ? { "Idempotency-Key": body.idempotency_key }
              : {}),
          },
          body: JSON.stringify({
            presentation_id: body.presentation_id,
            content: body.content,
            slides_markdown: body.slides_markdown,
            ...generationPreferencesPayload(body),
            template: "authored",
            strategy: "authored",
            export_as: "pptx",
            // Optional self-correction pass (render + vision-critique + re-author flagged).
            // OFF by default — it adds a second authoring round (~doubles time); the
            // design-system hardening already prevents most overflow/overlap. Opt in for
            // a max-quality, slower pass.
            vision_qa: body.vision_qa ?? false,
            authored_style: body.authored_style ?? "default",
            ...(body.masterPin ? { masterPin: body.masterPin } : {}),
          }),
          cache: "no-cache",
        },
      );

      const result = await ApiResponseHandler.handleResponse(
        response,
        "Failed to start authored generation",
      );
      return {
        ...result,
        request_id:
          result?.request_id ||
          response.headers.get("x-request-id") ||
          undefined,
      };
    } catch (error) {
      console.warn("error starting authored generation", error);
      throw error;
    }
  }

  static async getGenerationStatus(
    taskId: string,
  ): Promise<GenerationStatusResponse> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/status/${taskId}`),
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        },
      );

      const result = await ApiResponseHandler.handleResponse(
        response,
        "Failed to get generation status",
      );
      return {
        ...result,
        request_id:
          result?.request_id ||
          response.headers.get("x-request-id") ||
          undefined,
      };
    } catch (error) {
      console.warn("error polling generation status", error);
      throw error;
    }
  }

  static async cancelGeneration(taskId: string) {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/status/${taskId}/cancel`),
      {
        method: "POST",
        headers: getHeader(),
      },
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to cancel generation",
    );
  }

  static async presentationPrepare(presentationData: unknown) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/prepare`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to prepare presentation",
      );
    } catch (error) {
      // The caller renders a user-facing notification. Logging a handled ApiError
      // with console.error makes the Next.js development overlay cover that UI.
      console.warn("presentation preparation failed", error);
      throw error;
    }
  }

  static async retemplateAuthoredPresentation(
    presentationId: string,
    presentationData: { authored_style: string; vision_qa?: boolean },
  ) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/retemplate`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to change presentation template",
      );
    } catch (error) {
      console.error("error changing authored presentation template", error);
      throw error;
    }
  }

  static async reviewAuthoredPresentation(
    presentationId: string,
    reviewData: {
      scope: "all" | "current" | "subset";
      slide_indices?: number[];
      /** Explicit request gate; backend rollout flags and kill switch still win. */
      slidespec_opt_in?: boolean;
    },
  ) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/quality-review`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({ ...reviewData, mode: "analyze_and_fix" }),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to start high-quality review",
      );
    } catch (error) {
      console.error(
        "error starting authored presentation quality review",
        error,
      );
      throw error;
    }
  }

  static async previewAuthoredPageRemake(
    presentationId: string,
    slideIndex: number,
  ) {
    const response = await fetch(
      getApiUrl(
        `/api/v1/ppt/presentation/${presentationId}/slides/${slideIndex}/remake/preview`,
      ),
      {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify({}),
      },
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to start page remake preview",
    );
  }

  /** Remake activity a reloaded client would otherwise have lost. */
  static async listAuthoredPageRemakePreviews(presentationId: string) {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/${presentationId}/remake/previews`),
      { method: "GET", headers: getHeader() },
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to load page remake history",
    );
  }

  static async publishAuthoredPageRemake(
    presentationId: string,
    slideIndex: number,
    previewTaskId: string,
  ) {
    const response = await fetch(
      getApiUrl(
        `/api/v1/ppt/presentation/${presentationId}/slides/${slideIndex}/remake/publish`,
      ),
      {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify({ preview_task_id: previewTaskId }),
      },
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to publish page remake",
    );
  }

  // IMAGE AND ICON SEARCH

  static async generateImage(imageGenerate: ImageGenerate) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/images/generate?prompt=${imageGenerate.prompt}`),
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        },
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to generate image",
      );
    } catch (error) {
      console.error("error in image generation", error);
      throw error;
    }
  }

  static getPreviousGeneratedImages = async (): Promise<
    PreviousGeneratedImagesResponse[]
  > => {
    try {
      const response = await fetch(getApiUrl(`/api/v1/ppt/images/generated`), {
        method: "GET",
        headers: getHeader(),
      });

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to get previous generated images",
      );
    } catch (error) {
      console.error("error in getting previous generated images", error);
      throw error;
    }
  };

  static async searchIcons(iconSearch: IconSearch) {
    try {
      const params = new URLSearchParams({
        query: iconSearch.query,
        limit: String(iconSearch.limit),
      });
      if (iconSearch.icon_weight) {
        params.set("icon_weight", iconSearch.icon_weight);
      }
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/icons/search?${params.toString()}`),
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        },
      );

      const icons = await ApiResponseHandler.handleResponse(
        response,
        "Failed to search icons",
      );
      return Array.isArray(icons)
        ? icons.map((icon) =>
            typeof icon === "string" ? resolveBackendAssetUrl(icon) : icon,
          )
        : icons;
    } catch (error) {
      console.error("error in icon search", error);
      throw error;
    }
  }

  /**
   * Frozen compatibility shim. Template V2 remains import/edit/export
   * infrastructure, but it cannot start a new product generation request.
   */
  static async generateTemplateV2Async(
    _body: GenerationPreferences & {
      presentation_id: string;
      content: string;
      slides_markdown: string[];
      template_v2_id: string;
      template_v2_revision: number;
      idempotency_key?: string;
    },
  ): Promise<StartGenerationResponse> {
    throw new Error("template_v2_new_generation_disabled");
  }
}
