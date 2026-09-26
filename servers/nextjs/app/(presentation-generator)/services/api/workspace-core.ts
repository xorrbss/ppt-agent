export const ROOT = "/api/v1/ppt/workspace";

export class WorkspaceApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly retryable?: boolean,
  ) {
    super(message);
    this.name = "WorkspaceApiError";
  }
}

export async function parseResponse(response: Response): Promise<unknown> {
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const raw =
      payload && typeof payload === "object"
        ? (payload as Record<string, unknown>)
        : {};
    const detail =
      raw.detail && typeof raw.detail === "object"
        ? (raw.detail as Record<string, unknown>)
        : {};
    const envelopeError =
      raw.error && typeof raw.error === "object"
        ? (raw.error as Record<string, unknown>)
        : {};
    const retryable =
      typeof envelopeError.retryable === "boolean"
        ? envelopeError.retryable
        : typeof detail.retryable === "boolean"
          ? detail.retryable
          : undefined;
    const safeMessage =
      (typeof envelopeError.safe_message === "string" &&
        envelopeError.safe_message) ||
      (typeof detail.safe_message === "string" && detail.safe_message) ||
      (typeof raw.safe_message === "string" && raw.safe_message);
    throw new WorkspaceApiError(
      safeMessage || `Workspace request failed (${response.status})`,
      response.status,
      typeof raw.code === "string"
        ? raw.code
        : typeof envelopeError.code === "string"
          ? envelopeError.code
          : typeof detail.code === "string"
            ? detail.code
            : typeof raw.detail === "string"
              ? raw.detail
              : undefined,
      retryable,
    );
  }
  return payload;
}

