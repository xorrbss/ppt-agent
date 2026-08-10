import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseUnifiedConversationEvent } from "./event-contract.ts";

// Keep this test executable through Node without requiring a TS loader. It
// validates the versioned cross-runtime fixture and the browser parser's rules.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const contract = JSON.parse(readFileSync(path.join(root, "contracts", "unified-conversation", "v1", "event-envelope.json"), "utf8"));
const reconnect = JSON.parse(readFileSync(path.join(root, "contracts", "unified-conversation", "v1", "reconnect-sequence.json"), "utf8"));
const typeSet = new Set(contract.properties.type.enum);
const cursor = /^(0|[1-9][0-9]*)$/u;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/iu;
const envelopeKeys = new Set(["schema_version", "id", "type", "durability", "conversation_id", "run_id", "message_id", "presentation_id", "cursor", "checkpoint_cursor", "occurred_at", "data"]);
const privateKeys = new Set(["chain_of_thought", "cot", "raw_provider_response", "provider_response", "provider_raw", "prompt", "system_prompt", "authorization", "api_key", "authorization_header", "bearer_token", "credentials", "password", "access_token", "refresh_token", "stack_trace", "traceback"]);

const normalizedDataKey = (key) => key.trim()
  .replace(/([a-z0-9])([A-Z])/gu, "$1_$2")
  .replace(/[\s-]+/gu, "_")
  .toLowerCase();

const hasPrivateData = (value) => Array.isArray(value)
  ? value.some(hasPrivateData)
  : value && typeof value === "object"
    ? Object.entries(value).some(([key, child]) => privateKeys.has(normalizedDataKey(key)) || hasPrivateData(child))
    : false;

function accepts(event) {
  if (!event || event.schema_version !== 1 || !typeSet.has(event.type) || !["durable", "transient"].includes(event.durability)) return false;
  if (Object.keys(event).some((key) => !envelopeKeys.has(key))) return false;
  if (typeof event.id !== "string" || !event.id || typeof event.conversation_id !== "string" || !uuid.test(event.conversation_id) || typeof event.occurred_at !== "string" || Number.isNaN(Date.parse(event.occurred_at)) || !event.data || hasPrivateData(event.data)) return false;
  if (![event.run_id, event.message_id, event.presentation_id].every((value) => value == null || (typeof value === "string" && uuid.test(value)))) return false;
  if (event.cursor != null && (typeof event.cursor !== "string" || !cursor.test(event.cursor))) return false;
  if (event.checkpoint_cursor != null && (typeof event.checkpoint_cursor !== "string" || !cursor.test(event.checkpoint_cursor))) return false;
  if (event.type === "assistant.delta") return event.durability === "transient" && event.cursor == null && typeof event.data.delta === "string" && event.data.delta.length <= 100_000 && typeof event.run_id === "string" && typeof event.message_id === "string";
  if (event.durability !== "durable" || event.cursor == null) return false;
  if (event.type === "assistant.checkpoint") return typeof event.data.content === "string" && event.data.content.length <= 100_000 && typeof event.run_id === "string" && typeof event.message_id === "string";
  if (event.type === "presentation.published") return typeof event.presentation_id === "string" && Number.isInteger(event.data.revision);
  if (event.type === "message.created") return typeof event.data.role === "string" && typeof event.data.content === "string";
  if (["run.queued", "run.started", "run.status", "run.completed"].includes(event.type)) return typeof event.data.status === "string" && event.data.status.length > 0;
  if (["tool.started", "tool.completed"].includes(event.type)) return typeof event.data.tool === "string" && event.data.tool.length > 0 && typeof event.data.summary === "string" && event.data.summary.length > 0;
  if (event.type === "approval.required") return typeof event.data.approval_id === "string" && event.data.approval_id.length > 0;
  if (event.type === "run.failed") return typeof event.data.status === "string" && event.data.status.length > 0 && typeof event.data.error_code === "string" && event.data.error_code.length > 0;
  return false;
}

test("Unified Conversation fixture is browser-safe and defines a replay checkpoint", () => {
  assert.equal(contract.schema_version, 1);
  assert.deepEqual(new Set(contract["x-invariants"].transient_types), new Set(["assistant.delta"]));
  assert.ok(contract["x-invariants"].checkpoint_reconnect.includes("assistant.checkpoint"));
  assert.ok(contract["x-replay"].retention.includes("not silently accepted"));
  assert.ok(contract["x-replay"].rebase.includes("expected revision"));
  for (const event of contract.examples) {
    assert.equal(accepts(event), true);
    assert.ok(parseUnifiedConversationEvent(event));
  }
  assert.equal(contract.examples[1].cursor, null);
  assert.equal(contract.examples[1].checkpoint_cursor, "43");
});

test("frontend event admission rejects malformed replay cursors and private runtime data", () => {
  const event = structuredClone(contract.examples[0]);
  event.cursor = "01";
  assert.equal(accepts(event), false);
  assert.equal(parseUnifiedConversationEvent(event), undefined);
  event.cursor = "41";
  event.data = { tool: { raw_provider_response: "must-not-leak" } };
  assert.equal(accepts(event), false);
  assert.equal(parseUnifiedConversationEvent(event), undefined);
});

test("frontend event admission rejects normalized private-key spellings", () => {
  for (const key of [
    "rawProviderResponse",
    "raw-provider-response",
    "authorization_header",
    "authorization-header",
    "authorizationHeader",
    "bearer_token",
    "bearer-token",
    "bearerToken",
    "credentials",
    "password",
  ]) {
    const event = structuredClone(contract.examples[0]);
    event.data = { content: "safe", nested: { [key]: "must-not-leak" } };
    assert.equal(accepts(event), false, key);
    assert.equal(parseUnifiedConversationEvent(event), undefined, key);
  }
});

test("frontend event admission rejects malformed identifiers, timestamps and required event data", () => {
  const event = structuredClone(contract.examples[0]);
  event.conversation_id = "not-a-uuid";
  assert.equal(accepts(event), false);
  assert.equal(parseUnifiedConversationEvent(event), undefined);
  event.conversation_id = contract.examples[0].conversation_id;
  event.occurred_at = "not-a-timestamp";
  assert.equal(accepts(event), false);
  assert.equal(parseUnifiedConversationEvent(event), undefined);
  event.occurred_at = contract.examples[0].occurred_at;
  delete event.data.content;
  assert.equal(accepts(event), false);
  assert.equal(parseUnifiedConversationEvent(event), undefined);
  event.data.content = "valid";
  event.unexpected = true;
  assert.equal(accepts(event), false);
  assert.equal(parseUnifiedConversationEvent(event), undefined);
});

test("browser parser enforces approval and failure payload requirements", () => {
  const envelope = {
    schema_version: 1,
    id: "event-approval",
    type: "approval.required",
    durability: "durable",
    conversation_id: "11111111-1111-4111-8111-111111111111",
    run_id: "44444444-4444-4444-8444-444444444444",
    message_id: null,
    presentation_id: null,
    cursor: "7",
    checkpoint_cursor: null,
    occurred_at: "2026-08-11T00:00:00Z",
    data: { approval_id: "approval-7" },
  };
  assert.ok(parseUnifiedConversationEvent(envelope));
  delete envelope.data.approval_id;
  assert.equal(parseUnifiedConversationEvent(envelope), undefined);
  const failed = {
    ...envelope,
    type: "run.failed",
    data: { status: "failed", error_code: "workspace_request_failed" },
  };
  assert.ok(parseUnifiedConversationEvent(failed));
  delete failed.data.error_code;
  assert.equal(parseUnifiedConversationEvent(failed), undefined);
});

test("checkpoint plus cursor fixture reconstructs a reconnectable assistant timeline", () => {
  const replay = reconnect.events.filter((event) => event.cursor != null && Number(event.cursor) > Number(reconnect.reconnect_after_cursor));
  const checkpoints = new Map();
  for (const event of replay) {
    assert.equal(accepts(event), true);
    if (event.type === "assistant.checkpoint") checkpoints.set(event.message_id, event.data.content);
  }
  assert.equal(replay.at(-1).cursor, reconnect.expected_recovery.next_cursor);
  assert.equal(checkpoints.get(reconnect.expected_recovery.assistant_message_id), reconnect.expected_recovery.assistant_content);
  assert.equal(replay.at(-1).data.status, reconnect.expected_recovery.run_status);
});
