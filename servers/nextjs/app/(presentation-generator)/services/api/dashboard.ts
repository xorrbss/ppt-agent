import {
  getHeader,
} from "@/app/(presentation-generator)/services/api/header";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { getApiUrl } from "@/utils/api";
import {
  buildPresentationListPath,
  isPresentationListRequestAbort,
  normalizePresentationListEnvelope,
} from "./dashboard-list-query";
import type {
  PresentationListEnvelope,
  PresentationListOptions,
} from "./dashboard-list-query";

export type PresentationListPage = PresentationListEnvelope<PresentationResponse>;

export interface PresentationResponse {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  data: Record<string, unknown> | null;
  file: string;
  n_slides: number;
  prompt: string;
  summary: string | null;
  theme: Record<string, unknown> | null;
  titles: string[];
  user_id: string;
  vector_store: unknown;

  thumbnail: string;
  slides: unknown[];
}

export interface PresentationGenerationSettings {
  n_slides: number;
  language: string;
  tone: string | null;
  verbosity: string | null;
  instructions: string | null;
  include_table_of_contents: boolean;
  include_title_slide: boolean;
  web_search: boolean;
  mode: string | null;
  version: string;
}

export interface PresentationGenerationInfo {
  prompt: string;
  attachment_file_names: string[];
  settings: PresentationGenerationSettings;
}

export interface ShareInfo {
  shared: boolean;
  share_token: string | null;
  expires_at: string | null;
  view_count: number;
  expired: boolean;
}

export class DashboardApi {

  static async getPresentations(
    options: PresentationListOptions = {},
    signal?: AbortSignal,
  ): Promise<PresentationListPage> {
    try {
      const response = await fetch(
        getApiUrl(buildPresentationListPath(options)),
        {
          method: "GET",
          credentials: "include",
          signal,
        }
      );

      // Handle the special case where 404 means "no presentations found"
      if (response.status === 404) {
        console.log("No presentations found");
        return normalizePresentationListEnvelope([], options);
      }

      const payload = await ApiResponseHandler.handleResponse(
        response,
        "Failed to fetch presentations",
      );
      return normalizePresentationListEnvelope(payload, options);
    } catch (error) {
      // React Strict Mode and changing list filters both cancel obsolete
      // requests. Logging those expected aborts with console.error makes the
      // Next.js development overlay report a false runtime failure.
      if (!isPresentationListRequestAbort(error, signal)) {
        console.error("Error fetching presentations:", error);
      }
      throw error;
    }
  }

  static async getPresentation(id: string) {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/${id}`),
      {
        method: "GET",
        credentials: "include",
      }
    );

    return await ApiResponseHandler.handleResponse(response, "Presentation not found");
  }

  static async getPresentationGenerationInfo(
    id: string
  ): Promise<PresentationGenerationInfo> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${id}/generation-info`),
        {
          method: "GET",
          credentials: "include",
        }
      );

      return await ApiResponseHandler.handleResponse(
        response,
        "Failed to fetch presentation generation info"
      );
    } catch (error) {
      console.error("Error fetching presentation generation info:", error);
      throw error;
    }
  }

  static async deletePresentation(presentation_id: string) {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/${presentation_id}`),
        {
          method: "DELETE",
          headers: getHeader(),
        }
      );

      return await ApiResponseHandler.handleResponseWithResult(response, "Failed to delete presentation");
    } catch (error) {
      console.error("Error deleting presentation:", error);
      return {
        success: false,
        message: error instanceof Error ? error.message : "Failed to delete presentation",
      };
    }
  }

  static async getShareStatus(presentationId: string): Promise<ShareInfo> {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/${presentationId}/share`),
      {
        method: "GET",
        headers: getHeader(),
        credentials: "include",
        cache: "no-store",
      },
    );
    return await ApiResponseHandler.handleResponse(
      response,
      "Failed to load share status",
    );
  }

  static async enableShare(
    presentationId: string,
    expiresAt: string | null,
    regenerate = false,
  ): Promise<ShareInfo> {
    const response = await fetch(
      getApiUrl(
        `/api/v1/ppt/presentation/${presentationId}/share${
          regenerate ? "?regenerate=true" : ""
        }`,
      ),
      {
        method: "POST",
        headers: getHeader(),
        credentials: "include",
        cache: "no-store",
        body: JSON.stringify({ expires_at: expiresAt }),
      },
    );
    return await ApiResponseHandler.handleResponse(
      response,
      "Failed to enable sharing",
    );
  }

  static async disableShare(presentationId: string): Promise<ShareInfo> {
    const response = await fetch(
      getApiUrl(`/api/v1/ppt/presentation/${presentationId}/share`),
      {
        method: "DELETE",
        headers: getHeader(),
        credentials: "include",
        cache: "no-store",
      },
    );
    return await ApiResponseHandler.handleResponse(
      response,
      "Failed to disable sharing",
    );
  }
}
