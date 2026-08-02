import { getHeader, getHeaderForFormData } from "./header";
import { IconSearch, ImageGenerate, PreviousGeneratedImagesResponse } from "./params";
import { ApiResponseHandler } from "./api-error-handler";
import { getApiUrl, resolveBackendAssetUrl } from "@/utils/api";
import { fetchWithNetworkRetry } from "@/utils/fetchWithNetworkRetry";

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
  data?: Record<string, any> | null;
  request_id?: string;
  progress?: GenerationProgress | null;
  failure?: GenerationFailure | null;
};

export type StartGenerationResponse = {
  id?: string;
  status?: string;
  request_id?: string;
};

export type PresentationThemePayload = {
  id: string;
  name: string;
  description: string;
  user: string;
  logo: string | null;
  logo_url: string | null;
  company_name: string | null;
  data: Record<string, unknown>;
};

type GenerationPreferences = {
  language?: string | null;
  tone?: string;
  verbosity?: string;
  instructions?: string | null;
  include_table_of_contents?: boolean;
  include_title_slide?: boolean;
  web_search?: boolean;
};

const generationPreferencesPayload = (body: GenerationPreferences) => ({
  language: body.language ?? null,
  ...(body.tone ? { tone: body.tone } : {}),
  ...(body.verbosity ? { verbosity: body.verbosity } : {}),
  ...(body.instructions !== undefined
    ? { instructions: body.instructions }
    : {}),
  ...(body.include_table_of_contents !== undefined
    ? { include_table_of_contents: body.include_table_of_contents }
    : {}),
  ...(body.include_title_slide !== undefined
    ? { include_title_slide: body.include_title_slide }
    : {}),
  ...(body.web_search !== undefined
    ? { web_search: body.web_search }
    : {}),
});

export class PresentationGenerationApi {
  static async uploadDoc(documents: File[]) {
    const formData = new FormData();

    documents.forEach((document) => {
      formData.append("files", document);
    });

    try {
      const response = await fetchWithNetworkRetry(
        getApiUrl(`/api/v1/ppt/files/upload`),
        {
          method: "POST",
          headers: getHeaderForFormData(),
          body: formData,
          cache: "no-cache",
        }
      );

      return await ApiResponseHandler.handleResponse(response, "Failed to upload documents");
    } catch (error) {
      console.error("Upload error:", error);
      throw error;
    }
  }

  static async decomposeDocuments(
    documentKeys: string[],
    language?: string | null
  ) {
    try {
      // Document conversion/OCR can take longer than Next's rewrite proxy allows.
      // Use the route handler that forwards the request with no short proxy timeout.
      const response = await fetch("/api/decompose-documents", {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify({
          file_paths: documentKeys,
          language: language ?? null,
        }),
        cache: "no-cache",
      });

      return await ApiResponseHandler.handleResponse(response, "Failed to decompose documents");
    } catch (error) {
      console.error("Error in Decompose Files", error);
      throw error;
    }
  }
  static async createPresentation({
    presentation_id,
    idempotency_key,
    content,
    n_slides,
    file_paths,
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
        }
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to create presentation"
      );
    } catch (error) {
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
        }
      );
      const presentation = (await ApiResponseHandler.handleResponse(
        response,
        "Failed to create blank presentation"
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

  static async editSlide(
    slide_id: string,
    prompt: string
  ) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/slide/edit`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
            id: slide_id,
            prompt,
          }),
          cache: "no-cache",
        }
      );

      return await ApiResponseHandler.handleResponse(response, "Failed to update slide");
    } catch (error) {
      console.error("error in slide update", error);
      throw error;
    }
  }

  static async updatePresentationContent(body: any) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/update`),
        {
          method: "PATCH",
          headers: getHeader(),
          body: JSON.stringify(body),
          cache: "no-cache",
        }
      );
      
      return await ApiResponseHandler.handleResponse(response, "Failed to update presentation content");
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
        }
      );

      return await ApiResponseHandler.handleResponse(response, "Failed to load version history");
    } catch (error) {
      console.error("error loading presentation versions", error);
      throw error;
    }
  }

  static async restorePresentationVersion(presentationId: string, versionId: string) {
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/presentation/${presentationId}/versions/${versionId}/restore`
        ),
        {
          method: "POST",
          headers: getHeader(),
          cache: "no-cache",
        }
      );

      return await ApiResponseHandler.handleResponse(response, "Failed to restore version");
    } catch (error) {
      console.error("error restoring presentation version", error);
      throw error;
    }
  }

  static async getShareStatus(presentationId: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/share`),
        { method: "GET", headers: getHeader(), cache: "no-cache" }
      );
      return await ApiResponseHandler.handleResponse(response, "Failed to load share status");
    } catch (error) {
      console.error("error loading share status", error);
      throw error;
    }
  }

  static async enableShare(presentationId: string, regenerate = false) {
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/presentation/${presentationId}/share${regenerate ? "?regenerate=true" : ""}`
        ),
        { method: "POST", headers: getHeader(), cache: "no-cache" }
      );
      return await ApiResponseHandler.handleResponse(response, "Failed to enable sharing");
    } catch (error) {
      console.error("error enabling share", error);
      throw error;
    }
  }

  static async disableShare(presentationId: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/share`),
        { method: "DELETE", headers: getHeader(), cache: "no-cache" }
      );
      return await ApiResponseHandler.handleResponse(response, "Failed to disable sharing");
    } catch (error) {
      console.error("error disabling share", error);
      throw error;
    }
  }

  // Authored (high-quality) mode: the model authors bespoke HTML per slide, rendered
  // to images and assembled into an image PPTX. It bypasses the layout/stream path, so
  // it runs through the async generate endpoint (minutes-long) and is polled to
  // completion. The reviewed outline is passed as slides_markdown so the user's edits
  // are honoured. Returns the async task ({ id, status, ... }).
  static async generateAuthoredAsync(body: GenerationPreferences & {
    presentation_id: string;
    content: string;
    slides_markdown: string[];
    vision_qa?: boolean;
    authored_style?: string;
    idempotency_key?: string;
  }): Promise<StartGenerationResponse> {
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
            export_as: "pptx",
            // Optional self-correction pass (render + vision-critique + re-author flagged).
            // OFF by default — it adds a second authoring round (~doubles time); the
            // design-system hardening already prevents most overflow/overlap. Opt in for
            // a max-quality, slower pass.
            vision_qa: body.vision_qa ?? false,
            authored_style: body.authored_style ?? "default",
          }),
          cache: "no-cache",
        }
      );

      const result = await ApiResponseHandler.handleResponse(
        response,
        "Failed to start authored generation"
      );
      return {
        ...result,
        request_id:
          result?.request_id || response.headers.get("x-request-id") || undefined,
      };
    } catch (error) {
      console.warn("error starting authored generation", error);
      throw error;
    }
  }

  static async generateTemplateV2Async(body: GenerationPreferences & {
    presentation_id: string;
    content: string;
    slides_markdown: string[];
    template_v2_id: string;
    template_v2_revision: number;
    idempotency_key?: string;
  }): Promise<StartGenerationResponse> {
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
            strategy: "template_v2",
            template_v2_id: body.template_v2_id,
            template_v2_revision: body.template_v2_revision,
            export_as: "pptx",
          }),
          cache: "no-cache",
        }
      );

      const result = await ApiResponseHandler.handleResponse(
        response,
        "Failed to start structured-template generation"
      );
      return {
        ...result,
        request_id:
          result?.request_id || response.headers.get("x-request-id") || undefined,
      };
    } catch (error) {
      console.warn("error starting structured-template generation", error);
      throw error;
    }
  }

  static async generateStandardAsync(body: GenerationPreferences & {
    presentation_id: string;
    content: string;
    slides_markdown: string[];
    template: string;
    idempotency_key?: string;
  }): Promise<StartGenerationResponse> {
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
            template: body.template,
            strategy: body.template === "adaptive" ? "adaptive" : "legacy",
            export_as: "pptx",
          }),
          cache: "no-cache",
        }
      );

      const result = await ApiResponseHandler.handleResponse(
        response,
        "Failed to start presentation generation"
      );
      return {
        ...result,
        request_id:
          result?.request_id || response.headers.get("x-request-id") || undefined,
      };
    } catch (error) {
      console.warn("error starting presentation generation", error);
      throw error;
    }
  }

  static async getGenerationStatus(
    taskId: string
  ): Promise<GenerationStatusResponse> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/status/${taskId}`),
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        }
      );

      const result = await ApiResponseHandler.handleResponse(
        response,
        "Failed to get generation status"
      );
      return {
        ...result,
        request_id:
          result?.request_id || response.headers.get("x-request-id") || undefined,
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
      }
    );
    return ApiResponseHandler.handleResponse(
      response,
      "Failed to cancel generation"
    );
  }

  static async presentationPrepare(presentationData: any) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/prepare`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
          cache: "no-cache",
        }
      );
      
      return await ApiResponseHandler.handleResponse(response, "Failed to prepare presentation");
    } catch (error) {
      // The caller renders a user-facing notification. Logging a handled ApiError
      // with console.error makes the Next.js development overlay cover that UI.
      console.warn("presentation preparation failed", error);
      throw error;
    }
  }

  static async retemplateAuthoredPresentation(
    presentationId: string,
    presentationData: { authored_style: string; vision_qa?: boolean }
  ) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentationId}/retemplate`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
          cache: "no-cache",
        }
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to change presentation template"
      );
    } catch (error) {
      console.error("error changing authored presentation template", error);
      throw error;
    }
  }

  static async reviewAuthoredPresentation(
    presentationId: string,
    reviewData: {
      scope: "all" | "current";
      slide_indices?: number[];
      mode: "analyze_only" | "analyze_and_fix";
    }
  ) {
    try {
      const response = await fetch(
        getApiUrl(
          `/api/v1/ppt/presentation/${presentationId}/quality-review`
        ),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(reviewData),
          cache: "no-cache",
        }
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to start high-quality review"
      );
    } catch (error) {
      console.error("error starting authored presentation quality review", error);
      throw error;
    }
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
        }
      );
      
      return await ApiResponseHandler.handleResponse(response, "Failed to generate image");
    } catch (error) {
      console.error("error in image generation", error);
      throw error;
    }
  }

  static getPreviousGeneratedImages = async (): Promise<PreviousGeneratedImagesResponse[]> => {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/images/generated`),
        {
          method: "GET",
          headers: getHeader(),
        }
      );
      
      return await ApiResponseHandler.handleResponse(response, "Failed to get previous generated images");
    } catch (error) {
      console.error("error in getting previous generated images", error);
      throw error;
    }
  }
  
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
        }
      );
      
      const icons = await ApiResponseHandler.handleResponse(response, "Failed to search icons");
      return Array.isArray(icons)
        ? icons.map((icon) =>
            typeof icon === "string" ? resolveBackendAssetUrl(icon) : icon
          )
        : icons;
    } catch (error) {
      console.error("error in icon search", error);
      throw error;
    }
  }

}
