import { buildAbsoluteApiRequestUrl, getApiUrl } from "@/utils/api";
import { getHeader } from "./header";
import {
  normalizeEventPage,
  normalizeOperationEvent,
  normalizeTimelinePage,
} from "../../presentation/workspace/timelineProjection";
import type {
  WorkspaceCapability,
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
    const raw = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
    const detail =
      raw.detail && typeof raw.detail === "object"
        ? (raw.detail as Record<string, unknown>)
        : {};
    const envelopeError =
      raw.error && typeof raw.error === "object"
        ? (raw.error as Record<string, unknown>)
        : {};
    throw new WorkspaceApiError(
      (typeof raw.detail === "string" && raw.detail) ||
        (typeof envelopeError.safe_message === "string" && envelopeError.safe_message) ||
        (typeof envelopeError.message === "string" && envelopeError.message) ||
        (typeof detail.safe_message === "string" && detail.safe_message) ||
        (typeof detail.message === "string" && detail.message) ||
        (typeof raw.message === "string" && raw.message) ||
        `Workspace request failed (${response.status})`,
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
    );
  }
  return payload;
}

export async function getWorkspaceCapabilities(signal?: AbortSignal): Promise<WorkspaceCapability> {
  try {
    const response = await fetch(getApiUrl(`${ROOT}/capabilities`), {
      headers: getHeader(),
      signal,
      cache: "no-store",
    });
    if (response.status === 404 || response.status === 501) {
      return { supported: false, timeline: false, commands: false, events: false, reason: "unsupported" };
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
      reason: typeof data.reason === "string" ? data.reason : supported ? undefined : "disabled",
    };
  } catch (error) {
    if (error instanceof WorkspaceApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return { supported: false, timeline: false, commands: false, events: false, reason: "unavailable" };
  }
}

export async function getWorkspaceTimeline(
  presentationId: string,
  options: { beforePosition?: number; limit?: number; signal?: AbortSignal } = {},
): Promise<WorkspaceTimelinePage> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(`${ROOT}/presentations/${encodeURIComponent(presentationId)}/timeline`),
  );
  if (options.beforePosition !== undefined) url.searchParams.set("before_position", String(options.beforePosition));
  url.searchParams.set("limit", String(options.limit ?? 50));
  const response = await fetch(url, { headers: getHeader(), signal: options.signal, cache: "no-store" });
  return normalizeTimelinePage(await parseResponse(response));
}

export async function getWorkspaceEvents(
  presentationId: string,
  options: { after?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<WorkspaceEventPage> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(`${ROOT}/presentations/${encodeURIComponent(presentationId)}/events`),
  );
  if (options.after) url.searchParams.set("after", options.after);
  url.searchParams.set("limit", String(options.limit ?? 200));
  const response = await fetch(url, { headers: getHeader(), signal: options.signal, cache: "no-store" });
  return normalizeEventPage(await parseResponse(response));
}

export async function postWorkspaceCommand(
  presentationId: string,
  request: WorkspaceCommandRequest,
  signal?: AbortSignal,
): Promise<WorkspaceTimelinePage> {
  const response = await fetch(
    getApiUrl(`${ROOT}/presentations/${encodeURIComponent(presentationId)}/commands`),
    {
      method: "POST",
      headers: { ...getHeader(), "Content-Type": "application/json" },
      body: JSON.stringify({
        command: request.command,
        command_type: request.commandType,
        idempotency_key: request.idempotencyKey,
        expected_revision: request.expectedRevision,
        context: request.context,
      }),
      signal,
    },
  );
  return normalizeTimelinePage(await parseResponse(response));
}

export async function postWorkspaceOperationAction(
  operationId: string,
  request: WorkspaceOperationActionRequest,
  signal?: AbortSignal,
): Promise<WorkspaceTimelinePage> {
  const response = await fetch(getApiUrl(`${ROOT}/operations/${encodeURIComponent(operationId)}/actions`), {
    method: "POST",
    headers: { ...getHeader(), "Content-Type": "application/json" },
    body: JSON.stringify({
      action: request.action,
      idempotency_key: request.idempotencyKey,
      expected_revision: request.expectedRevision,
      expected_operation_version: request.expectedOperationVersion,
    }),
    signal,
  });
  return normalizeTimelinePage(await parseResponse(response));
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
    fileName: contentDispositionFileName(response.headers.get("content-disposition")),
  };
}

interface StreamOptions {
  after?: string;
  signal?: AbortSignal;
  onEvent: (event: WorkspaceOperationEvent, cursor?: string) => void;
}

export async function streamWorkspaceEvents(
  presentationId: string,
  options: StreamOptions,
): Promise<void> {
  const url = new URL(
    buildAbsoluteApiRequestUrl(`${ROOT}/presentations/${encodeURIComponent(presentationId)}/events`),
  );
  url.searchParams.set("stream", "1");
  if (options.after) url.searchParams.set("after", options.after);
  const headers: Record<string, string> = { ...getHeader() };
  headers.Accept = "text/event-stream";
  if (options.after) headers["Last-Event-ID"] = options.after;
  const response = await fetch(url, { headers, signal: options.signal, cache: "no-store" });
  if (!response.ok || !response.body) await parseResponse(response);
  if (!response.body) throw new WorkspaceApiError("Event stream is unavailable", response.status || 503);
  if (!response.headers.get("content-type")?.toLowerCase().includes("text/event-stream")) {
    throw new WorkspaceApiError("Event stream is unavailable", 503, "stream_unavailable");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.search(/\r?\n\r?\n/);
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary).replace(/^\r?\n\r?\n/, "");
      let id: string | undefined;
      const data: string[] = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("id:")) id = line.slice(3).trim();
        if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length > 0) {
        try {
          const event = normalizeOperationEvent(JSON.parse(data.join("\n")));
          if (event) options.onEvent(event, id ?? event.cursor);
        } catch {
          // A malformed/heartbeat frame cannot mutate the projection.
        }
      }
      boundary = buffer.search(/\r?\n\r?\n/);
    }
  }
}
