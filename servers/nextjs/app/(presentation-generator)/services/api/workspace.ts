import { buildAbsoluteApiRequestUrl, getApiUrl } from "@/utils/api";
import { getHeader, getHeaderForFormData } from "./header";
import {
  normalizeEventPage,
  normalizeTimelinePage,
  normalizeWorkspaceActionReceipt,
} from "../../presentation/workspace/timelineProjection";
import {
  consumeWorkspaceSseStream,
  WorkspaceStreamError,
} from "../../presentation/workspace/workspaceStream";
import type {
  WorkspaceCapability,
  WorkspaceActionReceipt,
  WorkspaceCommandRequest,
  WorkspaceEventPage,
  WorkspaceOperationActionRequest,
  WorkspaceOperationEvent,
  WorkspaceTimelinePage,
} from "../../presentation/workspace/types";

const ROOT = "/api/v1/ppt/workspace";

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

async function parseResponse(response: Response): Promise<unknown> {
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

export async function getWorkspaceCapabilities(
  signal?: AbortSignal,
  presentationId?: string,
): Promise<WorkspaceCapability> {
  try {
    const url = new URL(buildAbsoluteApiRequestUrl(`${ROOT}/capabilities`));
    if (presentationId) url.searchParams.set("presentation_id", presentationId);
    const response = await fetch(url, {
      headers: getHeader(),
      signal,
      cache: "no-store",
    });
    if (response.status === 404 || response.status === 501) {
      return {
        supported: false,
        timeline: false,
        commands: false,
        events: false,
        reason: "unsupported",
      };
    }
    const payload = (await parseResponse(response)) as Record<string, unknown>;
    const data =
      payload.data && typeof payload.data === "object"
        ? (payload.data as Record<string, unknown>)
        : payload;
    const supported = data.supported === true;
    const enabled = data.enabled === true;
    return {
      supported,
      timeline: supported && data.timeline !== false,
      commands: supported && enabled && data.commands !== false,
      events: supported && data.events !== false,
      reason:
        typeof data.reason === "string"
          ? data.reason
          : supported
            ? undefined
            : "disabled",
    };
  } catch (error) {
    if (error instanceof WorkspaceApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    return {
      supported: false,
      timeline: false,
      commands: false,
      events: false,
      reason: "unavailable",
    };
  }
}

export async function getWorkspaceTimeline(
  presentationId: string,
  options: {
    beforePosition?: number;
    limit?: number;
    signal?: AbortSignal;
  } = {},
): Promise<WorkspaceTimelinePage> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/timeline`,
    ),
  );
  if (options.beforePosition !== undefined)
    url.searchParams.set("before_position", String(options.beforePosition));
  url.searchParams.set("limit", String(options.limit ?? 50));
  const response = await fetch(url, {
    headers: getHeader(),
    signal: options.signal,
    cache: "no-store",
  });
  return normalizeTimelinePage(await parseResponse(response));
}

export async function getWorkspaceEvents(
  presentationId: string,
  options: { after?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<WorkspaceEventPage> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/events`,
    ),
  );
  if (options.after) url.searchParams.set("after", options.after);
  url.searchParams.set("limit", String(options.limit ?? 200));
  const response = await fetch(url, {
    headers: getHeader(),
    signal: options.signal,
    cache: "no-store",
  });
  return normalizeEventPage(await parseResponse(response));
}

export async function postWorkspaceCommand(
  presentationId: string,
  request: WorkspaceCommandRequest,
  signal?: AbortSignal,
): Promise<WorkspaceActionReceipt> {
  const response = await fetch(
    getApiUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/commands`,
    ),
    {
      method: "POST",
      headers: { ...getHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({
        command: request.command,
        command_type: request.commandType,
        idempotency_key: request.idempotencyKey,
        expected_revision: request.expectedRevision,
        context: request.context,
        asset_ids: request.assetIds,
      }),
      signal,
    },
  );
  return normalizeWorkspaceActionReceipt(await parseResponse(response));
}

export async function postWorkspaceOperationAction(
  operationId: string,
  request: WorkspaceOperationActionRequest,
  signal?: AbortSignal,
): Promise<WorkspaceActionReceipt> {
  const response = await fetch(
    getApiUrl(`${ROOT}/operations/${encodeURIComponent(operationId)}/actions`),
    {
      method: "POST",
      headers: { ...getHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({
        action: request.action,
        idempotency_key: request.idempotencyKey,
        expected_revision: request.expectedRevision,
        expected_operation_version: request.expectedOperationVersion,
      }),
      signal,
    },
  );
  return normalizeWorkspaceActionReceipt(await parseResponse(response));
}

export interface WorkspaceArtifactDownload {
  blob: Blob;
  fileName?: string;
}

function contentDispositionFileName(value: string | null): string | undefined {
  if (!value) return undefined;
  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  const quotedMatch = value.match(/filename="([^"]+)"/i);
  const plainMatch = value.match(/filename=([^;]+)/i);
  const raw = utf8Match?.[1] ?? quotedMatch?.[1] ?? plainMatch?.[1];
  if (!raw) return undefined;
  let decoded = raw.trim();
  try {
    decoded = decodeURIComponent(decoded);
  } catch {
    // Keep the server-provided fallback when it is not percent encoded.
  }
  const safe = decoded.replace(/[\\/\u0000-\u001f\u007f]/g, "_").trim();
  return safe || undefined;
}

export async function downloadWorkspaceArtifact(
  artifactId: string,
  signal?: AbortSignal,
): Promise<WorkspaceArtifactDownload> {
  const response = await fetch(
    getApiUrl(`${ROOT}/artifacts/${encodeURIComponent(artifactId)}/download`),
    {
      headers: { Accept: "application/octet-stream" },
      credentials: "include",
      signal,
      cache: "no-store",
    },
  );
  if (!response.ok) await parseResponse(response);
  return {
    blob: await response.blob(),
    fileName: contentDispositionFileName(
      response.headers.get("content-disposition"),
    ),
  };
}

interface StreamOptions {
  after?: string;
  signal?: AbortSignal;
  onEvent: (
    event: WorkspaceOperationEvent,
    cursor?: string,
  ) => void | Promise<void>;
}

export async function streamWorkspaceEvents(
  presentationId: string,
  options: StreamOptions,
): Promise<void> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(
      `${ROOT}/presentations/${encodeURIComponent(presentationId)}/events`,
    ),
  );
  url.searchParams.set("stream", "1");
  if (options.after) url.searchParams.set("after", options.after);
  const headers: Record<string, string> = { ...getHeader() };
  headers.Accept = "text/event-stream";
  if (options.after) headers["Last-Event-ID"] = options.after;
  const response = await fetch(url, {
    headers,
    signal: options.signal,
    cache: "no-store",
  });
  if (!response.ok || !response.body) await parseResponse(response);
  if (!response.body)
    throw new WorkspaceApiError(
      "Event stream is unavailable",
      response.status || 503,
    );
  if (
    !response.headers
      .get("content-type")
      ?.toLowerCase()
      .includes("text/event-stream")
  ) {
    throw new WorkspaceApiError(
      "Event stream is unavailable",
      503,
      "stream_unavailable",
    );
  }

  try {
    await consumeWorkspaceSseStream(response.body, options.onEvent);
  } catch (error: unknown) {
    if (!(error instanceof WorkspaceStreamError)) throw error;
    const cursorExpired =
      error.code === "cursor_expired" || error.code === "event_cursor_expired";
    throw new WorkspaceApiError(
      error.message,
      cursorExpired ? 410 : 503,
      error.code,
      error.retryable,
    );
  }
}

export interface WorkspaceUploadedAsset {
  assetId: string;
  fileName: string;
  mediaType?: string;
  sizeBytes: number;
  status: string;
}

export async function postWorkspaceAsset(
  file: File,
  presentationId: string,
  signal?: AbortSignal,
): Promise<WorkspaceUploadedAsset> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("presentation_id", presentationId);
  const response = await fetch(getApiUrl(`${ROOT}/assets`), {
    method: "POST",
    headers: getHeaderForFormData(),
    body: form,
    signal,
  });
  const payload = (await parseResponse(response)) as Record<string, unknown>;
  const data =
    payload.data && typeof payload.data === "object"
      ? (payload.data as Record<string, unknown>)
      : payload;
  const assetId = typeof data.asset_id === "string" ? data.asset_id : data.id;
  if (typeof assetId !== "string" || assetId.length === 0) {
    throw new WorkspaceApiError(
      "업로드 결과에서 자산 ID를 확인하지 못했습니다.",
      502,
      "invalid_asset_response",
      false,
    );
  }
  return {
    assetId,
    fileName: typeof data.file_name === "string" ? data.file_name : file.name,
    mediaType:
      typeof data.media_type === "string"
        ? data.media_type
        : file.type || undefined,
    sizeBytes:
      typeof data.size_bytes === "number" ? data.size_bytes : file.size,
    status: typeof data.status === "string" ? data.status : "uploaded",
  };
}
