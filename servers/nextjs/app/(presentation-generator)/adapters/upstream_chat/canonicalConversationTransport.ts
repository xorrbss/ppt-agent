import { buildAbsoluteApiRequestUrl, getApiUrl } from "@/utils/api";
import { getHeader, getHeaderForFormData } from "../../services/api/header";
import { parseUnifiedConversationEvent } from "@/lib/unified-conversation/event-contract";
import type {
  AgentRun,
  Conversation,
  ConversationAsset,
  ConversationEvent,
  ConversationRunAction,
  ConversationTimeline,
  ConversationTimelineItem,
  ConversationTransport,
  SendConversationMessageRequest,
} from "../../presentation/conversation/types";

const ROOT = "/api/v1/ppt/conversations";
type RecordValue = Record<string, unknown>;

export class CanonicalConversationTransportError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly retryable?: boolean,
  ) {
    super(message);
    this.name = "CanonicalConversationTransportError";
  }
}

function record(value: unknown): RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
}
function text(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function number(value: unknown): number | undefined { return typeof value === "number" && Number.isFinite(value) ? value : undefined; }
function array(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }

async function payload(response: Response): Promise<unknown> {
  const raw = await response.text();
  let body: unknown = {};
  try { body = raw ? JSON.parse(raw) : {}; } catch { /* non-JSON errors stay opaque */ }
  if (!response.ok) {
    const source = record(body); const detail = record(source.detail); const error = record(source.error);
    throw new CanonicalConversationTransportError(
      text(error.safe_message, text(detail.safe_message, text(source.safe_message, `Conversation request failed (${response.status})`))),
      response.status,
      text(error.code, text(detail.code, typeof source.detail === "string" ? source.detail : text(source.code))) || undefined,
      typeof error.retryable === "boolean" ? error.retryable : typeof detail.retryable === "boolean" ? detail.retryable : undefined,
    );
  }
  const source = record(body);
  return source.data ?? body;
}

function asset(value: unknown): ConversationAsset {
  const raw = record(value);
  return {
    id: text(raw.id, text(raw.asset_id)),
    name: text(raw.name, text(raw.file_name, "Attachment")),
    status: text(raw.current_state, text(raw.status, "unknown")),
    mimeType: typeof raw.mime_type === "string" ? raw.mime_type : undefined,
    sizeBytes: number(raw.size_bytes),
    downloadUrl:
      typeof raw.download_url === "string" ? raw.download_url : undefined,
  };
}
function run(value: unknown): AgentRun {
  const raw = record(value);
  const revision = record(raw.revision);
  const actions = array(raw.allowed_actions ?? raw.actions).flatMap((action) => {
    if (typeof action === "string") return [action];
    const candidate = record(action);
    return candidate.enabled === false ? [] : [text(candidate.id)];
  }).filter((action): action is ConversationRunAction => ["retry", "cancel", "approve", "reject", "undo"].includes(action));
  return { id: text(raw.id, text(raw.run_id)), status: text(raw.status, "queued") as AgentRun["status"], title: typeof raw.title === "string" ? raw.title : typeof raw.run_type === "string" ? raw.run_type : undefined, version: number(raw.version), error: typeof raw.safe_error_message === "string" ? raw.safe_error_message : typeof raw.error === "string" ? raw.error : undefined, expectedRevision: number(raw.expected_revision) ?? number(revision.source_revision), resultRevision: number(raw.result_revision) ?? number(revision.result_revision), actions };
}
function item(value: unknown, fallbackPosition: number): ConversationTimelineItem | null {
  const raw = record(value); const kind = text(raw.kind, text(raw.item_type, text(raw.anchor_type))); const position = number(raw.position) ?? fallbackPosition;
  if (kind === "message" || raw.role) return { kind: "message", position, message: { id: text(raw.id, text(raw.message_id)), role: text(raw.role, "assistant") as "user" | "assistant" | "system", content: text(raw.content), createdAt: typeof raw.created_at === "string" ? raw.created_at : undefined, attachments: array(raw.attachments ?? raw.assets).map(asset) } };
  if (kind === "run" || kind === "operation" || raw.run_id || raw.operation_id) return { kind: "run", position, run: run(raw.run ?? raw) };
  if (kind === "tool" || raw.tool) { const tool = record(raw.tool ?? raw); return { kind: "tool", position, tool: { id: text(tool.id), runId: text(tool.run_id), name: text(tool.name, "Tool"), status: text(tool.status, "started") as "started" | "completed", summary: typeof tool.summary === "string" ? tool.summary : undefined } }; }
  return null;
}
function timeline(value: unknown): ConversationTimeline {
  const raw = record(value);
  const items = array(raw.items).map((entry, index) => item(entry, index)).filter((entry): entry is ConversationTimelineItem => entry !== null);
  return { items, revision: number(raw.revision), cursor: typeof raw.cursor === "string" ? raw.cursor : typeof raw.event_high_watermark === "string" ? raw.event_high_watermark : undefined, hasMore: raw.has_more === true, nextBeforePosition: number(raw.next_before_position) };
}
function conversation(value: unknown): Conversation {
  const raw = record(value);
  return { id: text(raw.id, text(raw.conversation_id)), presentationId: text(raw.presentation_id), revision: number(raw.revision) };
}
function contextWire(value: SendConversationMessageRequest["context"]): RecordValue | undefined {
  if (!value) return undefined;
  return {
    ...(value.viewport ? { viewport: {
      ...(value.viewport.width !== undefined ? { width: value.viewport.width } : {}),
      ...(value.viewport.height !== undefined ? { height: value.viewport.height } : {}),
      ...(value.viewport.zoom !== undefined ? { zoom: value.viewport.zoom } : {}),
      ...(value.viewport.visibleSlideIds?.length ? { visible_slide_ids: value.viewport.visibleSlideIds } : {}),
    } } : {}),
    ...(value.currentSlide ? { current_slide: {
      ...(value.currentSlide.slideId ? { slide_id: value.currentSlide.slideId } : {}),
      index: value.currentSlide.index,
    } } : {}),
  };
}
function event(value: unknown, sseId: string): ConversationEvent | undefined {
  // Fail closed: the shared event parser rejects provider/private payload keys.
  const envelope = parseUnifiedConversationEvent(value);
  if (!envelope) return undefined;
  const data = envelope.data;
  const nestedMessage = data.message ? item({ kind: "message", ...record(data.message) }, 0) : null;
  const message = nestedMessage?.kind === "message"
    ? nestedMessage.message
    : envelope.type === "message.created" && envelope.message_id && typeof data.content === "string"
      ? { id: envelope.message_id, role: text(data.role, "assistant") as "user" | "assistant" | "system", content: text(data.content), attachments: array(data.attachments).map(asset) }
      : undefined;
  const eventRun = data.run
    ? run(data.run)
    : envelope.run_id && typeof data.status === "string"
      ? { id: envelope.run_id, status: data.status as AgentRun["status"], error: typeof data.safe_message === "string" ? data.safe_message : typeof data.error === "string" ? data.error : undefined }
      : undefined;
  return {
    id: envelope.id || sseId,
    cursor: envelope.cursor ?? undefined,
    type: envelope.type,
    runId: envelope.run_id ?? undefined,
    messageId: envelope.message_id ?? undefined,
    status: typeof data.status === "string" ? data.status as AgentRun["status"] : undefined,
    delta: typeof data.delta === "string" ? data.delta : undefined,
    checkpoint: typeof data.content === "string" && envelope.type === "assistant.checkpoint" ? data.content : undefined,
    message,
    run: eventRun,
    tool: data.tool ? { id: text(record(data.tool).id, `${envelope.run_id ?? "run"}:${typeof data.tool === "string" ? data.tool : text(record(data.tool).name, "tool")}`), runId: envelope.run_id ?? "", name: typeof data.tool === "string" ? data.tool : text(record(data.tool).name, "Tool"), status: envelope.type === "tool.completed" ? "completed" : "started", summary: typeof data.summary === "string" ? data.summary : undefined } : undefined,
    revision: number(data.revision),
  };
}

function streamError(value: unknown): CanonicalConversationTransportError {
  const source = record(value); const error = record(source.error);
  const code = text(error.code, text(source.code));
  return new CanonicalConversationTransportError(
    text(error.safe_message, text(source.safe_message, "Conversation event stream failed")),
    code === "event_cursor_expired" ? 410 : 503,
    code || undefined,
    typeof error.retryable === "boolean" ? error.retryable : undefined,
  );
}

async function consumeSse(body: ReadableStream<Uint8Array>, onEvent: (next: ConversationEvent) => void): Promise<void> {
  const reader = body.getReader(); const decoder = new TextDecoder(); let buffer = ""; let lastId = "";
  while (true) {
    const part = await reader.read();
    if (part.done) return;
    buffer += decoder.decode(part.value, { stream: true });
    let boundary: RegExpMatchArray | null;
    while ((boundary = buffer.match(/\r?\n\r?\n/u)) !== null && boundary.index !== undefined) {
      const block = buffer.slice(0, boundary.index); buffer = buffer.slice(boundary.index + boundary[0].length);
      const eventName = block.split(/\r?\n/u).find((line) => line.startsWith("event:"))?.slice(6).trim();
      const data = block.split(/\r?\n/).filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trim()).join("\n");
      const idLine = block.split(/\r?\n/).find((line) => line.startsWith("id:")); if (idLine) lastId = idLine.slice(3).trim();
      if (!data) continue;
      let decoded: unknown;
      try { decoded = JSON.parse(data); } catch { continue; }
      if (eventName === "stream_error") throw streamError(decoded);
      const next = event(decoded, lastId); if (next) onEvent(next);
    }
  }
}

/** Anti-corruption layer: only this file knows the canonical HTTP wire shape. */
export const canonicalConversationTransport: ConversationTransport = {
  async getOrCreate(presentationId, signal) {
    const response = await fetch(getApiUrl(ROOT), { method: "POST", headers: { ...getHeader(), "Content-Type": "application/json" }, body: JSON.stringify({ presentation_id: presentationId }), signal });
    return conversation(await payload(response));
  },
  async getTimeline(conversationId, options = {}) {
    const url = new URL(buildAbsoluteApiRequestUrl(`${ROOT}/${encodeURIComponent(conversationId)}/timeline`));
    if (options.beforePosition !== undefined) url.searchParams.set("before_position", String(options.beforePosition));
    const response = await fetch(url, { headers: getHeader(), signal: options.signal, cache: "no-store" }); return timeline(await payload(response));
  },
  async sendMessage(conversationId, request: SendConversationMessageRequest, signal) {
    const response = await fetch(getApiUrl(`${ROOT}/${encodeURIComponent(conversationId)}/messages`), { method: "POST", headers: { ...getHeader(), "Content-Type": "application/json", "Idempotency-Key": request.idempotencyKey }, body: JSON.stringify({ content: request.content, idempotency_key: request.idempotencyKey, expected_revision: request.expectedRevision, context: contextWire(request.context), asset_ids: request.assetIds, ...(request.commandType ? { command_type: request.commandType } : {}), ...(request.operationContext ? { operation_context: request.operationContext } : {}) }), signal });
    await payload(response);
    return this.getTimeline(conversationId, { signal });
  },
  async performRunAction(conversationId, runId, action, request, signal) {
    const response = await fetch(getApiUrl(`${ROOT}/${encodeURIComponent(conversationId)}/runs/${encodeURIComponent(runId)}/actions`), { method: "POST", headers: { ...getHeader(), "Content-Type": "application/json" }, body: JSON.stringify({ action, idempotency_key: request.idempotencyKey, expected_revision: request.expectedRevision, expected_run_version: request.expectedRunVersion }), signal });
    await payload(response);
    return this.getTimeline(conversationId, { signal });
  },
  async streamEvents(conversationId, options) {
    const url = new URL(buildAbsoluteApiRequestUrl(`${ROOT}/${encodeURIComponent(conversationId)}/events`)); url.searchParams.set("stream", "1"); if (options.after) url.searchParams.set("after", options.after);
    const response = await fetch(url, { headers: { ...getHeader(), Accept: "text/event-stream", ...(options.after ? { "Last-Event-ID": options.after } : {}) }, signal: options.signal, cache: "no-store" });
    if (!response.ok || !response.body) await payload(response); if (!response.body) throw new Error("Conversation event stream is unavailable");
    await consumeSse(response.body, options.onEvent);
  },
  async uploadAsset(conversationId, file, signal) {
    const form = new FormData(); form.append("file", file, file.name);
    const response = await fetch(getApiUrl(`${ROOT}/${encodeURIComponent(conversationId)}/assets`), { method: "POST", headers: getHeaderForFormData(), body: form, signal }); return asset(await payload(response));
  },
};
