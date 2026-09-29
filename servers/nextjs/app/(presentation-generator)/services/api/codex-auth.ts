import { getApiUrl } from "@/utils/api";
import { ApiResponseHandler } from "./api-error-handler";

export type CodexAuthStatus = {
  status?: string;
};

const CODEX_AUTH_STATUS_PATH = "/api/v1/ppt/codex/auth/status";

/**
 * Current ChatGPT (Codex) sign-in status. Resolves to the parsed payload, or
 * null when the backend answers non-2xx or cannot be reached, so callers can
 * treat every failure as "signed out".
 */
export async function codexAuthStatus<
  T extends CodexAuthStatus = CodexAuthStatus,
>(): Promise<T | null> {
  try {
    const response = await fetch(getApiUrl(CODEX_AUTH_STATUS_PATH), {
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export async function isCodexAuthenticated(): Promise<boolean> {
  return (await codexAuthStatus())?.status === "authenticated";
}

export class CodexAuthApi {
  static async refreshIfExpired(): Promise<boolean> {
    const statusResponse = await fetch(
      getApiUrl(CODEX_AUTH_STATUS_PATH),
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
