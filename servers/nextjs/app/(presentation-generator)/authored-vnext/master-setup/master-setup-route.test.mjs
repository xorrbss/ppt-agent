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
const studio = readFileSync(
  path.join(routeDir, "MasterStudioCanvas.tsx"),
  "utf8",
);
const catalogDialog = readFileSync(
  path.join(routeDir, "MasterCatalogDialog.tsx"),
  "utf8",
);
const catalogCard = readFileSync(
  path.join(routeDir, "MasterCatalogCard.tsx"),
  "utf8",
);
const generatorDir = path.join(process.cwd(), "app", "(presentation-generator)");
const uploadPage = readFileSync(
  path.join(generatorDir, "upload", "components", "UploadPage.tsx"),
  "utf8",
);
const uploadState = readFileSync(
  path.join(
    generatorDir,
    "upload",
    "components",
    "upload-page",
    "useUploadPageState.ts",
  ),
  "utf8",
);
const outlinePage = readFileSync(
  path.join(generatorDir, "outline", "components", "OutlinePage.tsx"),
  "utf8",
);
const presentationGeneration = readFileSync(
  path.join(
    generatorDir,
    "outline",
    "hooks",
    "usePresentationGeneration.ts",
  ),
  "utf8",
);
const source = `${page}\n${gate}\n${scene}\n${publishPanel}\n${styleControls}`;

test("catalog selection commits only on Apply and draft cards remain edit-only", () => {
  assert.match(catalogDialog, /setPendingMasterId\(entry\.masterId\)/);
  assert.match(catalogDialog, /client\.getPublished\(pendingMasterId\)/);
  assert.match(catalogDialog, /await onApply\(published\)/);
  assert.match(catalogDialog, /setPendingMasterId\(selectedMasterId\)/);
  assert.match(catalogCard, /entry\.publishedRevision !== null/);
  assert.match(catalogCard, /disabled=\{!selectable\}/);
  assert.match(gate, /client\.registerMaster/);
  assert.match(gate, /onPublishComplete\?\.\(published\)/);
  assert.doesNotMatch(`${catalogDialog}\n${catalogCard}`, /publishedDigest/);
});

test("Upload keeps master preference separate and defaults to one-shot generation", () => {
  assert.match(uploadPage, /readStoredCompanyStandardPreference\(\)/);
  assert.match(uploadPage, /masterClient[\s\S]*?\.getPublished\(lastUsedMasterId\)/);
  assert.match(uploadPage, /saveLastUsedMasterId\(published\.masterId\)/);
  assert.match(uploadPage, /onApply=\{applyMaster\}/);
  assert.match(uploadPage, /masterBlocksGeneration/);
  assert.match(uploadPage, /data-testid="master-admission-error"/);
  assert.match(uploadPage, /handleReviewOutline/);
  assert.match(uploadState, /masterClient\.getPublished\(requested\.masterId\)/);
  assert.match(uploadState, /companyStandardRef\.current\?\.masterId !== requested\.masterId/);
  assert.match(uploadState, /startPresentation\(true\)/);
  assert.match(uploadState, /startPresentation\(false\)/);
  assert.match(uploadState, /\/outline\?auto=1&id=/);
  assert.match(outlinePage, /const autoWorking = auto/);
  assert.match(outlinePage, /masterDefaultResolved/);
  assert.match(presentationGeneration, /generationMasterPin\(companyStandard\)/);
  assert.match(presentationGeneration, /authoredRequest\.masterPin/);
});

test("Master Setup route supports an explicit emergency-disable feature flag", () => {
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

test("legacy Master library rail stays hidden so WYSIWYG uses the full width", () => {
  assert.doesNotMatch(gate, /xl:grid-cols-\[280px_minmax\(0,1fr\)\]/);
  assert.match(gate, /<div className="grid gap-4">/);
  assert.match(gate, /<aside\s+className="hidden space-y-4/);
  assert.match(gate, /<MasterStudioCanvas/);
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

test("Master studio keeps direct canvas manipulation mapped to the existing contract", () => {
  assert.match(gate, /MasterStudioCanvas/);
  assert.match(gate, /client\.assetUrl\(logo\)/);
  assert.match(gate, /buildEditingPreviewScene\(contract, selectedSlide\)/);
  assert.match(studio, /setBounds\(contract, layoutKind/);
  assert.match(studio, /beginDrag\(event, item, "resize"\)/);
  assert.match(studio, /t\("addContent"\)/);
  assert.match(studio, /activateFurniture\("furniture:header"\)/);
  assert.match(studio, /activateFurniture\("furniture:logo", true\)/);
  assert.match(studio, /authored-master-logo/);
  assert.match(studio, /disabled type="button"/);
  assert.match(studio, /activateFurniture\("furniture:divider"\)/);
  assert.match(studio, /activateFurniture\("furniture:page-number"\)/);
  assert.match(studio, /const setFurnitureText/);
  assert.match(studio, /aria-label=\{t\("textAria"\)\}/);
  assert.match(studio, /furniture\?\.text \?\? item\.role/);
  assert.match(studio, /validateMasterSetup\(contract\)/);
  assert.match(studio, /const conflictIds/);
  assert.match(studio, /ring-red-500/);
  assert.match(studio, /overlapWarning/);
  assert.match(studio, /setPointerCapture\(event\.pointerId\)/);
  assert.match(studio, /releasePointerCapture\(drag\.pointerId\)/);
  assert.match(studio, /drag\.moved \|\| dragMoved\.current/);
  assert.match(studio, /data-studio-role/);
  assert.match(studio, /selectedNow \? 2_000 : 1_000/);
  assert.match(studio, /selectedNow \? 500 : 100/);
  assert.doesNotMatch(studio, /onPointerLeave=\{stop\}/);
  assert.match(studio, /t\("bringFront"\)/);
  assert.match(studio, /t\("safeArea"\)/);
  assert.match(studio, /t\("undo"\)/);
  assert.doesNotMatch(scene, /LOGO/);
  assert.match(scene, /<img/);
});

test("Master operations keep the authored-vnext client contract", () => {
  for (const operation of [
    "client.createDraft",
    "client.validateCanonicalDraft",
    "client.publishValidatedDraft",
    "client.preview",
  ]) {
    assert.match(gate, new RegExp(operation.replace(".", "\\.")));
  }
  assert.doesNotMatch(gate, /fetch\(/);
});
