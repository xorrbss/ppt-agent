import assert from "node:assert/strict";
import test from "node:test";

import {
  MASTER_CONTRACT_KEY_MANIFEST,
  REQUIRED_LAYOUT_KINDS,
  isAuthoredVnextMasterEnabled,
} from "./contracts.ts";
import {
  DEFAULT_MASTER_CONTRACT,
  DEFAULT_VISUAL_DIRECTION,
  TITLE_STRESS_CORPUS,
} from "./fixtures.ts";
import {
  createMasterSetupState,
  masterSetupReducer,
  validateMasterSetup,
} from "./view-model.ts";

test("Master Setup route feature flag is default-off and exact-match enabled", () => {
  assert.equal(isAuthoredVnextMasterEnabled(undefined), true);
  assert.equal(isAuthoredVnextMasterEnabled("false"), false);
  assert.equal(isAuthoredVnextMasterEnabled("TRUE"), false);
  assert.equal(isAuthoredVnextMasterEnabled("true"), true);
});

test("default contract follows the backend-owned root key manifest", () => {
  const optional = new Set(["customLayouts", "semanticElements", "aiEditZones"]);
  assert.deepEqual(
    Object.keys(DEFAULT_MASTER_CONTRACT),
    MASTER_CONTRACT_KEY_MANIFEST.contract.filter(
      (key) => !optional.has(key) || key in DEFAULT_MASTER_CONTRACT,
    ),
  );
});

test("master furniture is locked while legacy slots remain compatibility-only", () => {
  const validation = validateMasterSetup(DEFAULT_MASTER_CONTRACT);
  assert.equal(validation.valid, true, JSON.stringify(validation.errors));
  assert.equal(validation.furnitureBodyDuplicates, 0);
  assert.equal(validation.writableOverlaps, 0);
  assert.equal(validation.titleFitFailures, 0);

  assert.ok(
    Object.values(DEFAULT_MASTER_CONTRACT.furniture).every(
      (item) => item.ownership.locked && !item.ownership.modelWritable,
    ),
  );
});

test("title/body slots can be absent or malformed without changing furniture-only validity", () => {
  for (const layoutKind of REQUIRED_LAYOUT_KINDS) {
    const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
    contract.layouts[layoutKind].slots[0].ownership = {
      locked: true,
      modelWritable: false,
    };
    assert.equal(validateMasterSetup(contract).valid, true, layoutKind);

    const missing = structuredClone(DEFAULT_MASTER_CONTRACT);
    missing.layouts[layoutKind].slots = [];
    assert.equal(validateMasterSetup(missing).valid, true, layoutKind);
  }
});

test("AI edit zones are geometry-only reserved envelopes", () => {
  const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
  contract.aiEditZones = [{
    id: "ai-zone:standard-content",
    scope: { kind: "layout", layoutId: contract.layouts.standard.id },
    bounds: { x: 120, y: 180, width: 880, height: 260 },
  }];
  assert.equal(validateMasterSetup(contract).valid, true);
  assert.deepEqual(Object.keys(contract.aiEditZones[0]).sort(), [
    "bounds",
    "id",
    "scope",
  ]);

  contract.aiEditZones[0].bounds.width = 0;
  const invalid = validateMasterSetup(contract);
  assert.ok(invalid.errors.some((issue) => issue.code === "invalid_ai_edit_zone"));
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

test("legacy fit and stress data is retained as compatibility data", () => {
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

test("legacy slot collisions do not change furniture-only validation", () => {
  const contract = structuredClone(DEFAULT_MASTER_CONTRACT);
  contract.layouts.standard.slots[0].id = contract.furniture.header.id;
  assert.equal(validateMasterSetup(contract).valid, true);
  assert.equal(contract.layouts.standard.slots[0].id, contract.furniture.header.id);
});
