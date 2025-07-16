import {
  getHeader,
} from "@/app/(presentation-generator)/services/api/header";



export interface PresentationResponse {
  id: string;
  title: string;
  created_at: string;
  data: any | null;
  file: string;
  n_slides: number;
  prompt: string;
  summary: string | null;
    theme: string;
    titles: string[];
    user_id: string;
    vector_store: any;

    thumbnail: string;
    slide: any;
}

export class DashboardApi {

  static async getPresentations(): Promise<PresentationResponse[]> {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/all`,
        {
          method: "GET",
        }
      );
      if (response.status === 200) {
        const data = await response.json();
        return data;
      } else if (response.status === 404) {
        console.log("No presentations found");
        return [];
      }
      return [];
    } catch (error) {
      console.error("Error fetching presentations:", error);
      throw error;
    }
  }
  static async getPresentation(id: string) {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/?id=${id}`,
        {
          method: "GET",

        }
      );
      if (response.status === 200) {
        const data = await response.json();
        return data;
      }
      throw new Error("Presentation not found");
    } catch (error) {
      console.error("Error fetching presentations:", error);
      throw error;
    }
  }
  static async deletePresentation(presentation_id: string) {
    try {
      const response = await fetch(
        `/api/v1/ppt/delete?id=${presentation_id}`,
        {
          method: "DELETE",
          headers: getHeader(),
        }
      );

      if (response.status === 204) {
        return true;
      }
      return false;
    } catch (error) {
      console.error("Error deleting presentation:", error);
      throw error;
    }
  }
  
}
