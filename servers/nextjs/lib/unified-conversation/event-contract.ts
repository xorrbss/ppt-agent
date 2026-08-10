/** Transport-neutral mirror of contracts/unified-conversation/v1/event-envelope.json. */

export const UNIFIED_CONVERSATION_SCHEMA_VERSION = 1 as const;

export const unifiedConversationEventTypes = [
  "message.created", "run.queued", "run.started", "run.status",
  "assistant.delta", "assistant.checkpoint", "tool.started", "tool.completed",
  "approval.required", "presentation.published", "run.completed", "run.failed",
] as const;

export type UnifiedConversationEventType = (typeof unifiedConversationEventTypes)[number];
export type UnifiedEventDurability = "durable" | "transient";

export type UnifiedConversationEvent = {
  schema_version: 1;
  id: string;
  type: UnifiedConversationEventType;
  durability: UnifiedEventDurability;
  conversation_id: string;
  run_id?: string | null;
  message_id?: string | null;
  presentation_id?: string | null;
  cursor?: string | null;
  checkpoint_cursor?: string | null;
  occurred_at: string;
  data: Record<string, unknown>;
};

const cursorPattern = /^(0|[1-9][0-9]*)$/u;
const transientTypes = new Set<UnifiedConversationEventType>(["assistant.delta"]);
const privateKeys = new Set([
  "chain_of_thought", "cot", "raw_provider_response", "provider_response",
  "provider_raw", "prompt", "system_prompt", "authorization", "api_key",
  "authorization_header", "bearer_token", "credentials", "password",
  "access_token", "refresh_token", "stack_trace", "traceback",
]);
const maxPublicAssistantTextLength = 100_000;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const allowedEnvelopeKeys = new Set([
  "schema_version", "id", "type", "durability", "conversation_id", "run_id",
  "message_id", "presentation_id", "cursor", "checkpoint_cursor", "occurred_at", "data",
]);

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function normalizedDataKey(key: string): string {
  return key
    .trim()
    .replace(/([a-z0-9])([A-Z])/gu, "$1_$2")
    .replace(/[\s-]+/gu, "_")
    .toLowerCase();
}

function hasPrivateData(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(hasPrivateData);
  if (!isRecord(value)) return false;
  return Object.entries(value).some(([key, child]) =>
    privateKeys.has(normalizedDataKey(key)) || hasPrivateData(child),
  );
}

function isUuidOrNull(value: unknown): boolean {
  return value === null || (typeof value === "string" && uuidPattern.test(value));
}

function isDateTime(value: unknown): boolean {
  return typeof value === "string" && value.length > 0 && !Number.isNaN(Date.parse(value));
}

function hasStringData(data: Record<string, unknown>, ...keys: string[]): boolean {
  return keys.every((key) => typeof data[key] === "string" && data[key].length > 0);
}

function hasRequiredEventData(type: UnifiedConversationEventType, data: Record<string, unknown>): boolean {
  switch (type) {
    case "message.created":
      return hasStringData(data, "role", "content");
    case "run.queued":
    case "run.started":
    case "run.status":
    case "run.completed":
      return hasStringData(data, "status");
    case "assistant.delta":
      return typeof data.delta === "string" && data.delta.length <= maxPublicAssistantTextLength;
    case "assistant.checkpoint":
      return typeof data.content === "string" && data.content.length <= maxPublicAssistantTextLength;
    case "tool.started":
    case "tool.completed":
      return hasStringData(data, "tool", "summary");
    case "approval.required":
      return hasStringData(data, "approval_id");
    case "presentation.published":
      return Number.isInteger(data.revision);
    case "run.failed":
      return hasStringData(data, "status", "error_code");
  }
}

export function isCanonicalConversationCursor(value: unknown): value is string {
  return typeof value === "string" && cursorPattern.test(value);
}

/** Decode unknown wire input fail-closed before it reaches a timeline reducer. */
export function parseUnifiedConversationEvent(value: unknown): UnifiedConversationEvent | undefined {
  if (!isRecord(value) || value.schema_version !== UNIFIED_CONVERSATION_SCHEMA_VERSION) return undefined;
  if (Object.keys(value).some((key) => !allowedEnvelopeKeys.has(key))) return undefined;
  if (typeof value.id !== "string" || value.id.length === 0 || typeof value.conversation_id !== "string" || !uuidPattern.test(value.conversation_id)) return undefined;
  if (typeof value.type !== "string" || !unifiedConversationEventTypes.includes(value.type as UnifiedConversationEventType)) return undefined;
  if (value.durability !== "durable" && value.durability !== "transient") return undefined;
  if (!isDateTime(value.occurred_at) || !isRecord(value.data) || hasPrivateData(value.data)) return undefined;
  if (!isUuidOrNull(value.run_id ?? null) || !isUuidOrNull(value.message_id ?? null) || !isUuidOrNull(value.presentation_id ?? null)) return undefined;
  const type = value.type as UnifiedConversationEventType;
  const cursor = value.cursor ?? null;
  const checkpointCursor = value.checkpoint_cursor ?? null;
  if (cursor !== null && !isCanonicalConversationCursor(cursor)) return undefined;
  if (checkpointCursor !== null && !isCanonicalConversationCursor(checkpointCursor)) return undefined;
  if (transientTypes.has(type) !== (value.durability === "transient")) return undefined;
  if (value.durability === "durable" && cursor === null) return undefined;
  if (value.durability === "transient" && cursor !== null) return undefined;
  if (!hasRequiredEventData(type, value.data)) return undefined;
  if ((type === "assistant.delta" || type === "assistant.checkpoint") && (typeof value.run_id !== "string" || typeof value.message_id !== "string")) return undefined;
  if (type === "presentation.published" && typeof value.presentation_id !== "string") return undefined;
  return value as UnifiedConversationEvent;
}
