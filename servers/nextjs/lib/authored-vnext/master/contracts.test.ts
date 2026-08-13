import assert from "node:assert/strict";
import test from "node:test";

import {
  MASTER_CONTRACT_KEY_MANIFEST,
  MasterContractError,
  REQUIRED_LAYOUT_KINDS,
  isAuthoredVnextMasterEnabled,
} from "./contracts.ts";
import {
  DEFAULT_MASTER_CONTRACT,
  DEFAULT_VISUAL_DIRECTION,
  TITLE_STRESS_CORPUS,
} from "./fixtures.ts";
import {
  buildPreviewScene,
  createMasterSetupState,
  createSlideSpec,
  masterSetupReducer,
  validateMasterSetup,
} from "./view-model.ts";

test("Master Setup route feature flag is default-off and exact-match enabled", () => {
  assert.equal(isAuthoredVnextMasterEnabled(undefined), false);
  assert.equal(isAuthoredVnextMasterEnabled("false"), false);
  assert.equal(isAuthoredVnextMasterEnabled("TRUE"), false);
  assert.equal(isAuthoredVnextMasterEnabled("true"), true);
});

test("default contract follows the backend-owned root key manifest", () => {
  assert.deepEqual(Object.keys(DEFAULT_MASTER_CONTRACT), [
    ...MASTER_CONTRACT_KEY_MANIFEST.contract,
  ]);
  const slide = createSlideSpec(
    DEFAULT_MASTER_CONTRACT,
    "longTitle",
    TITLE_STRESS_CORPUS[4],
  );
  assert.deepEqual(Object.keys(slide), [
    ...MASTER_CONTRACT_KEY_MANIFEST.slideDraft,
  ]);
  assert.equal(slide.slideId, "slide:master-setup-preview");
  assert.equal("windowsFontFallback" in slide.title, false);
});

test("master furniture is locked and body slots are model-writable in separate layers", () => {
  const validation = validateMasterSetup(DEFAULT_MASTER_CONTRACT);
  assert.equal(validation.valid, true, JSON.stringify(validation.errors));
  assert.equal(validation.furnitureBodyDuplicates, 0);
  assert.equal(validation.writableOverlaps, 0);
  assert.equal(validation.titleFitFailures, 0);

  for (const layoutKind of REQUIRED_LAYOUT_KINDS) {
    const scene = buildPreviewScene(
      DEFAULT_MASTER_CONTRACT,
      createSlideSpec(
        DEFAULT_MASTER_CONTRACT,
        layoutKind,
        TITLE_STRESS_CORPUS[0],
      ),
    );
    assert.equal(
      scene.layoutId,
      DEFAULT_MASTER_CONTRACT.layouts[layoutKind].id,
    );
    const locked = scene.items.filter(
      (item) => item.layer === "masterFurniture",
    );
    const writable = scene.items.filter(
      (item) => item.layer === "modelWritableBody",
    );
    assert.equal(locked.length, 5);
    assert.ok(locked.every((item) => item.locked && !item.modelWritable));
    assert.ok(writable.every((item) => !item.locked && item.modelWritable));
    assert.deepEqual(writable.map((item) => item.role).sort(), [
      "body",
      "title",
    ]);
    assert.equal(
      new Set(locked.map((item) => item.id)).intersection(
        new Set(writable.map((item) => item.id)),
      ).size,
      0,
    );
  }
});

test("all four layouts require distinct editable title and body placeholders", () => {
  for (const layoutKind of REQUIRED_LAYOUT_KINDS) {
    const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
    contract.layouts[layoutKind].slots[0].ownership = {
      locked: true,
      modelWritable: false,
    };
    const ownership = validateMasterSetup(contract);
    assert.ok(
      ownership.errors.some(
        (issue) => issue.code === "ownership_boundary_violation",
      ),
      layoutKind,
    );

    const missing = structuredClone(DEFAULT_MASTER_CONTRACT);
    missing.layouts[layoutKind].slots = missing.layouts[
      layoutKind
    ].slots.filter((slot) => slot.role !== "body");
    const missingPlaceholder = validateMasterSetup(missing);
    assert.ok(
      missingPlaceholder.errors.some(
        (issue) => issue.code === "missing_editable_placeholder",
      ),
      layoutKind,
    );
  }
});

test("Visual Direction remains separate from the immutable master contract", () => {
  assert.equal("visualDirection" in DEFAULT_MASTER_CONTRACT, false);
  const state = createMasterSetupState(
    DEFAULT_MASTER_CONTRACT,
    DEFAULT_VISUAL_DIRECTION,
  );
  const changed = masterSetupReducer(state, {
    type: "visualDirectionChanged",
    visualDirection: { ...DEFAULT_VISUAL_DIRECTION, tone: ["editorial"] },
  });
  assert.deepEqual(changed.visualDirection.tone, ["editorial"]);
  assert.equal(changed.contract, state.contract);
});

test("stress corpus covers Korean, English, mixed, and numeric-parenthetical fixed lines", () => {
  assert.deepEqual(
    [...new Set(TITLE_STRESS_CORPUS.map((entry) => entry.category))].sort(),
    ["en", "ko", "mixed", "numericParenthetical"],
  );
  for (const entry of TITLE_STRESS_CORPUS) {
    assert.ok(
      entry.expectedLines.length >= 1 && entry.expectedLines.length <= 3,
    );
    assert.equal(
      entry.text.trim().split(/\s+/).join(" "),
      entry.expectedLines.join(" ").trim().split(/\s+/).join(" "),
    );
  }
});

test("furniture/body namespace collision fails closed", () => {
  const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
  contract.layouts.standard.slots[0].id = contract.furniture.header.id;
  const slide = createSlideSpec(contract, "standard", TITLE_STRESS_CORPUS[0]);
  assert.throws(() => buildPreviewScene(contract, slide), MasterContractError);
});
