import { getHeader, getHeaderForFormData } from "./header";
import {
  IconSearch,
  ImageGenerate,
  PreviousGeneratedImagesResponse,
} from "./params";
import type { SourceContentPolicy } from "./source-content/contract";
import { ApiError, apiErrorFromPayload, ApiResponseHandler } from "./api-error-handler";
import { getApiUrl, resolveBackendAssetUrl } from "@/utils/api";
import { fetchWithNetworkRetry } from "@/utils/fetchWithNetworkRetry";
import { getWorkspaceTimeline } from "./workspace-commands";
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
import type {
  BlankPresentationResponse,
  CanonicalDocumentAsset,
  DecomposedDocument,
  DecomposeDocumentsOptions,
  DecomposeDocumentSource,
  DocumentDecomposeJob,
  DocumentUploadAdmission,
  GenerationStatusResponse,
  StartGenerationResponse,
} from "./presentation-generation-types";

export type { GenerationPreferences, PresentationThemePayload } from "./presentation-generation-options";

export type {
  BlankPresentationResponse,
  CanonicalDocumentAsset,
  DecomposedDocument,
  DecomposeDocumentsOptions,
  DocumentDecomposeFailure,
  DocumentDecomposeJob,
  DocumentDecomposeProgress,
  DocumentUploadAdmission,
  GenerationFailure,
  GenerationProgress,
  GenerationStage,
  GenerationStatusResponse,
  StartGenerationResponse,
} from "./presentation-generation-types";

const DOCUMENT_DECOMPOSE_POLL_INTERVAL_MS = 1_000;

type SlideOrderBody = { slide_ids: string[]; expected_revision: number };
type SlideOrderResult = { presentation_id: string; revision: number; order: string[]; operation_id: string };
type SlideOrderPollOptions = { signal?: AbortSignal; pollIntervalMs?: number; timeoutMs?: number };

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

const generationPreferencesPayload = (body: GenerationPreferences) => ({ language: body.language ?? null, ...(body.tone ? { tone: body.tone } : {}), ...(body.verbosity ? { verbosity: body.verbosity } : {}), ...(body.instructions !== undefined ? { instructions: body.instructions } : {}), ...(body.include_table_of_contents !== undefined ? { include_table_of_contents: body.include_table_of_contents } : {}), ...(body.include_title_slide !== undefined ? { include_title_slide: body.include_title_slide } : {}), ...(body.web_search !== undefined ? { web_search: body.web_search } : {}), ...(body.codex_generation_preset ? { codex_generation_preset: body.codex_generation_preset } : {}) });

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
    previous_presentation_id,
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
    common_header_design,
    common_header_mode,
    common_header_cover_exception,
    aspect_ratio,
    page_size,
    orientation,
    svg_canvas,
    svgTemplatePin,
    svgTemplateMode,
    svgTemplateFrameMap,
    svg_design_reference_snapshot_id,
    theme,
    strategy = "svg",
  }: {
    presentation_id: string;
    previous_presentation_id?: string | null;
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
    common_header_design?: import("@/lib/common-header-design").CommonHeaderDesign;
    common_header_mode?: "uniform" | "free";
    common_header_cover_exception?: boolean;
    aspect_ratio?: "16:9" | "9:16" | "9:13" | "1:1" | "21:9" | "4:3";
    svg_canvas?: import("../../upload/utils/svgCanvasSelection").SvgCanvasSelection;
    svg_design_reference_snapshot_id?: string;
    svgTemplatePin?: import("./svg-templates").SvgTemplatePin;
    svgTemplateMode?: import("./svg-templates").SvgTemplateMode;
    svgTemplateFrameMap?: Record<string, string>;
    page_size?: "A4";
    orientation?: "landscape" | "portrait";
    theme?: PresentationThemePayload | null;
    /** Server persists this before the async generation job is admitted. */
    strategy?: "authored" | "svg";
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
            ...(previous_presentation_id ? { previous_presentation_id } : {}),
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
            ...(strategy === "svg" && (common_header_mode ?? "uniform") === "uniform" && common_header_design ? { common_header_design } : {}),
            ...(strategy === "svg" && common_header_mode ? { common_header_mode, common_header_cover_exception: common_header_cover_exception ?? false } : {}),
            ...(svg_canvas ? { svg_canvas } : page_size && orientation
              ? { page_size, orientation }
              : { aspect_ratio }),
            ...(svg_design_reference_snapshot_id ? { svg_design_reference_snapshot_id } : {}),
            ...(svgTemplatePin ? {
              svg_template_pin: svgTemplatePin,
              svg_template_mode: svgTemplateMode,
              svg_template_frame_map: svgTemplateFrameMap,
            } : {}),
            theme,
            strategy,
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
      // A lost response or server error can follow a committed create. Keep
      // immutable document admissions usable for an exact same-key replay.
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
        for (const snapshotId of reference_snapshot_ids ?? []) {
          await PresentationGenerationApi.cleanupDocumentAdmission({
            referenceSnapshotId: snapshotId,
          });
        }
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

  // The drag reorder in SidePanel.tsx used to rely on the generic autosave
  // PATCH above (`/presentation/update`), which has no backend handler, so
  // reorder never persisted for any deck. This calls the narrow slide-order
  // endpoint instead; other autosave behavior is unchanged.
  static async reorderSlides(
    presentationId: string,
    body: SlideOrderBody,
    options: SlideOrderPollOptions & { onPending?: (operationId: string) => void } = {},
  ): Promise<SlideOrderResult> {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/${presentationId}/slides/order`),
      {
        method: "PATCH",
        headers: getHeader(),
        body: JSON.stringify(body),
        cache: "no-cache",
        signal: options.signal,
      },
    );

    try {
      return await ApiResponseHandler.handleResponse(response, "Failed to save the new slide order");
    } catch (error) {
      const operationId = response.headers.get("X-Workspace-Operation-ID");
      if (!(error instanceof ApiError) || error.status !== 503 ||
          error.code !== "slide_order_still_running" ||
          !operationId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(operationId)) {
        throw error;
      }
      options.onPending?.(operationId);
      return this.waitForSlideOrderOperation(presentationId, body, operationId, options);
    }
  }

  // Only read the admitted operation through the owner-scoped timeline.
  // A bounded wait ending without a terminal result is still unconfirmed.
  static async waitForSlideOrderOperation(
    presentationId: string,
    body: SlideOrderBody,
    operationId: string,
    options: SlideOrderPollOptions = {},
  ): Promise<SlideOrderResult> {
    const deadline = Date.now() + (options.timeoutMs ?? 60_000);
    const controller = new AbortController();
    const abort = () => controller.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    if (options.signal?.aborted) abort();
    const timeout = window.setTimeout(abort, Math.max(0, deadline - Date.now()));
    const unconfirmed = (code: string) => new ApiError("Slide order status is unconfirmed", {
      code, detail: { operation_id: operationId },
    });
    try {
      while (Date.now() < deadline) {
        let beforePosition: number | undefined;
        do {
          const page = await getWorkspaceTimeline(presentationId, {
            limit: 200, beforePosition, signal: controller.signal,
          });
          const operation = page.items.find(item =>
            item.anchorType === "operation" && item.operationId === operationId);
          if (operation?.anchorType === "operation") {
            if (operation.operationType !== "presentation.slides.reorder" ||
                operation.target?.presentation_id !== presentationId ||
                operation.expectedRevision !== body.expected_revision) {
              throw unconfirmed("slide_order_operation_mismatch");
            }
            if (operation.status === "succeeded") {
              const order = operation.result?.order;
              const revision = operation.resultRevision;
              if (!Array.isArray(order) || order.length !== body.slide_ids.length ||
                  !order.every((id, index) => id === body.slide_ids[index]) ||
                  !Number.isInteger(revision) || (revision as number) < body.expected_revision) {
                throw unconfirmed("slide_order_result_mismatch");
              }
              return { presentation_id: presentationId, revision: revision as number,
                order: order as string[], operation_id: operationId };
            }
            if (["failed", "cancelled", "deadline_exceeded", "dead_letter"].includes(operation.status)) {
              throw new ApiError("Slide order operation did not succeed", {
                status: operation.error?.startsWith("stale_") ? 409 : 422,
                code: operation.error || `slide_order_${operation.status}`,
                detail: { operation_id: operationId, state: operation.status },
              });
            }
            break;
          }
          if (!page.hasMore) break;
          if (page.nextBeforePosition === undefined ||
              (beforePosition !== undefined && page.nextBeforePosition >= beforePosition)) {
            throw unconfirmed("slide_order_status_unavailable");
          }
          beforePosition = page.nextBeforePosition;
        } while (Date.now() < deadline);
        await waitForDecomposePoll(options.pollIntervalMs ?? 1_000, controller.signal);
      }
      throw unconfirmed("slide_order_poll_timeout");
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw unconfirmed(controller.signal.aborted
        ? "slide_order_poll_timeout" : "slide_order_status_unavailable");
    } finally {
      window.clearTimeout(timeout);
      options.signal?.removeEventListener("abort", abort);
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
      slides_markdown?: string[];
      vision_qa?: boolean;
      strategy?: "authored" | "svg";
      common_header_design?: import("@/lib/common-header-design").CommonHeaderDesign;
    common_header_mode?: "uniform" | "free";
      common_header_cover_exception?: boolean;
      authored_style?: string;
      aspect_ratio?: "16:9" | "9:16" | "9:13" | "1:1" | "21:9" | "4:3";
      svg_canvas?: import("../../upload/utils/svgCanvasSelection").SvgCanvasSelection;
    svg_design_reference_snapshot_id?: string;
    svgTemplatePin?: import("./svg-templates").SvgTemplatePin;
    svgTemplateMode?: import("./svg-templates").SvgTemplateMode;
    svgTemplateFrameMap?: Record<string, string>;
    page_size?: "A4";
      orientation?: "landscape" | "portrait";
      masterPin?: {
        masterId: string;
        revision: number;
        digest: `sha256:${string}`;
      };
      sourceMode?: "source_content_restyle" | "raster_reconstruction";
      rasterApprovalPin?: import("./source-content/raster-contract").RasterApprovalPinWire;
      referenceSnapshotIds?: string[];
      sourceContentManifestPin?: {
        manifestId: string;
        digest: `sha256:${string}`;
      };
      contentPolicy?: SourceContentPolicy;
      idempotency_key?: string;
    },
  ): Promise<StartGenerationResponse> {
    // An omitted strategy now means svg (the current default), not authored.
    const resolvedStrategy = body.strategy ?? "svg";
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
            ...(body.sourceMode ? {} : { slides_markdown: body.slides_markdown ?? [] }),
            ...generationPreferencesPayload(body),
            template: "authored",
            strategy: resolvedStrategy,
            ...(resolvedStrategy === "svg" && (body.common_header_mode ?? "uniform") === "uniform" && body.common_header_design ? { common_header_design: body.common_header_design } : {}),
            ...(resolvedStrategy === "svg" && body.common_header_mode ? {
              common_header_mode: body.common_header_mode,
              common_header_cover_exception: body.common_header_cover_exception ?? false,
            } : {}),
            export_as: "pptx",
            // Export refuses a "development" receipt in every format.
            qualityProfile: "release",
            // Optional self-correction pass (render + vision-critique + re-author flagged).
            // OFF by default — it adds a second authoring round (~doubles time); the
            // design-system hardening already prevents most overflow/overlap. Opt in for
            // a max-quality, slower pass.
            vision_qa: resolvedStrategy === "svg" ? false : (body.vision_qa ?? false),
            ...(body.svgTemplatePin ? {} : { authored_style: body.authored_style ?? "default" }),
            ...(body.svg_design_reference_snapshot_id ? { svg_design_reference_snapshot_id: body.svg_design_reference_snapshot_id } : {}),
            ...(body.svgTemplatePin ? { svgTemplatePin: body.svgTemplatePin, svgTemplateMode: body.svgTemplateMode, svgTemplateFrameMap: body.svgTemplateFrameMap } : {}),
            ...(body.svg_canvas ? { svg_canvas: body.svg_canvas }
              : body.page_size && body.orientation
                ? { page_size: body.page_size, orientation: body.orientation }
                : body.aspect_ratio ? { aspect_ratio: body.aspect_ratio }
                : body.sourceMode === "source_content_restyle" && resolvedStrategy === "svg" ? {}
                : { aspect_ratio: "16:9" }),
            ...(body.masterPin ? { masterPin: body.masterPin } : {}),
            ...(body.sourceMode ? { sourceMode: body.sourceMode } : {}),
            ...(body.rasterApprovalPin ? { rasterApprovalPin: body.rasterApprovalPin } : {}),
            ...(body.sourceMode === "raster_reconstruction" && body.referenceSnapshotIds
              ? { reference_snapshot_ids: body.referenceSnapshotIds } : {}),
            ...(body.sourceContentManifestPin
              ? { sourceContentManifestPin: body.sourceContentManifestPin }
              : {}),
            ...(body.contentPolicy ? { contentPolicy: body.contentPolicy } : {}),
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
}
