import { getApiUrl } from "@/utils/api";

/**
 * Ask the backend which models an OpenAI-compatible endpoint serves.
 * Returns the raw response so each screen keeps its own error handling.
 */
export function openaiAvailableModels(params: {
  url?: string;
  apiKey?: string;
}): Promise<Response> {
  return fetch(getApiUrl("/api/v1/ppt/openai/models/available"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: params.url, api_key: params.apiKey }),
  });
}
