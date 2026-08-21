import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const root = path.join(process.cwd(), "app", "(presentation-generator)", "authored-vnext");

test("independent generation UI is frozen behind an internal compatibility gate", () => {
  const source = readFileSync(path.join(root, "page.tsx"), "utf8");
  assert.match(source, /AUTHORED_VNEXT_INTERNAL_COMPATIBILITY_UI/);
  assert.match(source, /internalCompatibilityUi/);
  assert.match(source, /isAuthoredVnextMasterEnabled/);
  assert.match(source, /notFound\(\)/);
});

test("flow wires Master publish, ten-slide creation, typed outbox, conflict and export request", () => {
  const source = readFileSync(path.join(root, "AuthoredVnextFlowClient.tsx"), "utf8");
  const outlineSource = readFileSync(path.join(root, "flow-client-outline.ts"), "utf8");
  const actionsSource = readFileSync(path.join(root, "flow-client-actions.ts"), "utf8");
  const callbacksSource = readFileSync(path.join(root, "flow-client-callbacks.ts"), "utf8");
  assert.match(source, /onPublished=/);
  assert.match(source, /confirmedDirectionEvents/);
  assert.match(outlineSource, /AUTHORED_VNEXT_SLIDE_COUNT/);
  assert.match(source, /generationClient\.generate/);
  assert.match(actionsSource, /generationClient\.publishRegeneration/);
  assert.match(source, /operation\/enqueue/);
  assert.match(callbacksSource, /conflict\/rebaseSucceeded/);
  assert.match(source, /export\.requested/);
  assert.match(source, /Re-read the publication head at click time/);
  assert.match(actionsSource, /deck\.scene\?\.assetManifest/);
  assert.match(actionsSource, /expectedAssetDigests:\s*\[\.\.\.new Set/);
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|\.innerHTML|slides\s*:\s*any/);
});
