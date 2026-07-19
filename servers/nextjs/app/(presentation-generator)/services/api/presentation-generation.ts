import { getHeader, getHeaderForFormData } from "./header";
import { IconSearch, ImageGenerate, ImageSearch, PreviousGeneratedImagesResponse } from "./params";
import { ApiResponseHandler } from "./api-error-handler";
import { getApiUrl, resolveBackendAssetUrl } from "@/utils/api";

export class PresentationGenerationApi {
  static async uploadDoc(documents: File[]) {
    const formData = new FormData();

    documents.forEach((document) => {
      formData.append("files", document);
    });

    try {
      const response = await fetch(
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
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/files/decompose`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
            file_paths: documentKeys,
            language: language ?? null,
          }),
          cache: "no-cache",
        }
      );
      
      return await ApiResponseHandler.handleResponse(response, "Failed to decompose documents");
    } catch (error) {
      console.error("Error in Decompose Files", error);
      throw error;
    }
  }
 
   static async createPresentation({
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
    
  }: {
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
  }) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/create`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
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
          }),
          cache: "no-cache",
        }
      );
      
      return await ApiResponseHandler.handleResponse(response, "Failed to create presentation");
    } catch (error) {
      console.error("error in presentation creation", error);
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
  static async generateAuthoredAsync(body: {
    content: string;
    slides_markdown: string[];
    language?: string | null;
    vision_qa?: boolean;
    authored_style?: string;
  }) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/generate/async`),
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
            content: body.content,
            slides_markdown: body.slides_markdown,
            language: body.language ?? null,
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

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to start authored generation"
      );
    } catch (error) {
      console.error("error starting authored generation", error);
      throw error;
    }
  }

  static async getGenerationStatus(taskId: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/status/${taskId}`),
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        }
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to get generation status"
      );
    } catch (error) {
      console.error("error polling generation status", error);
      throw error;
    }
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
      console.error("error in data generation", error);
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
