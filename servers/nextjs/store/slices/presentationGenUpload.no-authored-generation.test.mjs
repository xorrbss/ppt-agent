// New generation is svg-only: the upload flow offers no strategy choice and no
// request builder may name the removed authored strategy or its vision-QA pass.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..", "..");
const read = (...segments) => readFileSync(path.join(root, ...segments), "utf8");
const gen = ["app", "(presentation-generator)"];

const upload = [...gen, "upload", "components"];
const sources = {
  store: read("store", "slices", "presentationGenUpload.ts"),
  uploadPage: read(...upload, "UploadPage.tsx"),
  uploadState: read(...upload, "upload-page", "useUploadPageState.ts"),
  handlers: read(...upload, "upload-page", "uploadGenerationHandlers.ts"),
  canvasSelect: read(...upload, "CanvasSelect.tsx"),
  configurationSelects: read(...upload, "ConfigurationSelects.tsx"),
  outlineGeneration: read(...gen, "outline", "hooks", "usePresentationGeneration.ts"),
  durableTask: read(...gen, "outline", "hooks", "durableGenerationTask.ts"),
  api: read(...gen, "services", "api", "presentation-generation.ts"),
  capabilities: read(...gen, "services", "api", "document-canvas-capabilities.ts"),
  publicV2: read(...gen, "public-v2", "PublicV2OperationsClient.tsx"),
  stylePicker: read(...gen, "outline", "components", "AuthoredStylePicker.tsx"),
};

test("the upload store carries no generation strategy or vision-QA option", () => {
  assert.doesNotMatch(sources.store, /generationStrategy|PresentationGenerationStrategy|authoredVisionQa/);
  // The style id stays: it is the svg generation style.
  assert.match(sources.store, /authoredStyle: string/);
});

test("the upload page has no generation-strategy selector", () => {
  assert.doesNotMatch(sources.uploadPage, /GenerationStrategySelector|generation-strategy/);
  assert.doesNotMatch(sources.configurationSelects, /generationStrategy/);
  assert.doesNotMatch(sources.canvasSelect, /generationStrategy: "authored"|!svgMode/);
});

test("request builders never send the authored strategy", () => {
  for (const name of ["handlers", "outlineGeneration", "publicV2"]) {
    assert.doesNotMatch(sources[name], /strategy:\s*"authored"|"authored" as const/, name);
  }
  assert.match(sources.handlers, /strategy: "svg" as const/);
  assert.match(sources.outlineGeneration, /strategy: "svg" as const/);
  assert.match(sources.publicV2, /strategy: "svg" as const/);
});

test("typed strategy surfaces only admit svg", () => {
  assert.match(sources.api, /strategy\?: "svg";/);
  assert.doesNotMatch(sources.api, /strategy\?: "authored"/);
  assert.match(sources.durableTask, /strategy\?: "svg";/);
  assert.match(sources.capabilities, /generationStrategy: "svg";/);
});

test("the vision-QA toggle is gone from generation", () => {
  assert.doesNotMatch(sources.stylePicker, /authored-vision-qa|authoredVisionQa/);
  assert.doesNotMatch(sources.outlineGeneration, /visionQa|vision_qa/);
  assert.doesNotMatch(sources.handlers, /visionQa/);
  assert.doesNotMatch(sources.api.slice(sources.api.indexOf("generateAuthoredAsync")), /vision_qa: resolvedStrategy/);
});

test("company-standard pins still reach the svg request", () => {
  assert.match(sources.outlineGeneration, /masterPin: generationMasterPin\(companyStandard\)/);
  assert.match(sources.api, /masterPin\?: \{/);
  assert.match(sources.uploadPage, /upload-master-selection/);
});
