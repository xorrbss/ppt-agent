import { ApiResponseHandler } from "./api-error-handler"
import { getHeader, getHeaderForFormData } from "./header"
import { Theme, ThemeParams } from "./types"
import type { ThemeColors } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/types"
import { getApiUrl } from "@/utils/api"



/**
 * Response of `POST /theme/generate`: the 16 named colour roles. The index
 * signature keeps it usable as a plain colour map by callers that compose a
 * theme from an arbitrary palette.
 */
export interface GeneratedThemeColors extends ThemeColors {
  [key: string]: string
}

/** Response of `POST /fonts/upload`. */
export interface UploadedFontResponse {
  font_name: string
  font_url: string
}

/** Response of `GET /fonts/uploaded`. */
export interface UserFontsResponse {
  fonts: { name: string; url: string }[]
}

class ThemeApi {

  static async getThemes(): Promise<Theme[]> {
    const response = await fetch(getApiUrl(`/api/v1/ppt/themes/all`), {
      method: "GET",
      headers: getHeader(),
      cache: "no-store",
    })
    return await ApiResponseHandler.handleResponse<Theme[]>(response, "Failed to get themes")
  }
  static async createTheme(theme: ThemeParams) {
    try {

      const response = await fetch(getApiUrl(`/api/v1/ppt/themes/create`), {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify(theme),
        cache: "no-store",
      })
      return await ApiResponseHandler.handleResponse<Theme>(response, "Failed to create theme")
    }
    catch (error) {
      console.error("Error creating theme:", error)
      throw error
    }
  }
  static async updateTheme(theme: ThemeParams) {
    try {
      const response = await fetch(getApiUrl(`/api/v1/ppt/themes/update/${theme.id}`), {
        method: "PATCH",
        headers: getHeader(),
        body: JSON.stringify(theme),
        cache: "no-store",
      })
      return await ApiResponseHandler.handleResponse<Theme>(response, "Failed to update theme")
    }
    catch (error) {
      console.error("Error updating theme:", error)
      throw error
    }
  }
  static async deleteTheme(themeId: string) {
    try {
      const response = await fetch(getApiUrl(`/api/v1/ppt/themes/delete/${themeId}`), {
        method: "DELETE",
        headers: getHeader(),
        cache: "no-store",
      })
      return await ApiResponseHandler.handleResponse(response, "Failed to delete theme")
    }
    catch (error) {
      console.error("Error deleting theme:", error)
      throw error
    }
  }
  static async generateTheme({ primary, background }: { primary?: string, background?: string }) {
    try {
      let body = {}
      if (primary || background) {
        body = {
          primary: primary ?? undefined,
          background: background ?? undefined,
        }
      }
      const response = await fetch(getApiUrl(`/api/v1/ppt/theme/generate`), {
        method: "POST",
        headers: getHeader(),
        body: JSON.stringify(body),
      })
      return await ApiResponseHandler.handleResponse<GeneratedThemeColors>(response, "Failed to generate theme")
    }

    catch (error) {
      console.error("Error generating theme:", error)
      throw error
    }
  }
  static async uploadFont(font: File) {
    try {
      const formData = new FormData();
      formData.append("font_file", font);
      const response = await fetch(getApiUrl(`/api/v1/ppt/fonts/upload`), {
        method: "POST",
        headers: getHeaderForFormData(),
        body: formData,
      })
      return await ApiResponseHandler.handleResponse<UploadedFontResponse>(response, "Failed to upload font")
    }
    catch (error) {
      console.error("Error uploading font:", error)
      throw error
    }
  }
  static async getUserFonts() {
    try {
      const response = await fetch(getApiUrl(`/api/v1/ppt/fonts/uploaded`), {
        method: "GET",
        headers: getHeader(),
      })
      return await ApiResponseHandler.handleResponse<UserFontsResponse>(response, "Failed to get user fonts")
    }
    catch (error) {
      console.error("Error getting user fonts:", error)
      throw error
    }
  }
}

export default ThemeApi
