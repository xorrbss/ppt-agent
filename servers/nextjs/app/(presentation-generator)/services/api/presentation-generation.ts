import { getHeader, getHeaderForFormData } from "./header";
import { IconSearch, ImageGenerate, ImageSearch, PreviousGeneratedImagesResponse } from "./params";
export class PresentationGenerationApi {


  static async uploadDoc(documents: File[]) {
    const formData = new FormData();

    documents.forEach((document) => {
      formData.append("files", document);
    });

    try {
      const response = await fetch(
        `/api/v1/ppt/files/upload`,
        {
          method: "POST",
          headers: getHeaderForFormData(),
          body: formData,
          cache: "no-cache",
        }
      );

      if (!response.ok) {
        throw new Error(`Upload failed: ${response.statusText}`);
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error("Upload error:", error);
      throw error;
    }
  }



  static async decomposeDocuments(documentKeys: string[]) {
    try {
      const response = await fetch(
        `/api/v1/ppt/files/decompose`,
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
            file_paths: documentKeys,
          }),
          cache: "no-cache",
        }
      );
      if (response.status === 200) {
        const data = await response.json();

        return data;
      } else {
        throw new Error(`Failed to decompose files: ${response.statusText}`);
      }
    } catch (error) {
      console.error("Error in Decompose Files", error);
      throw error;
    }
  }
 

 
  static async editSlide(
    slide_id: string,
   
    prompt: string
  ) {
    try {
    
      const response = await fetch(
        `/api/v1/ppt/slide/edit`,
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

      if (!response.ok) {
        throw new Error("Failed to update slides");
      }

      const data = await response.json();
      return data;
    } catch (error) {
      console.error("error in slide update", error);
      throw error;
    }
  }

  static async updatePresentationContent(body: any) {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/update`,
        {
          method: "PUT",
          headers: getHeader(),
          body: JSON.stringify(body),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();

        return data;
      } else {
        throw new Error(
          `Failed to update presentation content: ${response.statusText}`
        );
      }
    } catch (error) {
      console.error("error in presentation content update", error);
      throw error;
    }
  }

  static async presentationPrepare(presentationData: any) {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/prepare`,
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();

        return data;
      } else {
        throw new Error(`Failed to generate data: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in data generation", error);
      throw error;
    }
  }
  // IMAGE AND ICON SEARCH
  static async imageSearch(imageSearch: ImageSearch) {
    try {
      const response = await fetch(
        `/api/v1/ppt/image/search`,
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(imageSearch),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();
        return data;
      } else {
        throw new Error(`Failed to search images: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in image search", error);
      throw error;
    }
  }
  static async generateImage(imageGenerate: ImageGenerate) {
    try {
      const response = await fetch(
        `/api/v1/ppt/images/generate?prompt=${imageGenerate.prompt}`,
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();

        return data;
      } else {
        throw new Error(`Failed to generate images: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in image generation", error);
      throw error;
    }
  }

  static  getPreviousGeneratedImages = async():Promise<PreviousGeneratedImagesResponse[]>=>{
    try {
      const response = await fetch(
        `/api/v1/ppt/images/generated`,
        {
          method: "GET",
          headers: getHeader(),
        }
      );
      if (response.ok) {
        const data = await response.json();
        return data;
      } else {
        throw new Error(`Failed to get previous generated images: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in getting previous generated images", error);
      throw error;
    }
  }
  static async searchIcons(iconSearch: IconSearch) {
    try {
      const response = await fetch(
        `/api/v1/ppt/icons/search?query=${iconSearch.query}&limit=${iconSearch.limit}`,
        {
          method: "GET",
          headers: getHeader(),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();

        return data;
      } else {
        throw new Error(`Failed to search icons: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in icon search", error);
      throw error;
    }
  }

  static async updateDocuments(body: any) {
    try {
      const response = await fetch(
        `/api/v1/ppt/document/update`,
        {
          method: "POST",
          headers: getHeaderForFormData(),
          body: body,
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();
        return data;
      } else {
        throw new Error(`Failed to update documents: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in document update", error);
      throw error;
    }
  }

  // EXPORT PRESENTATION
  static async exportAsPPTX(presentationData: any) {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/export/pptx`,
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        return await response.json();
      } else {
        throw new Error(`Failed to export as pptx: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in pptx export", error);
      throw error;
    }
  }
  static async exportAsPDF(presentationData: any) {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/export_as_pdf`,
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify(presentationData),
        }
      );
      if (response.ok) {
        const data = await response.json();

        return data;
      } else {
        throw new Error(`Failed to export as pdf: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in pdf export", error);
      throw error;
    }
  }
  static async deleteSlide(presentation_id: string, slide_id: string) {
    try {
      const response = await fetch(
        `/api/v1/ppt/slide/delete?presentation_id=${presentation_id}&slide_id=${slide_id}`,
        {
          method: "DELETE",
          headers: getHeader(),
          cache: "no-cache",
        }
      );
      if (response.status === 204) {
        return true;
      } else {
        throw new Error(`Failed to delete slide: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in slide deletion", error);
      throw error;
    }
  }
 

  static async createPresentation({
    prompt,
    n_slides,
   file_paths,
   language,
  

  }: {
    prompt: string;
    n_slides: number | null;
    file_paths?: string[];
    language: string | null;
   
  }) {
    try {
      const response = await fetch(
        `/api/v1/ppt/presentation/create`,
        {
          method: "POST",
          headers: getHeader(),
          body: JSON.stringify({
            prompt,
            n_slides,
            file_paths,
            language,
           

          }),
          cache: "no-cache",
        }
      );
      if (response.ok) {
        const data = await response.json();
        return data;
      } else {
        throw new Error(`Failed to get questions: ${response.statusText}`);
      }
    } catch (error) {
      console.error("error in question generation", error);
      throw error;
    }
  }
}
