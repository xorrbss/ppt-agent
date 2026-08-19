import assert from "node:assert/strict";
import test from "node:test";

import { MASTER_CONTRACT_KEY_MANIFEST } from "../../../../lib/authored-vnext/master/contracts.ts";
import { DEFAULT_MASTER_CONTRACT } from "../../../../lib/authored-vnext/master/fixtures.ts";
import {
  applyMasterCanvasTransaction,
  masterContractToCanvasScene,
  normalizeMasterForPersistence,
  normalizeAiEditZonesForLayoutEditing,
  normalizeMasterForLayoutEditing,
} from "./master-canvas-adapter.ts";

test("furniture geometry and legacy slot data survive save and re-entry without replacing canonical pins", () => {
  const canonicalDigest = `sha256:${"a".repeat(64)}` as const;
  const revisionDigest = `sha256:${"b".repeat(64)}` as const;
  const logoDigest = `sha256:${"c".repeat(64)}` as const;
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  original.digest = canonicalDigest;
  original.brandAssets.logo = {
    assetId: "asset:ibiz-logo",
    digest: logoDigest,
    altText: "IBIZ logo",
  };
  original.furniture.logo.assetId = "asset:ibiz-logo";

  const legacySlot = structuredClone(original.layouts.standard.slots[0]);
  const edited = applyMasterCanvasTransaction(original, "standard", {
    type: "updateGeometry",
    elementId: "furniture:logo",
    bounds: { x: 104, y: 12, width: 96, height: 48 },
  });
  const savedEnvelope = {
    masterId: edited.masterId,
    revision: 17,
    digest: revisionDigest,
    contract: edited,
  };
  const reentered = JSON.parse(JSON.stringify(savedEnvelope)) as typeof savedEnvelope;
  const logo = masterContractToCanvasScene(reentered.contract, "standard").elements.find(
    (element) => element.id === "furniture:logo",
  );

  assert.deepEqual(logo?.bounds, { x: 104, y: 12, width: 96, height: 48 });
  assert.deepEqual(reentered.contract.layouts.standard.slots[0], legacySlot);
  assert.equal(
    masterContractToCanvasScene(reentered.contract, "standard").elements.some(
      (element) => element.layer === "slot",
    ),
    false,
  );
  assert.deepEqual(logo?.asset, original.brandAssets.logo);
  assert.equal(logo?.ownership.locked, true);
  assert.equal(logo?.ownership.modelWritable, false);
  assert.equal(logo?.editable, true);
  assert.deepEqual(original.furniture.logo.bounds, { x: 76, y: 30, width: 32, height: 32 });
  assert.deepEqual(original.layouts.standard.slots[0], legacySlot);
  const optionalContractKeys = new Set(["customLayouts", "semanticElements", "aiEditZones"]);
  assert.deepEqual(
    Object.keys(reentered.contract),
    MASTER_CONTRACT_KEY_MANIFEST.contract.filter(
      (key) => !optionalContractKeys.has(key) || key in reentered.contract,
    ),
  );
  assert.equal(reentered.contract.contractVersion, "deckmaster-contract/v1");
  assert.equal(reentered.contract.masterId, original.masterId);
  assert.equal(reentered.contract.semanticVersion, original.semanticVersion);
  assert.equal(reentered.contract.digest, canonicalDigest);
  assert.equal(reentered.revision, 17);
  assert.equal(reentered.digest, revisionDigest);
  assert.equal("konva" in reentered.contract, false);
  assert.equal("elements" in reentered.contract, false);
});

test("fixed repeated text and a bound logo delete atomically while legacy slots stay hidden", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  original.brandAssets.logo = {
    assetId: "asset:canonical-logo",
    digest: `sha256:${"f".repeat(64)}` as const,
    altText: "Canonical logo",
  };
  original.furniture.logo.assetId = "asset:canonical-logo";
  const undoSnapshot = structuredClone(original);
  const preservedLayouts = structuredClone(original.layouts);
  const preservedFooter = structuredClone(original.furniture.footer);
  const preservedDivider = structuredClone(original.furniture.divider);
  const preservedPageNumber = structuredClone(original.furniture.pageNumber);

  const beforeScene = masterContractToCanvasScene(original, "standard");
  assert.equal(beforeScene.elements.find((item) => item.id === "furniture:header")?.deletable, true);
  assert.equal(beforeScene.elements.find((item) => item.id === "furniture:logo")?.deletable, true);

  // One adapter transaction is one editor history entry, including both
  // canonical clears. The pre-transaction snapshot is therefore one-step undo.
  const deleted = applyMasterCanvasTransaction(original, "standard", {
    type: "delete",
    elementIds: ["furniture:header", "furniture:logo"],
  });

  assert.equal(deleted.furniture.header.text, "");
  assert.equal(deleted.brandAssets.logo, null);
  assert.equal("assetId" in deleted.furniture.logo, false);
  assert.deepEqual(deleted.furniture.footer, preservedFooter);
  assert.deepEqual(deleted.furniture.divider, preservedDivider);
  assert.deepEqual(deleted.furniture.pageNumber, preservedPageNumber);
  assert.deepEqual(deleted.layouts, preservedLayouts);

  const standardAfter = masterContractToCanvasScene(deleted, "standard");
  const otherLayoutAfter = masterContractToCanvasScene(deleted, "longTitle");
  assert.equal(standardAfter.elements.find((item) => item.id === "furniture:header")?.visible, false);
  assert.equal(otherLayoutAfter.elements.find((item) => item.id === "furniture:header")?.visible, false);
  assert.equal(standardAfter.elements.find((item) => item.id === "furniture:footer")?.visible, true);
  assert.equal(otherLayoutAfter.elements.find((item) => item.id === "furniture:footer")?.visible, true);

  assert.equal(standardAfter.elements.some((item) => item.layer === "slot"), false);
  assert.deepEqual(deleted.layouts, preservedLayouts);
  assert.equal(standardAfter.elements.find((item) => item.id === "furniture:divider")?.deletable, false);
  assert.equal(standardAfter.elements.find((item) => item.id === "furniture:page-number")?.deletable, false);

  const undone = undoSnapshot;
  assert.deepEqual(undone, original);
  assert.equal(undone.furniture.header.text, "PRESENTON · AUTHORED vNEXT");
  assert.equal(undone.furniture.logo.assetId, "asset:canonical-logo");
  assert.deepEqual(
    applyMasterCanvasTransaction(undone, "standard", {
      type: "delete",
      elementIds: ["furniture:header", "furniture:logo"],
    }),
    deleted,
  );
});

test("AI edit zones persist only their reserved-envelope geometry", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  const inserted = applyMasterCanvasTransaction(original, "standard", {
    type: "addAiEditZone",
    zone: {
      id: "ai-zone:standard-content",
      scope: { kind: "layout", layoutId: original.layouts.standard.id },
      bounds: { x: 100, y: 180, width: 920, height: 300 },
    },
  });
  const zone = inserted.aiEditZones?.[0];
  assert.deepEqual(zone, {
    id: "ai-zone:standard-content",
    scope: { kind: "layout", layoutId: original.layouts.standard.id },
    bounds: { x: 100, y: 180, width: 920, height: 300 },
  });
  assert.deepEqual(Object.keys(zone ?? {}).sort(), ["bounds", "id", "scope"]);
  assert.equal(masterContractToCanvasScene(inserted, "standard").elements.find((item) => item.id === zone?.id)?.kind, "aiEditZone");
  assert.equal(masterContractToCanvasScene(inserted, "cover").elements.some((item) => item.id === zone?.id), false);

  const moved = applyMasterCanvasTransaction(inserted, "standard", {
    type: "updateAiEditZoneGeometry",
    zoneId: "ai-zone:standard-content",
    bounds: { x: 120, y: 200, width: 880, height: 280 },
  });
  assert.deepEqual(moved.aiEditZones?.[0].bounds, { x: 120, y: 200, width: 880, height: 280 });
  assert.deepEqual(moved.layouts, original.layouts);
  assert.deepEqual(moved.fontManifest, original.fontManifest);
  assert.deepEqual(moved.semanticStyles, original.semanticStyles);
  assert.deepEqual(
    applyMasterCanvasTransaction(moved, "standard", {
      type: "deleteAiEditZones",
      zoneIds: ["ai-zone:standard-content"],
    }).aiEditZones,
    [],
  );
});

test("AI edit zones clear fallback text when fallback behavior is disabled", () => {
  const zone = {
    id: "ai-zone:fallback-cleanup",
    scope: { kind: "layout" as const, layoutId: "layout:standard" },
    bounds: { x: 100, y: 100, width: 320, height: 120 },
    emptyBehavior: "fallback" as const,
    fallbackText: "내용 없음",
  };
  const withZone = applyMasterCanvasTransaction(DEFAULT_MASTER_CONTRACT, "standard", {
    type: "addAiEditZone",
    zone,
  });

  assert.throws(
    () => applyMasterCanvasTransaction(withZone, "standard", {
      type: "updateAiEditZone",
      zoneId: zone.id,
      patch: { emptyBehavior: "hidden" },
    }),
    /fallback text requires fallback behavior/,
  );

  const cleaned = applyMasterCanvasTransaction(withZone, "standard", {
    type: "updateAiEditZone",
    zoneId: zone.id,
    patch: { emptyBehavior: "hidden", fallbackText: null },
  });
  assert.equal(cleaned.aiEditZones?.[0]?.emptyBehavior, "hidden");
  assert.equal(cleaned.aiEditZones?.[0]?.fallbackText, undefined);

  const stale = {
    ...withZone,
    aiEditZones: withZone.aiEditZones?.map((item) => ({
      ...item,
      emptyBehavior: "hidden" as const,
    })),
  };
  const persistenceReady = normalizeMasterForPersistence(stale);
  assert.equal(persistenceReady.aiEditZones?.[0]?.fallbackText, undefined);
});

test("shared AI edit zones split into exact, independently editable layout zones", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  original.aiEditZones = [
    {
      id: "ai-zone:shared-content",
      scope: { kind: "common" },
      bounds: { x: 20, y: 20, width: 1240, height: 690 },
      instruction: "Summarize this layout independently",
    },
  ];
  const layoutIds = Object.values(original.layouts).map((layout) => layout.id);
  const split = applyMasterCanvasTransaction(original, "standard", {
    type: "splitAiEditZoneByLayout",
    zoneId: "ai-zone:shared-content",
    layoutIds,
    idFactory: (layoutId) => `ai-zone:${layoutId.replace("layout:", "")}`,
  });

  assert.equal(split.aiEditZones?.length, layoutIds.length);
  assert.deepEqual(
    split.aiEditZones?.map((zone) => zone.scope),
    layoutIds.map((layoutId) => ({ kind: "layout", layoutId })),
  );
  assert.equal(
    split.aiEditZones?.every((zone) =>
      JSON.stringify(zone.bounds) === JSON.stringify(original.aiEditZones?.[0].bounds)
    ),
    true,
  );

  const standardZone = split.aiEditZones?.find(
    (zone) => zone.scope.kind === "layout" &&
      zone.scope.layoutId === original.layouts.standard.id,
  );
  assert.ok(standardZone);
  const moved = applyMasterCanvasTransaction(split, "standard", {
    type: "updateAiEditZoneGeometry",
    zoneId: standardZone.id,
    bounds: { x: 200, y: 220, width: 400, height: 180 },
  });
  assert.deepEqual(
    moved.aiEditZones?.find((zone) => zone.id === standardZone.id)?.bounds,
    { x: 200, y: 220, width: 400, height: 180 },
  );
  assert.equal(
    moved.aiEditZones?.filter((zone) => zone.id !== standardZone.id)
      .every((zone) => zone.bounds.x === 20 && zone.bounds.y === 20),
    true,
  );
});

test("loaded legacy AI zones preserve exact geometry before save and publish", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  original.aiEditZones = [
    {
      id: "ai-zone:legacy-shared",
      scope: { kind: "common" },
      bounds: { x: 20, y: 20, width: 1240, height: 690 },
      instruction: "Create content for this layout",
    },
  ];

  const normalized = normalizeAiEditZonesForLayoutEditing(original);
  assert.notEqual(normalized, original);
  assert.equal(
    normalized.aiEditZones?.length,
    Object.keys(original.layouts).length,
  );
  assert.equal(
    normalized.aiEditZones?.every((zone) =>
      zone.scope.kind === "layout" &&
      JSON.stringify(zone.bounds) === JSON.stringify(original.aiEditZones?.[0].bounds)
    ),
    true,
  );
  assert.equal(
    normalizeAiEditZonesForLayoutEditing(normalized),
    normalized,
  );
});

test("cover section and body furniture edit independently after normalization", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  const normalized = normalizeMasterForLayoutEditing(original);

  assert.equal(
    Object.values(normalized.furniture).every((item) => item.visibility === "hidden"),
    true,
  );
  const localHeaders = normalized.semanticElements?.filter(
    (element) => element.content.role === "header",
  ) ?? [];
  assert.equal(localHeaders.length, 4);
  assert.deepEqual(
    new Set(localHeaders.map((element) =>
      element.scope.kind === "layout" ? element.scope.layoutId : "common"
    )),
    new Set(Object.values(original.layouts).map((layout) => layout.id)),
  );

  const sectionHeader = localHeaders.find(
    (element) => element.scope.kind === "layout" &&
      element.scope.layoutId === original.layouts.section.id,
  );
  const standardHeader = localHeaders.find(
    (element) => element.scope.kind === "layout" &&
      element.scope.layoutId === original.layouts.standard.id,
  );
  assert.ok(sectionHeader);
  assert.ok(standardHeader);
  const edited = applyMasterCanvasTransaction(normalized, "section", {
    type: "updateContent",
    elementId: sectionHeader.id,
    content: { text: "SECTION ONLY" },
  });
  assert.equal(
    edited.semanticElements?.find((element) => element.id === sectionHeader.id)?.content.text,
    "SECTION ONLY",
  );
  assert.equal(
    edited.semanticElements?.find((element) => element.id === standardHeader.id)?.content.text,
    original.furniture.header.text,
  );
  assert.equal(normalizeMasterForLayoutEditing(normalized), normalized);
});

test("legacy placeholder and AI-text records round-trip without entering furniture setup", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  original.semanticElements = [
    {
      id: "element:legacy-title",
      type: "placeholder",
      scope: { kind: "layout", layoutId: original.layouts.standard.id },
      bounds: { x: 80, y: 100, width: 1_000, height: 120 },
      rotation: 0,
      zIndex: 2,
      visibility: "layoutOnly",
      editableByUser: true,
      editableByAI: true,
      locked: false,
      style: { fontFamily: "Legacy Font", fontSize: 42 },
      content: { placeholderRole: "title", placeholderIndex: 0 },
    },
    {
      id: "element:legacy-ai-text",
      type: "text",
      scope: { kind: "layout", layoutId: original.layouts.standard.id },
      bounds: { x: 100, y: 260, width: 800, height: 96 },
      rotation: 0,
      zIndex: 3,
      visibility: "layoutOnly",
      editableByUser: true,
      editableByAI: true,
      locked: false,
      style: { fontFamily: "Legacy Font", fontSize: 22 },
      content: {
        role: "ai-text-zone",
        text: "legacy example",
        aiBinding: { instruction: "legacy", emptyBehavior: "blank" },
      },
    },
  ];
  const roundTripped = JSON.parse(JSON.stringify(original)) as typeof original;
  const scene = masterContractToCanvasScene(roundTripped, "standard");
  assert.equal(scene.elements.some((element) => element.id === "element:legacy-title"), false);
  assert.equal(scene.elements.some((element) => element.id === "element:legacy-ai-text"), false);
  assert.throws(
    () => applyMasterCanvasTransaction(roundTripped, "standard", {
      type: "updateGeometry",
      elementId: "element:legacy-ai-text",
      bounds: { x: 120, y: 280, width: 760, height: 96 },
    }),
    /Only confirmed master furniture can be edited in furniture setup/,
  );
  assert.deepEqual(roundTripped.semanticElements, original.semanticElements);
});

test("confirmed background-decoration furniture remains editable", () => {
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  const inserted = applyMasterCanvasTransaction(original, "standard", {
    type: "insertElement",
    element: {
      id: "element:background-decoration",
      type: "shape",
      scope: { kind: "layout", layoutId: original.layouts.standard.id },
      bounds: { x: 40, y: 44, width: 180, height: 84 },
      rotation: 0,
      zIndex: 100,
      visibility: "layoutOnly",
      editableByUser: true,
      editableByAI: false,
      locked: false,
      style: { shape: "ellipse", fill: "#dbeafe" },
      content: { role: "background-decoration" },
    },
  });
  assert.equal(
    masterContractToCanvasScene(inserted, "standard").elements.some(
      (element) => element.id === "element:background-decoration",
    ),
    true,
  );
  assert.deepEqual(
    applyMasterCanvasTransaction(inserted, "standard", {
      type: "updateGeometry",
      elementId: "element:background-decoration",
      bounds: { x: 56, y: 60, width: 200, height: 96 },
    }).semanticElements?.[0].bounds,
    { x: 56, y: 60, width: 200, height: 96 },
  );
});

test("shared logo range uses one asset reference with override, restore, and atomic undo", () => {
  const logo = {
    assetId: "asset:company-logo",
    digest: `sha256:${"d".repeat(64)}` as const,
    altText: "Company logo",
  };
  const original = structuredClone(DEFAULT_MASTER_CONTRACT);
  original.brandAssets.logo = {
    assetId: "asset:legacy-canonical-logo",
    digest: `sha256:${"e".repeat(64)}` as const,
    altText: "Legacy canonical logo",
  };
  original.furniture.logo.assetId = "asset:legacy-canonical-logo";
  const undoSnapshot = structuredClone(original);
  const insertion = {
    type: "insertSharedLogo" as const,
    asset: logo,
    layoutIds: [
      original.layouts.section.id,
      original.layouts.standard.id,
      original.layouts.longTitle.id,
      original.layouts.standard.id,
    ],
    anchor: "bottomRight" as const,
    groupId: "company-logo",
  };

  const inserted = applyMasterCanvasTransaction(original, "standard", insertion);
  const members = (inserted.semanticElements ?? []).filter(
    (element) => element.content.sharedGroupId === insertion.groupId,
  );

  assert.equal(members.length, 3);
  assert.deepEqual(
    members.map((element) => element.scope),
    [
      { kind: "layout", layoutId: original.layouts.longTitle.id },
      { kind: "layout", layoutId: original.layouts.section.id },
      { kind: "layout", layoutId: original.layouts.standard.id },
    ],
  );
  assert.ok(members.every((element) => element.content.assetId === logo.assetId));
  assert.ok(members.every((element) => element.content.digest === logo.digest));
  assert.ok(members.every((element) => element.content.mediaType === "image/png"));
  assert.ok(members.every((element) => element.content.sharedOverride === false));
  assert.ok(members.every((element) => element.content.role === "brand-logo"));
  assert.equal(inserted.brandAssets.logo, null);
  assert.equal("assetId" in inserted.furniture.logo, false);
  assert.deepEqual(inserted.furniture.logo.bounds, original.furniture.logo.bounds);
  assert.deepEqual(inserted.furniture.header, original.furniture.header);

  const initialBounds = members[0].bounds;
  const safeArea = original.canvas.safeArea;
  assert.ok(initialBounds.x >= safeArea.x && initialBounds.y >= safeArea.y);
  assert.ok(initialBounds.x + initialBounds.width <= safeArea.x + safeArea.width);
  assert.ok(initialBounds.y + initialBounds.height <= safeArea.y + safeArea.height);
  assert.equal(
    safeArea.x + safeArea.width - initialBounds.x - initialBounds.width,
    safeArea.y + safeArea.height - initialBounds.y - initialBounds.height,
  );

  const standard = members.find(
    (element) => element.scope.kind === "layout" && element.scope.layoutId === original.layouts.standard.id,
  )!;
  const section = members.find(
    (element) => element.scope.kind === "layout" && element.scope.layoutId === original.layouts.section.id,
  )!;
  const longTitle = members.find(
    (element) => element.scope.kind === "layout" && element.scope.layoutId === original.layouts.longTitle.id,
  )!;
  const overridden = applyMasterCanvasTransaction(inserted, "section", {
    type: "setSharedLogoOverride",
    elementId: section.id,
  });
  const synchronizedBounds = { x: 930, y: 596, width: 210, height: 84 };
  const synchronized = applyMasterCanvasTransaction(overridden, "standard", {
    type: "updateGeometry",
    elementId: standard.id,
    bounds: synchronizedBounds,
    rotation: 3,
  });
  const styled = applyMasterCanvasTransaction(synchronized, "standard", {
    type: "updateStyle",
    elementIds: [standard.id],
    style: { opacity: 0.42 },
  });
  const byId = (contract: typeof styled, id: string) =>
    contract.semanticElements?.find((element) => element.id === id)!;

  assert.deepEqual(byId(styled, standard.id).bounds, synchronizedBounds);
  assert.deepEqual(byId(styled, longTitle.id).bounds, synchronizedBounds);
  assert.equal(byId(styled, standard.id).rotation, 3);
  assert.equal(byId(styled, longTitle.id).rotation, 3);
  assert.equal(byId(styled, standard.id).style.opacity, 0.42);
  assert.equal(byId(styled, longTitle.id).style.opacity, 0.42);
  assert.deepEqual(byId(styled, section.id).bounds, initialBounds);
  assert.equal(byId(styled, section.id).rotation, 0);
  assert.equal(byId(styled, section.id).style.opacity, 1);
  assert.equal(byId(styled, section.id).content.sharedOverride, true);

  const locallyMoved = applyMasterCanvasTransaction(styled, "section", {
    type: "updateGeometry",
    elementId: section.id,
    bounds: { x: 100, y: 120, width: 160, height: 64 },
    rotation: -2,
  });
  const restored = applyMasterCanvasTransaction(locallyMoved, "section", {
    type: "restoreSharedLogo",
    elementId: section.id,
  });
  const restoredSection = byId(restored, section.id);
  assert.deepEqual(restoredSection.bounds, synchronizedBounds);
  assert.equal(restoredSection.rotation, 3);
  assert.equal(restoredSection.style.opacity, 0.42);
  assert.equal(restoredSection.content.sharedOverride, false);
  assert.equal(restoredSection.content.assetId, logo.assetId);
  assert.equal(restoredSection.content.digest, logo.digest);
  assert.throws(
    () => applyMasterCanvasTransaction(restored, "standard", {
      type: "updateImagePresentation",
      elementId: standard.id,
      assetId: "asset:replacement-must-not-fork-the-group",
    }),
    /one digest-pinned asset reference/,
  );

  const deleted = applyMasterCanvasTransaction(restored, "standard", {
    type: "delete",
    elementIds: [standard.id],
  });
  assert.equal(
    deleted.semanticElements?.some(
      (element) => element.content.sharedGroupId === insertion.groupId,
    ),
    false,
  );
  assert.equal(deleted.brandAssets.logo, null);
  assert.equal("assetId" in deleted.furniture.logo, false);

  // The editor records each semantic transaction as one history point. Keeping
  // the immutable pre-transaction contract is therefore the complete undo;
  // deterministic replay is the corresponding redo.
  assert.deepEqual(original, undoSnapshot);
  const undone = undoSnapshot;
  assert.deepEqual(undone, original);
  assert.equal(undone.furniture.logo.assetId, "asset:legacy-canonical-logo");
  assert.equal(undone.brandAssets.logo?.assetId, "asset:legacy-canonical-logo");
  assert.deepEqual(
    applyMasterCanvasTransaction(undone, "standard", insertion),
    inserted,
  );
});
