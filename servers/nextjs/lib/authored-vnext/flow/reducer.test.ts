import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORED_VNEXT_FLOW_SAFETY_CONTRACT,
  AUTHORED_VNEXT_SLIDE_COUNT,
  type AuthoredVnextFlowState,
  type FlowEvent,
  type OutlineLayoutPlan,
  type PublishedMasterReference,
  type Sha256Digest,
} from "./types.ts";
import {
  applyFlowEvents,
  authoredVnextFlowReducer,
  createAuthoredVnextFlowState,
  validateOutlineLayout,
} from "./reducer.ts";

const digestA = `sha256:${"a".repeat(64)}` as Sha256Digest;
const digestB = `sha256:${"b".repeat(64)}` as Sha256Digest;
const digestC = `sha256:${"c".repeat(64)}` as Sha256Digest;

const materials = [{ materialId: "material:brief", kind: "document", label: "고객 제안 요청서", digest: digestA }] as const;
const draft = {
  masterId: "master:corporate-blue",
  revision: 4,
  digest: digestB,
  validation: { valid: true, errorCodes: [] },
} as const;
const published: PublishedMasterReference = {
  masterId: draft.masterId,
  revision: 5,
  digest: digestC,
  status: "published",
};
const visualDirection = {
  directionId: "visual:editorial",
  tone: ["editorial", "credible"],
  imagery: ["documentary"],
  density: "balanced",
} as const;

function outline(slideCount: number = AUTHORED_VNEXT_SLIDE_COUNT): OutlineLayoutPlan {
  return {
    planId: "outline:proposal",
    revision: 1,
    slides: Array.from({ length: slideCount }, (_, index) => ({
      slideId: `slide:${index + 1}`,
      ordinal: index + 1,
      title: `${index + 1}번 슬라이드`,
      purpose: "핵심 메시지 전달",
      layoutId: index === 0 ? "layout:long-title" : "layout:standard",
    })),
  };
}

function reachOutline(): AuthoredVnextFlowState {
  return applyFlowEvents(createAuthoredVnextFlowState(), [
    { type: "materials.confirmed", materials },
    { type: "master.draftSelected", draft },
    { type: "master.publishSucceeded", published },
    { type: "visualDirection.confirmed", visualDirection },
  ]).state;
}

test("published Master is a hard gate and its CAS command precedes Visual Direction", () => {
  let state = createAuthoredVnextFlowState();
  state = authoredVnextFlowReducer(state, { type: "materials.confirmed", materials }).state;
  state = authoredVnextFlowReducer(state, { type: "master.draftSelected", draft }).state;

  const blocked = authoredVnextFlowReducer(state, { type: "visualDirection.confirmed", visualDirection });
  assert.equal(blocked.state, state);
  assert.deepEqual(blocked.effects.map((effect) => effect.type), ["flow.transitionRejected"]);

  const publish = authoredVnextFlowReducer(state, { type: "master.publishRequested" });
  assert.deepEqual(publish.effects, [{
    type: "master.publish",
    command: {
      masterId: draft.masterId,
      expectedRevision: draft.revision,
      expectedDigest: draft.digest,
    },
  }]);

  state = authoredVnextFlowReducer(state, { type: "master.publishSucceeded", published }).state;
  assert.equal(state.step, "visualDirection");
  assert.equal("visualDirection" in state.publishedMaster!, false);

  const direction = authoredVnextFlowReducer(state, { type: "visualDirection.confirmed", visualDirection });
  assert.equal(direction.state.step, "outlineLayout");
  assert.equal(direction.state.publishedMaster, published);
  assert.equal(direction.state.visualDirection, visualDirection);
  assert.equal(direction.effects[0].type, "outlineLayout.request");
  if (direction.effects[0].type === "outlineLayout.request") {
    assert.equal(direction.effects[0].command.master, published);
    assert.equal(direction.effects[0].command.visualDirection, visualDirection);
    assert.equal(direction.effects[0].command.slideCount, AUTHORED_VNEXT_SLIDE_COUNT);
  }
});

test("invalid or unpublished Master cannot enter outline or generation", () => {
  const invalidDraft = { ...draft, validation: { valid: false, errorCodes: ["title_fit_failed"] } };
  const state = applyFlowEvents(createAuthoredVnextFlowState(), [
    { type: "materials.confirmed", materials },
    { type: "master.draftSelected", draft: invalidDraft },
  ]).state;
  const publish = authoredVnextFlowReducer(state, { type: "master.publishRequested" });
  assert.equal(publish.effects[0].type, "flow.transitionRejected");

  const generate = authoredVnextFlowReducer(state, {
    type: "generation.requested",
    runId: "run:blocked",
    deckId: "deck:blocked",
    expectedDeckRevision: 0,
    expectedDeckDigest: digestA,
  });
  assert.equal(generate.state, state);
  assert.equal(generate.effects[0].type, "flow.transitionRejected");
});

test("outline requires ninety stable slide IDs with assigned layouts", () => {
  assert.deepEqual(validateOutlineLayout(outline()), []);
  assert.deepEqual(validateOutlineLayout(outline(89)), ["outline_requires_90_slides"]);

  const duplicate = structuredClone(outline());
  duplicate.slides[1].slideId = duplicate.slides[0].slideId;
  duplicate.slides[2].layoutId = "";
  assert.deepEqual(validateOutlineLayout(duplicate), [
    "outline_slide_id_not_stable_unique",
    "outline_layout_required",
  ]);
});

test("materials -> Master -> Visual Direction -> outline/layout -> 90-slide progress -> workbench -> export", () => {
  let state = reachOutline();
  state = authoredVnextFlowReducer(state, { type: "outline.succeeded", plan: outline() }).state;

  const generation = authoredVnextFlowReducer(state, {
    type: "generation.requested",
    runId: "run:90",
    deckId: "deck:proposal",
    expectedDeckRevision: 8,
    expectedDeckDigest: digestA,
  });
  assert.equal(generation.state.step, "generating");
  assert.equal(generation.state.generation?.slides.length, AUTHORED_VNEXT_SLIDE_COUNT);
  assert.equal(generation.effects[0].type, "deck.generate");
  if (generation.effects[0].type === "deck.generate") {
    assert.equal(generation.effects[0].command.master.digest, published.digest);
    assert.equal(generation.effects[0].command.visualDirection.directionId, visualDirection.directionId);
  }
  state = generation.state;

  const progressEvents: FlowEvent[] = Array.from({ length: AUTHORED_VNEXT_SLIDE_COUNT }, (_, index) => ({
    type: "generation.slideProgressed" as const,
    progress: {
      slideId: `slide:${index + 1}`,
      ordinal: index + 1,
      status: "ready" as const,
      percent: 100,
      slideRevision: 1,
    },
  }));
  state = applyFlowEvents(state, progressEvents).state;
  assert.equal(state.step, "workbench");
  assert.equal(state.generation?.slides.filter((slide) => slide.status === "ready").length, AUTHORED_VNEXT_SLIDE_COUNT);

  const exported = authoredVnextFlowReducer(state, {
    type: "export.requested",
    requestId: "export:1",
    format: "pptx",
  });
  assert.equal(exported.state.step, "exportRequest");
  assert.deepEqual(exported.effects, [{
    type: "deck.exportRequest",
    command: {
      requestId: "export:1",
      deckId: "deck:proposal",
      expectedDeckRevision: 8,
      expectedDeckDigest: digestA,
      format: "pptx",
    },
  }]);
});

test("loading, error, stale, and last-known-good recovery are explicit", () => {
  const ready = reachOutline();
  const loaded = authoredVnextFlowReducer(ready, {
    type: "flow.loaded",
    checkpoint: {
      step: ready.step,
      materials: ready.materials,
      masterDraft: ready.masterDraft,
      publishedMaster: ready.publishedMaster,
      visualDirection: ready.visualDirection,
    },
    refreshedAt: "2026-08-13T01:00:00.000Z",
  }).state;
  assert.equal(loaded.availability.status, "ready");

  const loading = authoredVnextFlowReducer(loaded, { type: "flow.loadRequested" });
  assert.equal(loading.state.availability.status, "loading");
  assert.deepEqual(loading.effects, [{ type: "flow.load" }]);

  const failed = authoredVnextFlowReducer(loading.state, {
    type: "flow.loadFailed",
    error: { code: "offline", message: "네트워크 연결 없음", retryable: true },
  }).state;
  assert.equal(failed.availability.status, "error");
  assert.ok(failed.availability.status === "error" && failed.availability.lastKnownGood);

  const restored = authoredVnextFlowReducer(failed, {
    type: "flow.lastKnownGoodRestored",
    refreshedAt: "2026-08-13T01:01:00.000Z",
  }).state;
  assert.equal(restored.availability.status, "ready");
  assert.equal(restored.step, "outlineLayout");

  const stale = authoredVnextFlowReducer(restored, {
    type: "flow.markedStale",
    reason: "server_revision_ahead",
    detectedAt: "2026-08-13T01:02:00.000Z",
  }).state;
  assert.equal(stale.availability.status, "stale");
  assert.ok(stale.availability.status === "stale" && stale.availability.lastKnownGood);
});

test("flow command surface excludes unsafe broad writes", () => {
  assert.deepEqual(AUTHORED_VNEXT_FLOW_SAFETY_CONTRACT, {
    wholeDeckAutosave: false,
    wildcardSlideCommand: false,
    canonicalHtmlWrite: false,
    generationSlideCount: 90,
  });
});
