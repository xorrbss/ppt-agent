import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  normalizeEventPage,
  normalizeTimelinePage,
  normalizeWorkspaceActionReceipt,
} from "../app/(presentation-generator)/presentation/workspace/timelineProjection.ts";

const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../..",
);
const fixtureRoot = path.join(root, "contracts", "chat-first-workspace", "v1");
const fixture = (name) =>
  JSON.parse(readFileSync(path.join(fixtureRoot, name), "utf8"));
const clone = (value) => JSON.parse(JSON.stringify(value));

test("bootstrap wire fixture decodes immutable attachment state and live capabilities", () => {
  const contract = fixture("bootstrap-and-assets.json");
  const response = contract.bootstrap_response;
  const normalized = normalizeTimelinePage(response.timeline);
  const first = normalized.items.find((item) => item.anchorType === "message");
  const operation = normalized.items.find(
    (item) => item.anchorType === "operation",
  );
  const attachment = first.attachments[0];

  assert.equal(normalized.writesSafe, true);
  assert.equal(normalized.eventHighWatermark, "1");
  assert.equal(first.id, response.first_user_message_id);
  assert.equal(first.content, contract.bootstrap_request.prompt);
  assert.equal(attachment.id, contract.bootstrap_request.asset_ids[0]);
  assert.equal(attachment.name, contract.asset_response.file_name);
  assert.equal(attachment.stateAtTime, "processing");
  assert.equal(attachment.currentState, "ready");
  assert.equal(attachment.status, "ready");
  assert.equal(attachment.mimeType, "application/pdf");
  assert.equal(attachment.size, 4096);
  assert.match(attachment.downloadUrl, /\/download$/);
  assert.match(attachment.previewUrl, /\/preview$/);
  assert.equal(operation.operationId, response.operation_id);
  assert.deepEqual(
    operation.allowedActions.map(({ id }) => id),
    ["cancel"],
  );
});

test("timeline wire fixture decodes card metadata and keeps snapshot state immutable", () => {
  const contract = fixture("timeline-projection.json");
  const normalized = normalizeTimelinePage(contract.timeline);
  const message = normalized.items.find(
    (item) => item.anchorType === "message",
  );
  const operation = normalized.items.find(
    (item) => item.anchorType === "operation",
  );
  const attachment = message.attachments[0];

  assert.equal(normalized.writesSafe, true);
  assert.equal(normalized.eventHighWatermark, "3");
  assert.deepEqual(
    normalized.items.map(({ position }) => position),
    [1, 2, 3],
  );
  assert.equal(attachment.stateAtTime, "ready");
  assert.equal(attachment.currentState, "deleted");
  assert.equal(attachment.failureCode, "workspace_asset_deleted");
  assert.equal(
    attachment.error,
    "An attachment was deleted and must be uploaded again.",
  );
  assert.equal(attachment.downloadUrl, undefined);
  assert.equal(attachment.previewUrl, undefined);
  assert.equal(operation.operationType, "presentation.generate");
  assert.equal(operation.version, 3);
  assert.equal(operation.expectedRevision, 0);
  assert.equal(operation.resultRevision, undefined);
  assert.equal(operation.actor.type, "user");
  assert.equal(operation.actor.label, "You");
  assert.equal(
    operation.target.presentation_id,
    "11111111-1111-4111-8111-111111111111",
  );
  assert.equal(operation.activitySummary, "Outline approval required");
  assert.deepEqual(
    operation.events.map(({ sequence }) => sequence),
    [1, 2, 3],
  );
  assert.deepEqual(
    operation.allowedActions.map(({ id }) => id),
    ["approve", "cancel"],
  );
  assert.ok(operation.allowedActions.every(({ enabled }) => enabled));
});

test("event-page wire fixture advances only a canonical bounded cursor", () => {
  const contract = fixture("timeline-projection.json");
  const normalized = normalizeEventPage(contract.event_page);

  assert.equal(normalized.cursorSafe, true);
  assert.equal(normalized.eventHighWatermark, "3");
  assert.equal(normalized.events.length, 1);
  assert.equal(normalized.events[0].cursor, "3");
  assert.equal(normalized.events[0].operationVersion, 3);

  const malformed = clone(contract.event_page);
  malformed.events[0].cursor = "event-sequence:3";
  const rejected = normalizeEventPage(malformed);
  assert.equal(rejected.cursorSafe, false);
  assert.equal(rejected.eventHighWatermark, undefined);
  assert.deepEqual(rejected.events, []);
});

test("receipt decoder exposes the same durable receipt on duplicate delivery", () => {
  const contract = fixture("commands-and-receipts.json");
  const first = normalizeWorkspaceActionReceipt(contract.action_response);
  const duplicate = normalizeWorkspaceActionReceipt(
    contract.duplicate_action_response,
  );

  assert.equal(contract.command_request.command_type, "slide.edit");
  assert.equal(contract.action_request.action, "approve");
  assert.equal(first.contract, "v1");
  assert.equal(first.schemaVersion, 1);
  assert.equal(first.timeline.writesSafe, true);
  assert.equal(first.duplicate, false);
  assert.equal(duplicate.contract, "v1");
  assert.equal(duplicate.duplicate, true);
  assert.equal(duplicate.receiptId, first.receiptId);
  assert.equal(duplicate.operationId, first.operationId);
  assert.equal(duplicate.status, first.status);
  assert.deepEqual(duplicate.timeline, first.timeline);
});

test("unknown schema versions fail closed for timelines and receipts", () => {
  const timeline = fixture("timeline-projection.json").timeline;
  const futureTimeline = { ...timeline, schema_version: 2 };
  const normalized = normalizeTimelinePage(futureTimeline);

  assert.equal(normalized.writesSafe, false);
  assert.equal(normalized.eventHighWatermark, undefined);
  assert.ok(normalized.items.every((item) => item.anchorType === "unknown"));

  const response = fixture("commands-and-receipts.json").action_response;
  const futureReceipt = normalizeWorkspaceActionReceipt({
    ...response,
    schema_version: 2,
  });
  assert.equal(futureReceipt.contract, "v1");
  assert.equal(futureReceipt.schemaVersion, 2);
  assert.equal(futureReceipt.timeline.writesSafe, false);
  assert.equal(futureReceipt.timeline.eventHighWatermark, undefined);
});

test("unknown operation types preserve history but expose no inferred actions", () => {
  const timeline = clone(fixture("timeline-projection.json").timeline);
  const rawOperation = timeline.items.find(
    (item) => item.anchor_type === "operation",
  );
  rawOperation.operation_type = "future.teleport";

  const normalized = normalizeTimelinePage(timeline);
  const operation = normalized.items.find(
    (item) => item.anchorType === "operation",
  );
  assert.equal(normalized.writesSafe, true);
  assert.equal(operation.operationType, "future.teleport");
  assert.deepEqual(operation.allowedActions, []);
});

test("capability fixture separates reads from writes and retains closed privacy sets", () => {
  const contract = fixture("errors-and-runtime.json");
  const enabled = contract.capability_responses.sqlite_enabled;
  const readOnly = contract.capability_responses.postgresql_read_only;
  const blocked = contract.capability_responses.mysql_blocked;

  assert.equal(enabled.reads_supported, true);
  assert.equal(enabled.writes_supported, true);
  assert.equal(readOnly.timeline, true);
  assert.equal(readOnly.events, true);
  assert.equal(readOnly.reads_supported, true);
  assert.equal(readOnly.commands, false);
  assert.equal(readOnly.commands_enabled, false);
  assert.equal(readOnly.writes_supported, false);
  assert.equal(readOnly.kill_switch_active, true);
  assert.equal(blocked.supported, false);
  assert.equal(blocked.reason, "chat_first_runtime_unsupported");
  assert.ok(
    contract.errors.every(
      ({ code, safe_message, retryable, status_code }) =>
        typeof code === "string" &&
        typeof safe_message === "string" &&
        typeof retryable === "boolean" &&
        Number.isInteger(status_code),
    ),
  );
  assert.ok(contract.privacy.telemetry_forbidden_fields.includes("prompt"));
  assert.ok(
    contract.privacy.public_share_forbidden_fields.includes("operation_events"),
  );
});
