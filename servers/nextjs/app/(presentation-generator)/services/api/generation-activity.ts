import { getApiUrl } from "@/utils/api";
import { getHeader } from "./header";
import { ROOT, parseResponse } from "./workspace-core";
import { normalizeGenerationActivity, type GenerationActivitySnapshot } from "./generation-activity-model";

export * from "./generation-activity-model";

/** The signed-in owner's running and queued `presentation.generate` operations. */
export async function getGenerationActivity(
  signal?: AbortSignal,
): Promise<GenerationActivitySnapshot> {
  const response = await fetch(getApiUrl(`${ROOT}/generation-activity`), {
    headers: getHeader(),
    signal,
    cache: "no-store",
  });
  return normalizeGenerationActivity(await parseResponse(response));
}
