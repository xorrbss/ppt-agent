import assert from "node:assert/strict";
import test from "node:test";

import {
  activeGenerationStep,
  buildGenerationProgress,
  etaMinutes,
  latestGenerationEventEvidence,
  normalizeGenerationStage,
  type GenerationActivityInput,
} from "./generationProgressSteps.ts";

const START = Date.parse("2026-10-22T00:00:00Z");
type Override = Partial<Omit<GenerationActivityInput, "pages">> & { pages?: Partial<GenerationActivityInput["pages"]> };
const activity = (over: Override = {}): GenerationActivityInput => ({
  pages: { authored: 0, rendered: 0, persisted: 0, total: 128, ...over.pages },
  stage: over.stage ?? null,
  startedAt: over.startedAt ?? new Date(START).toISOString(),
  lastProgressAt: over.lastProgressAt ?? null,
});
const states = (m: ReturnType<typeof buildGenerationProgress>) => m.steps.map((s) => s.state).join(",");

test("normalizes raw and public stage names", () => {
  assert.equal(normalizeGenerationStage("authored_author"), "author");
  assert.equal(normalizeGenerationStage("author"), "author");
  assert.equal(normalizeGenerationStage("authored_outline"), "outline");
  assert.equal(normalizeGenerationStage(" "), null);
});

test("prepare is active before any page is authored", () => {
  const m = buildGenerationProgress({ activity: activity({ stage: "outline" }), evidence: {}, now: START + 1000 });
  assert.equal(states(m), "active,pending,pending,pending,pending");
});

test("author and render are both active mid-run", () => {
  const m = buildGenerationProgress({
    activity: activity({ stage: "author", pages: { authored: 78, rendered: 76 } }),
    evidence: {}, now: START + 35 * 60_000,
  });
  assert.equal(states(m), "done,active,active,pending,pending");
  assert.deepEqual([m.steps[1].current, m.steps[1].total], [78, 128]);
  assert.deepEqual([m.steps[2].current, m.steps[2].total], [76, 128]);
  assert.equal(activeGenerationStep(m), "render");
});

test("persist activates when all pages are rendered", () => {
  const m = buildGenerationProgress({
    activity: activity({ stage: "render", pages: { authored: 128, rendered: 128, persisted: 10 } }),
    evidence: {}, now: START + 1000,
  });
  assert.equal(states(m), "done,done,done,active,pending");
});

test("publish stage completes earlier steps", () => {
  const m = buildGenerationProgress({ activity: activity({ stage: "publication", pages: { authored: 128, rendered: 128, persisted: 128 } }), evidence: {}, now: START });
  assert.equal(states(m), "done,done,done,done,active");
});

test("falls back to event evidence without activity", () => {
  const m = buildGenerationProgress({ activity: null, evidence: { stage: "authored_render", current: 3, total: 30 }, now: START });
  assert.equal(states(m), "done,done,active,pending,pending");
  assert.deepEqual([m.steps[2].current, m.steps[2].total], [3, 30]);
  assert.equal(m.elapsedMs, null);
  assert.equal(m.etaMs, null);
});

test("no evidence at all keeps prepare active", () => {
  const m = buildGenerationProgress({ activity: null, evidence: {}, now: START });
  assert.equal(states(m), "active,pending,pending,pending,pending");
  assert.equal(m.lastActivityAt, null);
});

test("eta needs enough samples and elapsed time", () => {
  const base = { stage: "render", pages: { authored: 80, rendered: 76 } };
  const m = buildGenerationProgress({ activity: activity(base), evidence: {}, now: START + 35 * 60_000 });
  assert.ok(m.etaMs !== null);
  assert.equal(etaMinutes(m.etaMs!), 24);
  assert.equal(buildGenerationProgress({ activity: activity({ ...base, pages: { rendered: 2 } }), evidence: {}, now: START + 35 * 60_000 }).etaMs, null);
  assert.equal(buildGenerationProgress({ activity: activity(base), evidence: {}, now: START + 60_000 }).etaMs, null);
  assert.equal(buildGenerationProgress({ activity: activity({ ...base, pages: { rendered: 128, authored: 128 } }), evidence: {}, now: START + 35 * 60_000 }).etaMs, null);
  assert.equal(etaMinutes(20_000), null);
});

test("lastActivityAt is the max of activity and event evidence", () => {
  const m = buildGenerationProgress({
    activity: activity({ lastProgressAt: "2026-10-22T00:10:00Z" }),
    evidence: { lastProgressAt: "2026-10-22T00:12:00Z" }, now: START,
  });
  assert.equal(m.lastActivityAt, Date.parse("2026-10-22T00:12:00Z"));
});

test("latest event evidence picks the highest sequence progress event", () => {
  const e = latestGenerationEventEvidence([
    { type: "operation.progress", sequence: 2, stage: "authored_author", current: 5, total: 9 },
    { type: "operation.progress", sequence: 1, stage: "authored_outline" },
    { type: "operation.failed", sequence: 3, stage: "x" },
  ]);
  assert.equal(e.stage, "authored_author");
  assert.equal(e.current, 5);
});
