import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const routeDir = path.join(
  process.cwd(),
  "app",
  "(presentation-generator)",
  "authored-vnext",
  "master-setup",
);

const page = readFileSync(path.join(routeDir, "page.tsx"), "utf8");
const gate = readFileSync(path.join(routeDir, "MasterSetupGate.tsx"), "utf8");
const scene = readFileSync(
  path.join(routeDir, "MasterPreviewScene.tsx"),
  "utf8",
);
const publishPanel = readFileSync(
  path.join(routeDir, "MasterSetupPublishPanel.tsx"),
  "utf8",
);
const styleControls = readFileSync(
  path.join(routeDir, "MasterStyleControls.tsx"),
  "utf8",
);
const source = `${page}\n${gate}\n${scene}\n${publishPanel}\n${styleControls}`;

test("Master Setup route remains hidden behind the default-off feature flag", () => {
  assert.match(page, /isAuthoredVnextMasterEnabled\(\)/);
  assert.match(page, /notFound\(\)/);
});

test("published Master is a required gate with a query-driven continuation", () => {
  assert.match(page, /params\.continue/);
  assert.match(page, /params\.returnTo/);
  assert.match(gate, /safeContinuePath/);
  assert.match(publishPanel, /published && !publishedStale/);
  assert.match(publishPanel, /data-testid="continue-after-publish"/);
  assert.match(gate, /masterRevision/);
  assert.match(gate, /masterDigest/);
  assert.match(source, /remotelyValidated/);
  assert.match(publishPanel, /onPublished\(published, visualDirection\)/);
  assert.match(publishPanel, /event\.preventDefault\(\)/);
});

test("Master and Visual Direction are visibly and structurally separate", () => {
  assert.match(gate, /library\.title/);
  assert.match(gate, /library\.draft/);
  assert.match(gate, /library\.published/);
  assert.match(gate, /direction\.title/);
  assert.match(gate, /direction\.description/);
  assert.match(gate, /buildPreviewScene/);
  assert.match(gate, /REQUIRED_LAYOUT_KINDS/);
  assert.match(gate, /REQUIRED_LAYOUT_KINDS\.map/);
  assert.match(source, /brand\.title/);
  assert.match(gate, /stress\.title/);
});

test("Master Designer exposes non-logo furniture, typography, semantic styles, and colors", () => {
  assert.match(gate, /MasterStyleControls/);
  for (const control of [
    "setMasterPrimaryFont",
    "setMasterFallbackFonts",
    "setMasterFurnitureText",
    "setMasterFurnitureColor",
    "setMasterSemanticFontPt",
    "setMasterTitleMinimumFontPt",
    "setMasterColor",
  ]) {
    assert.match(styleControls, new RegExp(control));
  }
  assert.doesNotMatch(styleControls, /logoPlaceholder|furniture\.logo/);
});

test("loading, error, empty, stale and LKG states are explicit", () => {
  for (const marker of [
    "loading",
    'role="alert"',
    "library.emptyTitle",
    "draftStale",
    "library.lkg",
  ]) {
    assert.match(source, new RegExp(marker));
  }
});

test("preview uses explicit accessible React layers and never injects or persists backend HTML", () => {
  assert.match(source, /MasterPreviewScene/);
  assert.match(scene, /data-layer/);
  assert.match(source, /aria-live/);
  assert.match(source, /role="radiogroup"/);
  assert.match(source, /aria-live/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB/);
});

test("Master operations keep the authored-vnext client contract", () => {
  for (const operation of [
    "client.createDraft",
    "client.validateDraft",
    "client.publishDraft",
    "client.preview",
  ]) {
    assert.match(gate, new RegExp(operation.replace(".", "\\.")));
  }
  assert.doesNotMatch(gate, /fetch\(/);
});
