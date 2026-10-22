import assert from "node:assert/strict";
import test from "node:test";

import type { GenerationActivityItem } from "../../../services/api/generation-activity-model";
import {
  cancelAllItems,
  isStalePreconditionError,
  pagesLabel,
  pickViewTarget,
} from "./generationGuardDecision.ts";

function item(overrides: Partial<GenerationActivityItem>): GenerationActivityItem {
  return {
    operationId: "op", presentationId: "p", state: "running",
    presentationTitle: null, sourceFileName: null,
    pages: { authored: 1, rendered: 0, persisted: 0, total: 10 },
    stage: null, queuePosition: null, capacityWaiting: false,
    createdAt: "2026-10-22T00:00:00Z", startedAt: null, lastProgressAt: null,
    operationVersion: 3, presentationRevision: 2, ...overrides,
  };
}

test("pickViewTarget prefers the first running item", () => {
  const items = [item({ operationId: "q", state: "queued" }), item({ operationId: "r" })];
  assert.equal(pickViewTarget(items)?.operationId, "r");
  assert.equal(pickViewTarget([items[0]])?.operationId, "q");
  assert.equal(pickViewTarget([]), null);
});

test("isStalePreconditionError matches status 409 only", () => {
  assert.equal(isStalePreconditionError({ status: 409 }), true);
  assert.equal(isStalePreconditionError({ status: 500 }), false);
  assert.equal(isStalePreconditionError(new Error("x")), false);
  assert.equal(isStalePreconditionError(null), false);
});

test("cancelAllItems sends preconditions with a fresh key per item", async () => {
  const calls: Array<Record<string, unknown>> = [];
  let n = 0;
  const outcome = await cancelAllItems(
    [item({ operationId: "a" }), item({ operationId: "b", operationVersion: 7, presentationRevision: 5 })],
    async (request) => { calls.push(request); },
    () => `k${++n}`,
  );
  assert.deepEqual(outcome, { ok: true });
  assert.deepEqual(calls, [
    { operationId: "a", idempotencyKey: "k1", expectedRevision: 2, expectedOperationVersion: 3 },
    { operationId: "b", idempotencyKey: "k2", expectedRevision: 5, expectedOperationVersion: 7 },
  ]);
});

test("cancelAllItems stops at the first failure and flags stale conflicts", async () => {
  const seen: string[] = [];
  const outcome = await cancelAllItems(
    [item({ operationId: "a" }), item({ operationId: "b" }), item({ operationId: "c" })],
    async (request) => {
      seen.push(request.operationId);
      if (request.operationId === "b") throw Object.assign(new Error("conflict"), { status: 409 });
    },
    () => "k",
  );
  assert.deepEqual(seen, ["a", "b"]);
  assert.equal(outcome.ok, false);
  assert.equal(outcome.ok === false && outcome.stale, true);
});

test("pagesLabel omits the fraction when total is unknown", () => {
  assert.equal(pagesLabel({ authored: 58, total: 100 }), "58/100");
  assert.equal(pagesLabel({ authored: 58, total: null }), "58");
});
