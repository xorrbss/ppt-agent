export type PresentationSortDirection = "asc" | "desc";

export interface PresentationListOptions {
  q?: string;
  page?: number;
  pageSize?: number;
  sort?: PresentationSortDirection;
}

export interface PresentationListEnvelope<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export function isPresentationListRequestAbort(
  error: unknown,
  signal?: AbortSignal,
): boolean {
  if (signal?.aborted) return true;
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

export function buildPresentationListPath(
  options: PresentationListOptions = {},
): string {
  const params = new URLSearchParams();
  const query = options.q?.trim();
  if (query) params.set("q", query);
  if (options.page !== undefined) params.set("page", String(options.page));
  if (options.pageSize !== undefined) {
    params.set("page_size", String(options.pageSize));
  }
  if (options.sort !== undefined) params.set("sort", options.sort);
  const suffix = params.toString();
  return `/api/v1/ppt/presentation/all${suffix ? `?${suffix}` : ""}`;
}

export function normalizePresentationListEnvelope<T>(
  payload: T[] | PresentationListEnvelope<T>,
  options: PresentationListOptions = {},
): PresentationListEnvelope<T> {
  if (!Array.isArray(payload)) return payload;
  return {
    items: payload,
    total: payload.length,
    page: options.page ?? 1,
    page_size: options.pageSize ?? Math.max(payload.length, 1),
  };
}
