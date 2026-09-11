import assert from "node:assert/strict";
import test from "node:test";

import { mapWithDeckBudget, medianMillis } from "./hybrid-deck-budget.mjs";

/** A clock the test advances explicitly, so no wall time is spent. */
function fakeClock(start = 0) {
  const state = { value: start };
  return {
    now: () => state.value,
    advance: (millis) => {
      state.value += millis;
    },
  };
}

/** A callback that costs `costMillis` of the fake clock per item. */
function costingCallback(clock, costMillis, log) {
  return async (item) => {
    log.push(item);
    clock.advance(typeof costMillis === "function" ? costMillis(item) : costMillis);
    return { item, prepared: true };
  };
}

const expired = (item) => ({ item, prepared: false });

test("medianMillis ignores a single slow outlier", () => {
  assert.equal(medianMillis([]), null);
  assert.equal(medianMillis([70]), 70);
  assert.equal(medianMillis([70, 60, 900]), 70);
  assert.equal(medianMillis([10, 20, 30, 40]), 25);
});

test("a deck that fits the budget attempts every slide", async () => {
  const clock = fakeClock();
  const log = [];
  const items = Array.from({ length: 35 }, (_, index) => index);
  // One slide of headroom beyond the deck's own cost: the estimate carries a
  // safety factor, so an exactly-tight budget legitimately declines its last
  // slide rather than starting one it cannot finish.
  const { output, telemetry } = await mapWithDeckBudget(
    items,
    1,
    36 * 100,
    costingCallback(clock, 100, log),
    expired,
    { now: clock.now },
  );

  assert.equal(log.length, 35, "every slide must be attempted");
  assert.equal(telemetry.attempted, 35);
  assert.equal(telemetry.skippedByEstimate, 0);
  assert.equal(telemetry.skippedPastDeadline, 0);
  assert.ok(output.every((entry) => entry.prepared));
});

test("a slide the estimate cannot cover is not started", async () => {
  // The regression: the old rule started a slide with any budget left at all,
  // so the last slides were begun and could not finish.
  const clock = fakeClock();
  const log = [];
  const items = Array.from({ length: 10 }, (_, index) => index);
  // Budget for about five 100ms slides.
  const { output, telemetry } = await mapWithDeckBudget(
    items,
    1,
    520,
    costingCallback(clock, 100, log),
    expired,
    { now: clock.now, safetyFactor: 1 },
  );

  assert.equal(telemetry.attempted, 5, "only the slides that fit are started");
  assert.equal(log.length, 5);
  assert.equal(telemetry.skippedByEstimate, 5);
  assert.equal(telemetry.skippedPastDeadline, 0);
  assert.equal(telemetry.observedMedianMillis, 100);
  // The skipped items are the ordered suffix and are reported, not silently lost.
  assert.deepEqual(
    output.map((entry) => entry.prepared),
    [true, true, true, true, true, false, false, false, false, false],
  );
});

test("the estimate never vetoes before it has enough samples", async () => {
  // One pathologically slow first slide must not gate the rest of the deck.
  const clock = fakeClock();
  const log = [];
  const items = Array.from({ length: 6 }, (_, index) => index);
  const { telemetry } = await mapWithDeckBudget(
    items,
    1,
    10_000,
    costingCallback(clock, (item) => (item === 0 ? 5_000 : 100), log),
    expired,
    { now: clock.now },
  );

  assert.equal(telemetry.attempted, 6, "a slow first slide must not veto the deck");
  assert.equal(telemetry.skippedByEstimate, 0);
});

test("work already in flight is never cancelled near the deadline", async () => {
  // Real timers and two pumps: the fake clock cannot model concurrency, since
  // both pumps would advance the same counter and each would be charged for
  // the other's time. Exact counts are timing dependent, so this asserts the
  // invariants instead: nothing started after the deadline, everything started
  // finished, and no slot is left unfilled.
  const started = [];
  const finished = [];
  const items = Array.from({ length: 20 }, (_, index) => index);
  const deadline = Date.now() + 300;
  const { output, telemetry } = await mapWithDeckBudget(
    items,
    2,
    deadline,
    async (item) => {
      started.push({ item, at: Date.now() });
      await new Promise((resolve) => setTimeout(resolve, 40));
      finished.push(item);
      return { item, prepared: true };
    },
    expired,
    { minimumSamples: 1, safetyFactor: 1 },
  );

  assert.equal(finished.length, started.length, "every started slide finished");
  assert.ok(
    started.every((entry) => entry.at < deadline),
    "no slide may be started after the deadline",
  );
  assert.equal(telemetry.attempted, started.length);
  assert.equal(telemetry.completed, finished.length);
  assert.equal(
    telemetry.attempted + telemetry.skippedByEstimate + telemetry.skippedPastDeadline,
    items.length,
    "every slide is either attempted or accounted for as skipped",
  );
  assert.equal(output.filter((entry) => entry.prepared).length, started.length);
  assert.ok(output.every((entry) => entry !== undefined), "no slot left unfilled");
  assert.ok(telemetry.attempted < items.length, "the budget must bind this deck");
});

test("an already-expired budget attempts nothing and still returns every slot", async () => {
  const clock = fakeClock(1_000);
  const log = [];
  const items = [0, 1, 2];
  const { output, telemetry } = await mapWithDeckBudget(
    items,
    2,
    500,
    costingCallback(clock, 100, log),
    expired,
    { now: clock.now },
  );

  assert.equal(log.length, 0);
  assert.equal(telemetry.attempted, 0);
  assert.equal(telemetry.skippedPastDeadline, 3);
  assert.equal(output.length, 3);
  assert.ok(output.every((entry) => entry && entry.prepared === false));
  assert.equal(telemetry.remainingAtFirstSkipMillis, 0);
});

test("telemetry reports how the budget was spent", async () => {
  const clock = fakeClock();
  const items = Array.from({ length: 12 }, (_, index) => index);
  const { telemetry } = await mapWithDeckBudget(
    items,
    1,
    650,
    costingCallback(clock, 100, []),
    expired,
    { now: clock.now, safetyFactor: 1 },
  );

  assert.equal(telemetry.completed, telemetry.attempted);
  assert.equal(telemetry.observedMaxMillis, 100);
  assert.equal(telemetry.estimateMillis, 100);
  assert.ok(
    telemetry.remainingAtFirstSkipMillis !== null
      && telemetry.remainingAtFirstSkipMillis < 100,
    "the first skip happened with less than one slide of budget left",
  );
});

test("a single slide and a zero concurrency request still run", async () => {
  const clock = fakeClock();
  const log = [];
  const { output, telemetry } = await mapWithDeckBudget(
    [0],
    0,
    1_000,
    costingCallback(clock, 10, log),
    expired,
    { now: clock.now },
  );
  assert.equal(log.length, 1);
  assert.equal(telemetry.attempted, 1);
  assert.equal(output[0].prepared, true);
});
