import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const fixtureRoot = path.join(root, "contracts", "chat-first-workspace", "v1");
const fixture = (name) =>
  JSON.parse(readFileSync(path.join(fixtureRoot, name), "utf8"));

test("workspace bootstrap fixture renders a durable first user message while assets wait", () => {
  const { bootstrap, asset_policy: policy } = fixture("bootstrap-and-assets.json");
  const first = bootstrap.first_user_message;

  assert.equal(first.role, "user");
  assert.ok(first.content);
  assert.equal(first.position, 1);
  assert.equal(bootstrap.operation_state, "waiting_for_assets");
  assert.ok(policy.wait_states.includes(first.attachments[0].status));
  assert.deepEqual(bootstrap.duplicate_bootstrap_result, {
    conversation_id: bootstrap.conversation_id,
    first_user_message_id: first.id,
    operation_id: bootstrap.operation_id,
  });
});

test("workspace timeline fixture projects events into one anchor and keeps cursors distinct", () => {
  const timeline = fixture("timeline-projection.json");
  const positions = timeline.top_level_items.map((item) => item.position);
  const sequences = timeline.operation_card.events.map((event) => event.sequence);

  assert.equal(positions[0], 1);
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
  assert.deepEqual(sequences, [1, 2, 3]);
  assert.notEqual(
    timeline.pagination.next_message_cursor,
    timeline.event_replay.high_watermark_cursor,
  );
  assert.equal(timeline.projection_invariants.event_projection, "update_anchor_card_only");
});

test("workspace command fixture preserves idempotent action receipt and single publish", () => {
  const { command, action, receipt, publication } = fixture("commands-and-receipts.json");

  assert.equal(command.command_type, "slide.edit");
  assert.equal(command.operation_id, action.operation_id);
  assert.equal(action.action_id, receipt.action_id);
  assert.equal(action.idempotency_key, receipt.idempotency_key);
  assert.equal(receipt.duplicate_result, "same_receipt");
  assert.equal(publication.checkpoint_increments_revision, false);
  assert.equal(publication.final_publish_increments_revision_once, true);
});

test("workspace runtime contract fails closed for MySQL and forbids sensitive telemetry", () => {
  const contract = fixture("errors-and-runtime.json");

  assert.equal(contract.runtime_capabilities.sqlite.chat_first_workspace, true);
  assert.equal(contract.runtime_capabilities.postgresql.chat_first_workspace, true);
  assert.equal(contract.runtime_capabilities.mysql.chat_first_workspace, false);
  assert.equal(
    contract.runtime_capabilities.mysql.error_code,
    "chat_first_runtime_unsupported",
  );
  assert.ok(contract.privacy.telemetry_forbidden_fields.includes("prompt"));
  assert.ok(contract.privacy.public_share_forbidden_fields.includes("operation_events"));
});
