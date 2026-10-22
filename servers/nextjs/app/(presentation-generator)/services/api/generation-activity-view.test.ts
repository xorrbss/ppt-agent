import assert from "node:assert/strict";
import test from "node:test";

import { normalizeGenerationActivity } from "./generation-activity-model.ts";
import { describeGenerationActivityItem, presentationHref } from "./generation-activity-view.ts";

const NOW = Date.parse("2026-10-22T09:00:00+00:00");

function item(overrides: Record<string, unknown>) {
  const snapshot = normalizeGenerationActivity({
    server_time: "2026-10-22T09:00:00+00:00",
    items: [{
      operation_id: "op", presentation_id: "p/1", state: "running",
      pages: { authored: 58, rendered: 57, persisted: 0, total: 100 },
      created_at: "2026-10-22T08:00:00+00:00", started_at: "2026-10-22T08:00:00+00:00",
      last_progress_at: "2026-10-22T08:55:00+00:00",
      operation_version: 1, presentation_revision: 1, ...overrides,
    }],
  });
  return snapshot.items[0];
}

test("running item reports pages and minutes since progress", () => {
  const view = describeGenerationActivityItem(item({}), NOW);
  assert.deepEqual(view.pages, { authored: 58, total: 100 });
  assert.equal(view.progressMinutes, 5);
  assert.equal(view.stalled, false);
  assert.equal(view.stalledMinutes, null);
});

test("running item with no page for 10+ minutes is stalled", () => {
  const view = describeGenerationActivityItem(item({ last_progress_at: "2026-10-22T08:40:00+00:00" }), NOW);
  assert.equal(view.stalled, true);
  assert.equal(view.stalledMinutes, 20);
});

test("falls back to start time when no page completed yet", () => {
  const view = describeGenerationActivityItem(item({ last_progress_at: null }), NOW);
  assert.equal(view.progressMinutes, 60);
  assert.equal(view.stalled, true);
});

test("queued item has position, no progress minutes, never stalled", () => {
  const view = describeGenerationActivityItem(
    item({ state: "queued", queue_position: 2, started_at: null, last_progress_at: null }), NOW);
  assert.equal(view.queued, true);
  assert.equal(view.queuePosition, 2);
  assert.equal(view.progressMinutes, null);
  assert.equal(view.stalled, false);
});

test("unknown total is preserved", () => {
  const view = describeGenerationActivityItem(
    item({ pages: { authored: 3, rendered: 0, persisted: 0, total: null } }), NOW);
  assert.equal(view.pages.total, null);
});

test("presentationHref encodes the id", () => {
  assert.equal(presentationHref("p/1"), "/presentation?id=p%2F1");
});

test("cancelling item shows neither progress minutes nor a stall", () => {
  const view = describeGenerationActivityItem(
    item({ state: "cancelling", last_progress_at: "2026-10-22T08:00:00+00:00" }), NOW);
  assert.equal(view.cancelling, true);
  assert.equal(view.queued, false);
  assert.equal(view.progressMinutes, null);
  assert.equal(view.stalled, false);
});
