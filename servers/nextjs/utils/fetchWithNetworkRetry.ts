export type FetchWithNetworkRetryOptions = {
  maxAttempts?: number;
  retryDelaysMs?: readonly number[];
  fetchImpl?: typeof fetch;
  sleep?: (delayMs: number) => Promise<void>;
};

const DEFAULT_RETRY_DELAYS_MS = [250, 750, 1500] as const;

function isRetryableNetworkError(error: unknown, signal?: AbortSignal | null) {
  return error instanceof TypeError && !signal?.aborted;
}

export async function fetchWithNetworkRetry(
  input: RequestInfo | URL,
  init: RequestInit = {},
  options: FetchWithNetworkRetryOptions = {}
): Promise<Response> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const retryDelaysMs =
    options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
  const maxAttempts = Math.max(
    1,
    options.maxAttempts ?? retryDelaysMs.length + 1
  );
  const sleep =
    options.sleep ??
    ((delayMs: number) =>
      new Promise<void>((resolve) => setTimeout(resolve, delayMs)));

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      return await fetchImpl(input, init);
    } catch (error) {
      const hasAnotherAttempt = attempt + 1 < maxAttempts;
      if (!hasAnotherAttempt || !isRetryableNetworkError(error, init.signal)) {
        throw error;
      }

      const delayMs =
        retryDelaysMs[Math.min(attempt, retryDelaysMs.length - 1)] ?? 0;
      if (delayMs > 0) {
        await sleep(delayMs);
      }
    }
  }

  throw new TypeError("Network request failed after all retry attempts");
}
