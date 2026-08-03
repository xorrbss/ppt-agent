import { normalizeCursorOperationEvent } from "./timelineProjection.ts";
import type { WorkspaceOperationEvent } from "./types.ts";

export type WorkspaceSseFrame =
  | { kind: "workspace_event"; event: WorkspaceOperationEvent; cursor: string }
  | { kind: "heartbeat" }
  | {
      kind: "stream_error";
      code: string;
      message?: string;
      retryable?: boolean;
    };

export class WorkspaceStreamError extends Error {
  readonly code: string;
  readonly retryable?: boolean;

  constructor(message: string, code: string, retryable?: boolean) {
    super(message);
    this.name = "WorkspaceStreamError";
    this.code = code;
    this.retryable = retryable;
  }
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Parse one complete SSE frame without mutating a projection or cursor. */
export function parseWorkspaceSseFrame(block: string): WorkspaceSseFrame {
  let eventType: string | undefined;
  let id: string | undefined;
  const data: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    else if (line.startsWith("id:")) id = line.slice(3).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }

  const meaningfulLines = block
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0 && !line.startsWith(":"));
  if (eventType === "heartbeat" || meaningfulLines.length === 0)
    return { kind: "heartbeat" };
  if (eventType !== "workspace_event" && eventType !== "stream_error") {
    return {
      kind: "stream_error",
      code: "stream_protocol_error",
      retryable: true,
    };
  }

  let payload: unknown;
  try {
    payload = JSON.parse(data.join("\n"));
  } catch {
    return {
      kind: "stream_error",
      code: "stream_protocol_error",
      retryable: true,
    };
  }

  if (eventType === "stream_error") {
    const raw = recordValue(payload);
    const rawError = recordValue(raw.error);
    return {
      kind: "stream_error",
      code:
        typeof rawError.code === "string"
          ? rawError.code
          : typeof raw.code === "string"
            ? raw.code
            : "stream_error",
      // Only the explicitly public contract field may reach live UI. In
      // particular, never render a provider/internal `message` fallback.
      message:
        typeof rawError.safe_message === "string"
          ? rawError.safe_message
          : typeof raw.safe_message === "string"
            ? raw.safe_message
            : undefined,
      retryable:
        typeof rawError.retryable === "boolean"
          ? rawError.retryable
          : typeof raw.retryable === "boolean"
            ? raw.retryable
            : undefined,
    };
  }

  const event = normalizeCursorOperationEvent(payload);
  return event && id !== undefined && id === event.cursor
    ? { kind: "workspace_event", event, cursor: id }
    : { kind: "stream_error", code: "stream_protocol_error", retryable: true };
}

export function workspaceReconnectDelay(
  failureCount: number,
  random: () => number = Math.random,
): number {
  const exponential = Math.min(
    10_000,
    500 * 2 ** Math.max(0, failureCount - 1),
  );
  return Math.round(
    exponential * (0.75 + Math.max(0, Math.min(1, random())) * 0.5),
  );
}

export class WorkspaceReconnectBackoff {
  #disconnectCount = 0;

  recordDisconnect(random: () => number = Math.random): {
    disconnectCount: number;
    delayMs: number;
  } {
    this.#disconnectCount += 1;
    return {
      disconnectCount: this.#disconnectCount,
      delayMs: workspaceReconnectDelay(this.#disconnectCount, random),
    };
  }

  resetAfterRebase(): void {
    this.#disconnectCount = 0;
  }
}

export async function consumeWorkspaceSseStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (
    event: WorkspaceOperationEvent,
    cursor?: string,
  ) => void | Promise<void>,
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      if (buffer.trim().length > 0) {
        throw new WorkspaceStreamError(
          "Event stream ended with an incomplete frame",
          "stream_protocol_error",
          true,
        );
      }
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.search(/\r?\n\r?\n/);
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary).replace(/^\r?\n\r?\n/, "");
      const frame = parseWorkspaceSseFrame(block);
      if (frame.kind === "workspace_event") {
        await onEvent(frame.event, frame.cursor);
      } else if (frame.kind === "stream_error") {
        throw new WorkspaceStreamError(
          frame.message ?? "Event stream protocol failed",
          frame.code,
          frame.retryable,
        );
      }
      boundary = buffer.search(/\r?\n\r?\n/);
    }
  }
}
