import { ApiError, isRetryablePresentationGenerationError } from "../../services/api/api-error-handler";

export const isAmbiguousGenerationTransportError = (error: unknown): boolean => {
  if (error instanceof TypeError) return true;
  if (
    typeof DOMException !== "undefined" &&
    error instanceof DOMException &&
    (error.name === "AbortError" || error.name === "NetworkError")
  ) {
    return true;
  }
  if (error instanceof ApiError) return error.status === 0;
  return Boolean(
    error &&
      typeof error === "object" &&
      (error as { status?: unknown }).status === 0
  );
};

export const isRetryableGenerationStartError = (error: unknown): boolean =>
  isAmbiguousGenerationTransportError(error) ||
  isRetryablePresentationGenerationError(error);

export const hasRecoverableChatFirstWorkspace = (error: unknown): boolean =>
  error instanceof ApiError && error.workspaceProjected;

export const isCompanyStandardAdmissionError = (error: unknown): boolean => {
  if (!(error instanceof ApiError)) return false;
  const value = `${error.code ?? ""} ${error.message}`.toLowerCase();
  return [
    "authored_vnext_stale_master",
    "authored_vnext_published_master_required",
    "authored_vnext_generation_not_found",
    "authored_vnext_master_disabled",
  ].some((code) => value.includes(code));
};

