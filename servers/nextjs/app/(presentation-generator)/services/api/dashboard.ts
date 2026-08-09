import {
  getHeader,
} from "@/app/(presentation-generator)/services/api/header";
import { ApiResponseHandler } from "@/app/(presentation-generator)/services/api/api-error-handler";
import { getApiUrl } from "@/utils/api";

export interface PresentationResponse {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  // Opaque server-side payloads: the dashboard only lists decks, so these are
  // carried through untouched rather than modelled. Narrow them at the point a
  // consumer actually reads one.
  data: unknown;
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

export class DashboardApi {

  static async getPresentations(): Promise<PresentationResponse[]> {
    try {
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/presentation/all`),
        {
          method: "GET",
        }
      );

      // Handle the special case where 404 means "no presentations found"
      if (response.status === 404) {
        console.log("No presentations found");
        return [];
      }

      return await ApiResponseHandler.handleResponse<PresentationResponse[]>(
        response,
        "Failed to fetch presentations"
      );
    } catch (error) {
      console.error("Error fetching presentations:", error);
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

      return await ApiResponseHandler.handleResponse<PresentationGenerationInfo>(
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
}
