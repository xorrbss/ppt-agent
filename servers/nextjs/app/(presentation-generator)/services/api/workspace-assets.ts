import { buildAbsoluteApiRequestUrl, getApiUrl } from "@/utils/api";
import { getHeader, getHeaderForFormData } from "./header";
import { ROOT, parseResponse, WorkspaceApiError } from "./workspace-core";
import { consumeWorkspaceSseStream, WorkspaceStreamError } from "../../presentation/workspace/workspaceStream";
import type { WorkspaceOperationEvent } from "../../presentation/workspace/types";

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
  conversationId?: string | null,
): Promise<WorkspaceUploadedAsset> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("presentation_id", presentationId);
  if (conversationId) form.append("conversation_id", conversationId);
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

export async function getWorkspaceAsset(
  assetId: string,
  conversationId?: string | null,
  signal?: AbortSignal,
): Promise<WorkspaceUploadedAsset> {
  const conversationQuery = conversationId
    ? `?conversation_id=${encodeURIComponent(conversationId)}`
    : "";
  const response = await fetch(
    getApiUrl(`${ROOT}/assets/${encodeURIComponent(assetId)}${conversationQuery}`),
    {
      method: "GET",
      headers: getHeader(),
      signal,
      cache: "no-store",
    },
  );
  const payload = (await parseResponse(response)) as Record<string, unknown>;
  const data =
    payload.data && typeof payload.data === "object"
      ? (payload.data as Record<string, unknown>)
      : payload;
  const resolvedId =
    typeof data.asset_id === "string" ? data.asset_id : data.id;
  if (
    typeof resolvedId !== "string" ||
    resolvedId.length === 0 ||
    resolvedId !== assetId
  ) {
    throw new WorkspaceApiError(
      "첨부 파일 상태를 확인하지 못했습니다.",
      502,
      "invalid_asset_response",
      false,
    );
  }
  return {
    assetId: resolvedId,
    fileName: typeof data.file_name === "string" ? data.file_name : "첨부 파일",
    mediaType:
      typeof data.media_type === "string" ? data.media_type : undefined,
    sizeBytes: typeof data.size_bytes === "number" ? data.size_bytes : 0,
    status: typeof data.status === "string" ? data.status : "uploaded",
  };
}
