import assert from "node:assert/strict";
import test from "node:test";

import {
  adaptDeckSnapshotToWorkbenchDocument,
  createEmptyWorkbenchState,
  isWorkbenchOperation,
  lastKnownGood,
  parseDeckDocument,
  parseWorkbenchOperation,
  selectInspectorNode,
  selectNextWorkbenchEffect,
  WorkbenchInvariantError,
  WorkbenchParseError,
  workbenchReducer,
  type CasPin,
  type DeckDocument,
  type Digest,
  type OperationId,
  type SlideId,
  type WorkbenchOperation,
  type WorkbenchState,
} from "./index.ts";

const digestA = `sha256:${"a".repeat(64)}` as Digest;
const digestB = `sha256:${"b".repeat(64)}` as Digest;
const digestC = `sha256:${"c".repeat(64)}` as Digest;
const digestD = `sha256:${"d".repeat(64)}` as Digest;
const slideId = "slide:one" as SlideId;

function deck(overrides: Partial<Pick<DeckDocument, "revision" | "digest" | "sceneDigest" | "slides">> = {}): DeckDocument {
  return {
    schemaVersion: "authored-workbench/v1",
    id: "deck:test",
    revision: overrides.revision ?? 1,
    digest: overrides.digest ?? digestA,
    sceneDigest: overrides.sceneDigest ?? digestB,
    assetManifest: [{ assetId: "asset:old", digest: digestB, mediaType: "image/png", storageReference: "/assets/old.png", sizeBytes: 128 }],
    dataManifest: [{ dataId: "data:kpis", digest: digestC, kind: "table", payload: { cells: [["42"]] } }],
    master: { id: "master:published", revision: 4, digest: digestD, layer: "master", locked: true },
    slides: overrides.slides ?? [
      {
        id: slideId,
        revision: 2,
        digest: digestC,
        layoutId: "layout:standard",
        nodes: [
          {
            id: "element:title",
            kind: "text",
            layer: "body",
            zOrder: 1,
            rotation: 0,
            semanticRole: "title",
            editability: { text: true, geometry: true, asset: false, data: false, style: true },
            role: "title",
            text: "원본 제목",
            bounds: { x: 40, y: 40, width: 800, height: 80 },
          },
          {
            id: "element:hero",
            kind: "asset",
            layer: "body",
            zOrder: 2,
            rotation: 0,
            semanticRole: "hero-image",
            editability: { text: false, geometry: true, asset: true, data: false, style: false },
            assetId: "asset:old",
            digest: digestB,
            altText: "기존 이미지",
            crop: "contain",
            fit: "contain",
            bounds: { x: 40, y: 140, width: 500, height: 300 },
          },
          {
            id: "element:kpis",
            kind: "table",
            layer: "body",
            zOrder: 3,
            rotation: 0,
            semanticRole: "kpiTable",
            editability: { text: false, geometry: true, asset: false, data: true, style: false },
            dataId: "data:kpis",
            dataDigest: digestC,
            rows: 1,
            columns: 1,
            dataPayload: { cells: [["42"]] },
            bounds: { x: 560, y: 140, width: 300, height: 200 },
          },
        ],
      },
    ],
  };
}

function loaded(document = deck()): WorkbenchState {
  return workbenchReducer(createEmptyWorkbenchState(), { type: "deck/loadSucceeded", deck: document });
}

function pin(document = deck()): CasPin {
  return {
    deckRevision: document.revision,
    deckDigest: document.digest,
    sceneDigest: document.sceneDigest,
    slideRevision: document.slides[0].revision,
    slideDigest: document.slides[0].digest,
    masterDigest: document.master.digest,
  };
}

type OperationInput<T> = T extends WorkbenchOperation ? Omit<T, "operationId" | "slideId" | "base"> : never;

function operation(operationId: OperationId, value: OperationInput<WorkbenchOperation>): WorkbenchOperation {
  return { ...value, operationId, slideId, base: pin() } as WorkbenchOperation;
}

function titleText(state: WorkbenchState): string | undefined {
  const node = selectInspectorNode(state, "element:title");
  return node?.kind === "text" ? node.text : undefined;
}

test("unknown parsers enforce locked master, stable ids, body nodes, and typed operations", () => {
  const canonicalRaw = {
    schemaVersion: "authored-workbench/v1",
    id: "deck:test",
    revision: 1,
    digest: digestA,
    sceneDigest: digestB,
    assetManifest: [],
    dataManifest: [],
    master: { id: "master:published", revision: 4, digest: digestD, layer: "master", locked: true },
    slides: [{
      slideId,
      revision: 2,
      slideDigest: digestC,
      layoutId: "layout:standard",
      elements: [{
        elementId: "element:title",
        kind: "text",
        layer: "body",
        zOrder: 1,
        bounds: { x: 40, y: 40, width: 800, height: 80 },
        rotation: 0,
        semanticRole: "title",
        editability: { text: true, geometry: true, asset: false, data: false, style: true },
        text: "원본 제목",
      }, {
        elementId: "element:canvas-background",
        kind: "shape",
        layer: "body",
        zOrder: -100,
        bounds: { x: 0, y: 0, width: 1280, height: 720 },
        rotation: 0,
        semanticRole: "canvasBackground",
        editability: { text: false, geometry: false, asset: false, data: false, style: false },
        shape: "rect",
        fill: "#111827",
      }],
    }],
  };
  const parsed = parseDeckDocument(canonicalRaw);
  assert.equal(parsed.master.locked, true);
  assert.equal(parsed.slides[0].nodes[0].id, "element:title");
  const canvasBackground = parsed.slides[0].nodes.find((node) => node.semanticRole === "canvasBackground");
  assert.equal(canvasBackground?.kind, "shape");
  assert.equal(canvasBackground?.kind === "shape" ? canvasBackground.fill : undefined, "#111827");

  const raw: unknown = {
    kind: "text.replace",
    operationId: "op:ime-1",
    slideId,
    elementId: "element:title",
    text: "한글 입력",
    base: pin(),
  };
  assert.equal(isWorkbenchOperation(raw), true);
  assert.equal(parseWorkbenchOperation(raw).kind, "text.replace");

  const unlocked: unknown = { ...canonicalRaw, master: { ...canonicalRaw.master, locked: false } };
  assert.throws(() => parseDeckDocument(unlocked), WorkbenchParseError);
  assert.equal(isWorkbenchOperation({ kind: "html.write", html: "<script />" }), false);
});

test("FastAPI DeckSnapshot adapter derives deterministic title/body ids and fixed layout bounds", () => {
  const snapshot: unknown = {
    schemaVersion: "deck-document/v1",
    deckId: "deck:wire",
    revision: 3,
    digest: digestC,
    masterId: "master:published",
    masterRevision: 4,
    masterDigest: digestD,
    slides: [
      {
        schemaVersion: "slidespec/v2",
        slideId: "slide:kpi-01",
        revision: 2,
        layoutId: "layout:long-title",
        title: { text: "긴 제목", lines: ["긴 제목"] },
        body: "본문",
      },
    ],
  };
  const adapted = adaptDeckSnapshotToWorkbenchDocument(snapshot);
  assert.equal(adapted.master.locked, true);
  assert.deepEqual(adapted.slides[0].nodes.map((node) => node.id), [
    "element:kpi-01:title",
    "element:kpi-01:body",
  ]);
  assert.equal(adapted.slides[0].nodes[0].bounds.height, 150);
  assert.equal(adapted.slides[0].nodes[1].bounds.y, 230);
  assert.equal("html" in adapted, false);
});

test("actual Hybrid Scene receipt keeps Korean/English Unicode fixtures and enforces one native-or-raster owner per QID", () => {
  const snapshot: unknown = {
    schemaVersion: "deck-generation-snapshot/v1",
    deckId: "deck:actual-hybrid-fixture",
    revision: 7,
    digest: digestA,
    sourceDigest: digestB,
    master: { masterId: "master:published", masterRevision: 4, masterDigest: digestD, layoutRegistryDigest: digestA, fontManifestDigest: digestB },
    scene: {
      schemaVersion: "deck-scene-ir/v1",
      deckId: "deck:actual-hybrid-fixture",
      revision: 7,
      sceneDigest: digestC,
      compositionDigest: digestD,
      assetManifest: [
        { assetId: "asset:hero", assetDigest: digestA, mediaType: "image/png", storageReference: "/api/assets/hero.png?rev=7", sizeBytes: 128 },
        { assetId: "asset:raster-complex", assetDigest: digestB, mediaType: "image/png", storageReference: "/api/assets/raster-complex.png?rev=7", sizeBytes: 256 },
      ],
      dataManifest: [{ dataId: "data:edge-table", dataDigest: digestD, kind: "table", payload: { cells: [["한국어 장문", "English long-form text 😀\ud83d\ude80"], ["", "extreme-width"]], columnWidthsPt: [24, 420] } }],
      capabilities: [],
      slides: [{
        schemaVersion: "slide-scene-ir/v1",
        slideId: "slide:actual-hybrid-fixture",
        revision: 7,
        slideDigest: digestB,
        elements: [
          { elementId: "qid:title-ko-en", kind: "text", zOrder: 1, bounds: { x: 20, y: 20, width: 920, height: 96 }, rotation: 0, semanticRole: "title", editability: { text: true, geometry: true, asset: false, data: false, style: true }, text: "한글/English long title 😀\ud83d\ude80" },
          { elementId: "qid:hero-crop", kind: "image", zOrder: 2, bounds: { x: 20, y: 130, width: 420, height: 300 }, rotation: 0, semanticRole: "hero-image", editability: { text: false, geometry: true, asset: true, data: false, style: false }, assetId: "asset:hero", assetDigest: digestA, altText: "crop boundary", crop: "cover", cropGeometry: { left: 0.12, top: 0, right: 0.08, bottom: 0 } },
          { elementId: "qid:overflow-table", kind: "table", zOrder: 3, bounds: { x: 460, y: 130, width: 480, height: 300 }, rotation: 0, semanticRole: "overflow-table", editability: { text: false, geometry: true, asset: false, data: true, style: false }, dataId: "data:edge-table", dataDigest: digestD, rows: 2, columns: 2 },
          { elementId: "qid:complex-clip-shadow", kind: "shape", zOrder: 4, bounds: { x: 30, y: 450, width: 900, height: 80 }, rotation: 0, semanticRole: "complex-clip-shadow", editability: { text: false, geometry: false, asset: false, data: false, style: false }, shape: "rect" },
        ],
        hybridExport: {
          schemaVersion: "slide-hybrid-export-plan/v1",
          ownerDecisions: [
            { ownerId: "qid:title-ko-en", decision: "nativeEditable", capabilityId: "editable.text.ko-en.v1" },
            { ownerId: "qid:hero-crop", decision: "nativeEditable", capabilityId: "editable.image.rect-crop.v1" },
            { ownerId: "qid:overflow-table", decision: "nativeEditable", capabilityId: "editable.table.basic.v1" },
            { ownerId: "qid:complex-clip-shadow", decision: "rasterIsland", capabilityId: "editable.raster-island-integrity.v1", reasonCode: "unsupported_complex_css" },
          ],
          rasterIslands: [{ islandId: "island:actual-hybrid-fixture.4", sourceOwnerIds: ["qid:complex-clip-shadow"], assetId: "asset:raster-complex", assetDigest: digestB, bounds: { x: 30, y: 450, width: 900, height: 80 }, zBand: { minZ: 4, maxZ: 4 }, rasterPlaneDigest: digestB }],
        },
      }],
    },
    slides: [],
    createdAt: "2026-08-25T00:00:00Z",
  };
  const adapted = adaptDeckSnapshotToWorkbenchDocument(snapshot);
  const plan = adapted.slides[0].hybridExport;
  assert.equal(plan?.ownerDecisions.length, 4);
  assert.equal(plan?.ownerDecisions.filter((entry) => entry.decision === "nativeEditable").length, 3);
  assert.deepEqual(plan?.rasterIslands[0].sourceOwnerIds, ["qid:complex-clip-shadow"]);

  const duplicateRasterOwner = structuredClone(snapshot) as { scene: { slides: Array<{ hybridExport: { rasterIslands: Array<{ sourceOwnerIds: string[] }> } }> } };
  duplicateRasterOwner.scene.slides[0].hybridExport.rasterIslands[0].sourceOwnerIds.push("qid:title-ko-en");
  assert.throws(() => adaptDeckSnapshotToWorkbenchDocument(duplicateRasterOwner), WorkbenchParseError);
});

test("text, geometry, asset, and layout commands preserve stable ids and queue only typed effects", () => {
  const commands: readonly WorkbenchOperation[] = [
    operation("op:text", { kind: "text.replace", elementId: "element:title", text: "수정 제목" }),
    operation("op:geometry", { kind: "geometry.update", elementId: "element:title", bounds: { x: 50, y: 50, width: 700, height: 90 } }),
    operation("op:asset", { kind: "asset.set", elementId: "element:hero", assetId: "asset:new", digest: digestC, altText: "교체 이미지", crop: "cover", mediaType: "image/png", storageReference: "/assets/new.png", sizeBytes: 256 }),
    operation("op:data", { kind: "data.set", elementId: "element:kpis", dataId: "data:kpis", dataDigest: digestB, dataPayload: { cells: [["84"]] }, rows: 1, columns: 1 }),
    operation("op:layout", { kind: "layout.apply", layoutId: "layout:long-title" }),
  ];
  let state = loaded();
  for (const command of commands) {
    state = workbenchReducer(state, { type: "operation/enqueue", operation: command, createdAt: "2026-08-13T00:00:00Z" });
  }

  const current = lastKnownGood(state.deck);
  assert.ok(current);
  assert.equal(current.slides[0].id, slideId);
  assert.equal(current.slides[0].layoutId, "layout:long-title");
  assert.equal(selectInspectorNode(state, "element:title")?.id, "element:title");
  assert.equal(current.slides[0].nodes.find((node) => node.id === "element:hero")?.kind, "asset");
  assert.equal(state.outbox.length, 5);
  assert.deepEqual(selectNextWorkbenchEffect(state), { effect: "dispatchOperation", operation: commands[0] });
  assert.equal("deck" in (selectNextWorkbenchEffect(state) ?? {}), false);
});

test("undo and redo are inverse patches that produce auditable typed outbox entries", () => {
  let state = loaded();
  state = workbenchReducer(state, {
    type: "operation/enqueue",
    operation: operation("op:edit", { kind: "text.replace", elementId: "element:title", text: "편집됨" }),
    createdAt: "2026-08-13T00:00:00Z",
  });
  assert.equal(titleText(state), "편집됨");

  state = workbenchReducer(state, { type: "history/undo", operationId: "op:undo", createdAt: "2026-08-13T00:00:01Z" });
  assert.equal(titleText(state), "원본 제목");
  assert.equal(state.outbox.at(-1)?.source, "undo");
  assert.equal(state.outbox.at(-1)?.operation.kind, "text.replace");

  state = workbenchReducer(state, { type: "history/redo", operationId: "op:redo", createdAt: "2026-08-13T00:00:02Z" });
  assert.equal(titleText(state), "편집됨");
  assert.equal(state.outbox.at(-1)?.source, "redo");
});

test("ack rebases queued patches on the server snapshot and a CAS conflict requires explicit rebase", () => {
  const edit = operation("op:first", { kind: "text.replace", elementId: "element:title", text: "로컬 편집" });
  let state = workbenchReducer(loaded(), { type: "operation/enqueue", operation: edit, createdAt: "2026-08-13T00:00:00Z" });
  state = workbenchReducer(state, { type: "operation/conflicted", operationId: "op:first", code: "deck_digest_conflict", actual: { deckRevision: 2, deckDigest: digestB } });
  assert.equal(state.conflict?.rebase, "required");
  assert.equal(selectNextWorkbenchEffect(state), null);
  assert.throws(
    () => workbenchReducer(state, { type: "conflict/rebaseSucceeded", serverDeck: deck({ revision: 2, digest: digestB }) }),
    WorkbenchInvariantError,
  );

  state = workbenchReducer(state, { type: "conflict/rebaseStarted" });
  state = workbenchReducer(state, { type: "conflict/rebaseSucceeded", serverDeck: deck({ revision: 2, digest: digestB }) });
  assert.equal(state.conflict, null);
  assert.equal(state.outbox[0].status, "queued");
  assert.equal(state.outbox[0].operation.base.deckRevision, 2);
  assert.equal(titleText(state), "로컬 편집");
});

test("a definitive mutation failure restores the canonical snapshot and is never replayed", () => {
  const edit = operation("op:rejected", { kind: "text.replace", elementId: "element:title", text: "로컬 미저장" });
  let state = workbenchReducer(loaded(), {
    type: "operation/enqueue",
    operation: edit,
    createdAt: "2026-08-13T00:00:00Z",
  });
  state = workbenchReducer(state, { type: "operation/sendStarted", operationId: edit.operationId });
  assert.equal(selectNextWorkbenchEffect(state), null);
  state = workbenchReducer(state, {
    type: "operation/failed",
    operationId: edit.operationId,
    code: "authored_vnext_scene_asset_digest_mismatch",
    serverDeck: deck({ revision: 2, digest: digestB }),
  });
  assert.equal(titleText(state), "원본 제목");
  assert.equal(state.outbox[0]?.status, "failed");
  assert.equal(state.outbox[0]?.errorCode, "authored_vnext_scene_asset_digest_mismatch");
  assert.equal(selectNextWorkbenchEffect(state), null);
});

test("explicit rebase fails closed when the locked master changed", () => {
  let state = workbenchReducer(loaded(), {
    type: "operation/enqueue",
    operation: operation("op:pinned", { kind: "text.replace", elementId: "element:title", text: "로컬" }),
    createdAt: "2026-08-13T00:00:00Z",
  });
  state = workbenchReducer(state, { type: "operation/conflicted", operationId: "op:pinned", code: "master_changed" });
  state = workbenchReducer(state, { type: "conflict/rebaseStarted" });
  const changedMaster = { ...deck({ revision: 2, digest: digestB }), master: { ...deck().master, digest: digestC } };
  state = workbenchReducer(state, { type: "conflict/rebaseSucceeded", serverDeck: changedMaster });
  assert.equal(state.conflict?.rebase, "failed");
  assert.match(state.conflict?.message ?? "", /locked master changed/i);
  assert.equal(state.outbox[0].status, "conflict");
});

test("body-only regeneration exposes preview and accept/reject without changing master or layout", () => {
  const request = {
    requestId: "regen:1",
    scope: "bodyOnly" as const,
    slideId,
    layoutId: "layout:standard",
    body: "새 본문",
    pin: pin(),
  };
  const candidateSlide = {
    ...deck().slides[0],
    revision: 3,
    nodes: deck().slides[0].nodes.map((node) => node.kind === "text" ? { ...node, text: "재생성 후보" } : node),
  };
  const serverDeck = deck({ revision: 2, digest: digestC, slides: [candidateSlide] });

  let accepting = workbenchReducer(loaded(), { type: "regeneration/requested", request });
  assert.deepEqual(selectNextWorkbenchEffect(accepting), { effect: "regenerateBody", request });
  accepting = workbenchReducer(accepting, { type: "regeneration/succeeded", requestId: request.requestId, candidateDigest: digestB, serverDeck });
  assert.equal(accepting.regeneration.status, "preview");
  accepting = workbenchReducer(accepting, { type: "regeneration/accept" });
  assert.equal(lastKnownGood(accepting.deck)?.revision, 2);
  assert.equal(lastKnownGood(accepting.deck)?.master.digest, digestD);

  let rejecting = workbenchReducer(loaded(), { type: "regeneration/requested", request });
  rejecting = workbenchReducer(rejecting, { type: "regeneration/succeeded", requestId: request.requestId, candidateDigest: digestB, serverDeck });
  rejecting = workbenchReducer(rejecting, { type: "regeneration/reject" });
  assert.equal(rejecting.deck.status, "stale");
  assert.equal(lastKnownGood(rejecting.deck)?.revision, 1);
});

test("loading and error states retain an explicit last-known-good snapshot", () => {
  let state = loaded();
  state = workbenchReducer(state, { type: "deck/loadStarted" });
  assert.equal(state.deck.status, "loading");
  assert.equal(lastKnownGood(state.deck)?.id, "deck:test");
  state = workbenchReducer(state, {
    type: "deck/loadFailed",
    error: { code: "offline", message: "네트워크 연결 없음", retryable: true },
  });
  assert.equal(state.deck.status, "error");
  assert.equal(lastKnownGood(state.deck)?.digest, digestA);
  state = workbenchReducer(state, { type: "deck/loadEmpty" });
  assert.equal(state.deck.status, "empty");
});
