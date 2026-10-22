import assert from "node:assert/strict";
import test from "node:test";

import {
  GENERATION_STALL_THRESHOLD_MS,
  activeGenerationItems,
  isGenerationStalled,
  minutesSince,
  normalizeGenerationActivity,
  serverNowMs,
} from "./generation-activity-model.ts";

const ITEM = {
  operation_id: "op-1",
  presentation_id: "p-1",
  state: "running",
  presentation_title: "Deck",
  source_file_name: "plan.pdf",
  pages: { authored: 58, rendered: 57, persisted: 0, total: 100 },
  stage: "author",
  queue_position: null,
  capacity_waiting: false,
  created_at: "2026-10-22T08:00:00Z",
  started_at: "2026-10-22T08:01:00Z",
  last_progress_at: "2026-10-22T08:30:00Z",
  operation_version: 4,
  presentation_revision: 2,
};

test("normalizes items and unwraps a data envelope", () => {
  const snapshot = normalizeGenerationActivity(
    { data: { items: [ITEM], server_time: "2026-10-22T08:35:00Z" } },
    1000,
  );
  assert.equal(snapshot.items.length, 1);
  const [item] = snapshot.items;
  assert.equal(item.operationId, "op-1");
  assert.deepEqual(item.pages, { authored: 58, rendered: 57, persisted: 0, total: 100 });
  assert.equal(item.operationVersion, 4);
  assert.equal(item.presentationRevision, 2);
  assert.equal(snapshot.serverTime, "2026-10-22T08:35:00Z");
});

test("drops items missing identity, state or cancel preconditions", () => {
  const snapshot = normalizeGenerationActivity({
    items: [
      { ...ITEM, operation_id: "" },
      { ...ITEM, state: "succeeded" },
      { ...ITEM, operation_version: undefined },
      { ...ITEM, presentation_revision: -1 },
      ITEM,
    ],
  });
  assert.equal(snapshot.items.length, 1);
});

test("queue position below 1 is treated as unknown", () => {
  const [item] = normalizeGenerationActivity({ items: [{ ...ITEM, state: "queued", queue_position: 0 }] }).items;
  assert.equal(item.queuePosition, null);
});

test("server now advances with client elapsed time", () => {
  const now = serverNowMs({ serverTime: "2026-10-22T08:35:00Z", receivedAt: 1000 }, 61000);
  assert.equal(now, Date.parse("2026-10-22T08:36:00Z"));
});

test("stall uses last progress, falls back to start, and ignores queued", () => {
  const [item] = normalizeGenerationActivity({ items: [ITEM] }).items;
  const last = Date.parse(ITEM.last_progress_at);
  assert.equal(isGenerationStalled(item, last + GENERATION_STALL_THRESHOLD_MS - 1), false);
  assert.equal(isGenerationStalled(item, last + GENERATION_STALL_THRESHOLD_MS), true);
  const fresh = { ...item, lastProgressAt: null };
  assert.equal(isGenerationStalled(fresh, Date.parse(ITEM.started_at) + GENERATION_STALL_THRESHOLD_MS), true);
  assert.equal(isGenerationStalled({ ...item, state: "queued" }, last + 10 * GENERATION_STALL_THRESHOLD_MS), false);
});

test("minutesSince floors and never goes negative", () => {
  const base = Date.parse("2026-10-22T08:00:00Z");
  assert.equal(minutesSince("2026-10-22T08:00:00Z", base + 179_000), 2);
  assert.equal(minutesSince("2026-10-22T08:00:00Z", base - 60_000), 0);
  assert.equal(minutesSince(null, base), null);
});

test("cancelling is kept for display but is not active work or stalled", () => {
  const { items } = normalizeGenerationActivity({
    items: [{ ...ITEM, state: "cancelling" }, { ...ITEM, operation_id: "op-2", state: "queued" }],
  });
  assert.deepEqual(items.map((item) => item.state), ["cancelling", "queued"]);
  assert.deepEqual(activeGenerationItems(items).map((item) => item.operationId), ["op-2"]);
  assert.equal(isGenerationStalled(items[0], Date.parse(ITEM.last_progress_at) + 10 * GENERATION_STALL_THRESHOLD_MS), false);
});
