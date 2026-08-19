import { getApiUrl } from "@/utils/api";

import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";

/** The parent fan-out task; its id is what the dialog polls for progress. */
export type AuthoredBatchRemakePreviewTask = {
  id?: string;
  status?: string;
  message?: string | null;
};

/**
 * Starts one backend fan-out task for every selected page. The batch lives in
 * its own client module because `presentation-generation.ts` is frozen at its
 * file-size baseline.
 */
export async function previewAuthoredPageRemakeBatch(
  presentationId: string,
  slideIndexes: number[],
  instruction?: string | null,
): Promise<AuthoredBatchRemakePreviewTask> {
  const response = await fetch(
    getApiUrl(
      `/api/v1/ppt/presentation/${presentationId}/slides/remake/preview-batch`,
    ),
    {
      method: "POST",
      headers: getHeader(),
      body: JSON.stringify({
        slide_indexes: slideIndexes,
        instruction: instruction ?? null,
      }),
    },
  );
  return ApiResponseHandler.handleResponse(
    response,
    "Failed to start batch page remake preview",
  );
}
