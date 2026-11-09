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
// MasterStudioCanvas delegates pointer/toolbar interaction to these two
// (introduced alongside it in 8c88ac2c65, replacing the raw-pointer-capture
// design the tests below were originally written against).
const konvaCanvas = readFileSync(
  path.join(routeDir, "MasterKonvaCanvas.tsx"),
  "utf8",
);
const wysiwygWorkspace = readFileSync(
  path.join(routeDir, "MasterWysiwygWorkspace.tsx"),
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
const uploadGenerationHandlers = readFileSync(
  path.join(generatorDir, "upload", "components", "upload-page", "uploadGenerationHandlers.ts"),
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
const source = `${page}\n${gate}\n${publishPanel}\n${styleControls}`;

test("catalog selection commits only on Apply and draft cards remain edit-only", () => {
  assert.match(catalogDialog, /setPendingMasterId\(entry\.masterId\)/);
  assert.match(catalogDialog, /client\.getPublished\(pendingMasterId\)/);
  assert.match(catalogDialog, /await onApply\(published\)/);
  // The reset-to-selected effect always folds in the onClear fallback (added
  // in the same commit, 8c88ac2c65); the plain-selectedMasterId regex here
  // never matched that shape.
  assert.match(
    catalogDialog,
    /setPendingMasterId\(selectedMasterId \?\? \(onClear \? NO_COMPANY_STANDARD : null\)\)/,
  );
  assert.match(catalogCard, /entry\.publishedRevision !== null/);
  assert.match(catalogCard, /disabled=\{!selectable\}/);
  assert.match(gate, /client\.registerMaster/);
  assert.match(gate, /onPublishComplete\?\.\(published\)/);
  assert.doesNotMatch(`${catalogDialog}\n${catalogCard}`, /publishedDigest/);
});

test("Upload keeps master preference separate and defaults to one-shot generation", () => {
  // 1fc2b169a ("fix(presentation): complete authored source-content reflow")
  // deliberately dropped last-used-preference auto-resolution: a fresh
  // upload must never inherit browser state from another document, so the
  // mount effect now resets and clears any stored pin instead of loading it.
  assert.match(uploadPage, /dispatch\(setPptGenUploadState\(\{ companyStandard: null \}\)\)/);
  assert.match(uploadPage, /clearLastUsedMasterId\(\)/);
  assert.doesNotMatch(uploadPage, /readStoredCompanyStandardPreference/);
  assert.match(uploadPage, /onApply=\{applyMaster\}/);
  assert.match(uploadPage, /masterBlocksGeneration/);
  assert.match(uploadPage, /data-testid="master-admission-error"/);
  assert.match(uploadPage, /handleReviewOutline/);
  assert.match(uploadState, /masterClient\.getPublished\(requested\.masterId\)/);
  assert.match(uploadState, /companyStandardRef\.current\?\.masterId !== requested\.masterId/);
  assert.match(uploadState, /startPresentation\(true\)/);
  assert.match(uploadState, /startPresentation\(false\)/);
  assert.match(uploadGenerationHandlers, /\/outline\?auto=1&id=/);
  assert.match(outlinePage, /autoIntentState === "checking"/);
  assert.match(outlinePage, /masterDefaultResolved/);
  assert.match(presentationGeneration, /generationMasterPin\(companyStandard\)/);
  assert.match(presentationGeneration, /authoredRequest\.masterPin/);
});

test("Master Setup route supports an explicit emergency-disable feature flag", () => {
  assert.match(page, /isAuthoredVnextMasterEnabled\(\)/);
  assert.match(page, /notFound\(\)/);
});

test("published Master is a required gate with an apply-driven continuation", () => {
  // 8c88ac2c65 ("feat: complete authored presentation workflows") — the same
  // commit that added this test — replaced the query-param continuation
  // (page.tsx searchParams -> continueUrl -> safeContinuePath/buildContinueHref,
  // MasterSetupPublishPanel's onPublished(published, visualDirection) submit
  // handler) with a plain callback prop threaded straight through the gate:
  // MasterCatalogDialog now does `await onApply(published)` on the panel's
  // "apply-after-publish" button, and page.tsx never reads searchParams.
  assert.doesNotMatch(page, /searchParams/);
  assert.doesNotMatch(gate, /safeContinuePath|masterRevision|masterDigest/);
  assert.match(publishPanel, /published && !publishedStale/);
  assert.match(publishPanel, /data-testid="apply-after-publish"/);
  assert.match(gate, /onApply/);
  assert.match(publishPanel, /onApply\(published, visualDirection\)/);
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
  assert.match(source, /aria-live/);
  assert.match(source, /role="radiogroup"/);
  assert.match(source, /aria-live/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB/);
});

test("Master studio keeps direct canvas manipulation mapped to the existing contract", () => {
  // 8c88ac2c65 shipped MasterStudioCanvas on a react-konva canvas
  // (MasterKonvaCanvas) plus a toolbar shell (MasterWysiwygWorkspace) instead
  // of the raw-pointer-capture/"furniture:*" design this test originally
  // targeted; the raw-pointer/testid/z-index plumbing below has no current
  // counterpart because Konva's Stage/Transformer own it internally. This
  // rewrite checks the same intent — contract-in, transaction-out, no
  // rebuilt preview scene, resize/reorder/undo/logo controls present —
  // against the files that now implement it.
  assert.match(gate, /MasterStudioCanvas/);
  assert.match(gate, /client\.assetUrl\(logo\)/);
  assert.match(gate, /contract=\{contract\}/);
  assert.match(gate, /validateMasterSetup\(persistenceContract\)/);
  assert.match(studio, /masterContractToCanvasScene\(contract, activeLayoutId\)/);
  assert.match(studio, /applyMasterCanvasTransaction\(currentContract, activeLayoutId, canvasTransaction\)/);
  assert.match(studio, /onContractChange\(next\)/);
  assert.match(studio, /hasBoundCanonicalLogo=\{Boolean\(contract\.furniture\.logo\.assetId\)\}/);
  assert.match(studio, /type === "insertElement"/);
  assert.match(studio, /overlapWarning/);
  assert.match(konvaCanvas, /<Transformer /);
  assert.match(konvaCanvas, /onDragStart=\{beginDrag\}/);
  assert.match(konvaCanvas, /onTransformEnd=\{\(\) => commitGeometry/);
  assert.match(konvaCanvas, /aria-label=\{t\("canvasTextAria"\)\}/);
  assert.match(konvaCanvas, /t\("bringFront"\)/);
  assert.match(konvaCanvas, /t\("safeArea"\)/);
  assert.match(wysiwygWorkspace, /t\("undo"\)/);
  assert.doesNotMatch(`${studio}\n${konvaCanvas}`, /onPointerLeave=\{stop\}/);
});

test("Master operations keep the authored-vnext client contract", () => {
  // The gate never round-trips a render through client.preview() (that
  // client method is unused app-wide since 8c88ac2c65 replaced the
  // server-rendered MasterPreviewScene with the live MasterKonvaCanvas
  // WYSIWYG editor); getHead is the still-live typed-client read this gate
  // performs to resume an existing draft.
  for (const operation of [
    "client.createDraft",
    "client.validateCanonicalDraft",
    "client.publishValidatedDraft",
    "client.getHead",
  ]) {
    assert.match(gate, new RegExp(operation.replace(".", "\\.")));
  }
  assert.doesNotMatch(gate, /fetch\(/);
});
