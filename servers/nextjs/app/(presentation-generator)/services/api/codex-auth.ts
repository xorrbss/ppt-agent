import { getApiUrl } from "@/utils/api";
import { ApiResponseHandler } from "./api-error-handler";

type CodexAuthStatus = {
  status?: string;
};

export class CodexAuthApi {
  static async refreshIfExpired(): Promise<boolean> {
    const statusResponse = await fetch(
      getApiUrl("/api/v1/ppt/codex/auth/status"),
      { cache: "no-store" },
    );
    const status = (await ApiResponseHandler.handleResponse(
      statusResponse,
      "Failed to check the ChatGPT connection",
    )) as CodexAuthStatus;
    if (status.status !== "expired") return false;

    await CodexAuthApi.refresh();
    return true;
  }

  static async refresh(): Promise<void> {
    const response = await fetch(
      getApiUrl("/api/v1/ppt/codex/auth/refresh"),
      {
        method: "POST",
        cache: "no-store",
      },
    );
    await ApiResponseHandler.handleResponse(
      response,
      "Failed to refresh the ChatGPT connection",
    );
  }
}
