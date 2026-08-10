import assert from "node:assert/strict";
import test from "node:test";

import { applyConversationEvent } from "./timeline.ts";
import type { ConversationTimeline } from "./types.ts";

test("replays assistant checkpoint over transient deltas after reconnect", () => {
  const initial: ConversationTimeline = { items: [], hasMore: false, cursor: "4" };
  const withDelta = applyConversationEvent(initial, { id: "5", cursor: "5", type: "assistant.delta", runId: "run-1", delta: "Hello " });
  const replayed = applyConversationEvent(withDelta, { id: "6", cursor: "6", type: "assistant.checkpoint", runId: "run-1", checkpoint: "Hello world" });
  const message = replayed.items.find((item) => item.kind === "message");
  assert.equal(message?.kind === "message" && message.message.content, "Hello world");
  assert.equal(replayed.cursor, "6");
});

test("marks streaming assistant output complete without changing revision", () => {
  const streaming = applyConversationEvent({ items: [], hasMore: false, revision: 7 }, { id: "1", type: "assistant.delta", runId: "run", delta: "Answer" });
  const completed = applyConversationEvent(streaming, { id: "2", type: "run.completed", runId: "run" });
  const message = completed.items.find((item) => item.kind === "message");
  assert.equal(message?.kind === "message" && message.message.streaming, false);
  assert.equal(completed.revision, 7);
});

test("uses the durable assistant message id and preserves run controls on status replay", () => {
  const initial: ConversationTimeline = {
    hasMore: false,
    items: [{
      kind: "run",
      position: 1,
      run: { id: "run", status: "running", title: "Edit slide", version: 3, actions: ["cancel"] },
    }],
  };
  const withDelta = applyConversationEvent(initial, {
    id: "1",
    type: "assistant.delta",
    runId: "run",
    messageId: "assistant-message",
    delta: "Partial",
  });
  const recovered = applyConversationEvent(withDelta, {
    id: "2",
    cursor: "4",
    type: "assistant.checkpoint",
    runId: "run",
    messageId: "assistant-message",
    checkpoint: "Recovered answer",
  });
  const updated = applyConversationEvent(recovered, {
    id: "3",
    cursor: "5",
    type: "run.status",
    runId: "run",
    status: "approval_required",
    run: { id: "run", status: "approval_required" },
  });
  const assistant = updated.items.find((item) => item.kind === "message" && item.message.id === "assistant-message");
  const run = updated.items.find((item) => item.kind === "run" && item.run.id === "run");
  assert.equal(assistant?.kind === "message" && assistant.message.content, "Recovered answer");
  assert.equal(updated.items.filter((item) => item.kind === "message").length, 1);
  assert.deepEqual(run?.kind === "run" && run.run.actions, ["cancel"]);
  assert.equal(run?.kind === "run" && run.run.title, "Edit slide");
});
