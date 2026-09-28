import { getApiUrl } from "@/utils/api";
import { LLMConfig } from "@/types/llm_config";

export interface OllamaModel {
  label: string;
  value: string;
  size: string;
}

export interface DownloadingModel {
  name: string;
  size: number | null;
  downloaded: number | null;
  status: string;
  done: boolean;
  error?: string | null;
}

export interface OllamaModelsResult {
  models: OllamaModel[];
  updatedConfig?: LLMConfig;
}

export const checkIfSelectedOllamaModelIsPulled = async (ollamaModel: string) => {
  try {
    const response = await fetch(getApiUrl('/api/v1/ppt/ollama/models/available'));
    // GET /ollama/models/available → List[OllamaModelStatus], the same shape as
    // DownloadingModel.
    const models: DownloadingModel[] = await response.json();
    const pulledModels = models.map((model) => model.name);
    return pulledModels.includes(ollamaModel);
  } catch (error) {
    console.error('Error checking if selected Ollama model is pulled:', error);
    return false;
  }
}


/**
 * Resets downloading model state
 */
export const resetDownloadingModel = (): DownloadingModel => ({
  name: "",
  size: null,
  downloaded: null,
  status: "",
  done: false,
});

function abortPullError(): Error {
  const err = new Error("Download cancelled");
  err.name = "AbortError";
  return err;
}

function isAbortError(e: unknown): boolean {
  return e instanceof Error && e.name === "AbortError";
}

async function getPullErrorMessage(
  response: Response,
  fallback: string
): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
    if (typeof body?.error === "string" && body.error.trim()) {
      return body.error;
    }
  } catch {
    // Ignore parse errors and use fallback.
  }
  return fallback;
}

/**
 * Pulls Ollama model with progress tracking.
 * Pass an AbortSignal to stop polling (e.g. user cancels download).
 */
export const pullOllamaModel = async (
  model: string,
  onProgress?: (model: DownloadingModel) => void,
  signal?: AbortSignal
): Promise<DownloadingModel> => {
  return new Promise((resolve, reject) => {
    let interval: ReturnType<typeof setInterval> | null = null;
    let settled = false;
    let polling = false;

    const cleanup = () => {
      if (interval !== null) {
        clearInterval(interval);
        interval = null;
      }
      signal?.removeEventListener("abort", onAbort);
    };

    const onAbort = () => {
      if (settled) return;
      settled = true;
      cleanup();
      onProgress?.(resetDownloadingModel());
      reject(abortPullError());
    };

    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener("abort", onAbort);

    const pollOnce = async () => {
      if (settled || polling) {
        return;
      }

      if (signal?.aborted) {
        onAbort();
        return;
      }

      polling = true;
      try {
        const response = await fetch(
          getApiUrl(`/api/v1/ppt/ollama/model/pull?model=${model}`)
        );
        if (settled) return;
        if (response.status === 200) {
          const data = await response.json();
          if (data.done && data.status !== "error") {
            if (settled) return;
            settled = true;
            cleanup();
            onProgress?.(data);
            resolve(data);
          } else if (data.status === "error" || data.error) {
            if (settled) return;
            settled = true;
            cleanup();
            onProgress?.(resetDownloadingModel());
            reject(new Error(data.error || "Error occurred while pulling model"));
          } else {
            onProgress?.(data);
          }
        } else {
          if (settled) return;
          settled = true;
          cleanup();
          onProgress?.(resetDownloadingModel());
          if (response.status === 403) {
            reject(new Error("Request to Ollama Not Authorized"));
          } else {
            const errorMessage = await getPullErrorMessage(
              response,
              "Error occurred while pulling model"
            );
            reject(new Error(errorMessage));
          }
        }
      } catch (error) {
        if (settled) return;
        if (isAbortError(error)) {
          return;
        }
        settled = true;
        cleanup();
        onProgress?.(resetDownloadingModel());
        reject(error);
      } finally {
        polling = false;
      }
    };

    void pollOnce();
    interval = setInterval(() => {
      void pollOnce();
    }, 1000);
  });
};
