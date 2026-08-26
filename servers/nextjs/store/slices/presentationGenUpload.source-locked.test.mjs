import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(testDirectory, "presentationGenUpload.ts");
const dataModule = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;

async function loadSlice() {
  const source = await readFile(sourcePath, "utf8");
  const configUrl = dataModule("export {};");
  const toolkit = `
    const createSlice = ({ name, initialState, reducers }) => {
      const actions = Object.fromEntries(Object.keys(reducers).map((key) => [
        key, (payload) => ({ type: name + '/' + key, payload }),
      ]));
      return {
        actions,
        reducer(state = structuredClone(initialState), action) {
          const key = action.type.startsWith(name + '/') ? action.type.slice(name.length + 1) : null;
          if (!key || !reducers[key]) return state;
          const draft = structuredClone(state);
          reducers[key](draft, action);
          return draft;
        },
      };
    };
  `;
  const executable = source
    .replace('import { PresentationConfig } from "@/app/(presentation-generator)/upload/type";', `import {} from "${configUrl}";`)
    .replace('import { createSlice, PayloadAction } from "@reduxjs/toolkit";', toolkit);
  const compiled = await transform(executable, {
    format: "esm", loader: "ts", target: "esnext",
  });
  return import(dataModule(compiled.code));
}

test("source-content mode remains independent from attachment order", async () => {
  const {
    default: reducer,
    setSourceContentMode,
    setPptGenUploadState,
  } = await loadSlice();
  let state = reducer(undefined, { type: "init" });
  state = reducer(state, setSourceContentMode(true));
  state = reducer(state, setPptGenUploadState({ files: [{ name: "source.pptx" }] }));
  state = reducer(state, setSourceContentMode(false));
  assert.equal(state.sourceContentMode, false);
});

test("partial upload-state updates preserve independently selected source-content mode", async () => {
  const {
    default: reducer,
    setPptGenUploadState,
    setSourceContentMode,
  } = await loadSlice();
  let state = reducer(undefined, { type: "init" });
  state = reducer(state, setSourceContentMode(true));
  state = reducer(state, setPptGenUploadState({ authoredStyle: "osb-smart-banking" }));
  assert.equal(state.sourceContentMode, true);
  assert.equal(state.authoredStyle, "osb-smart-banking");
});

test("source-content analyze-confirm flow preserves the admitted pin into the authored request", async () => {
  const nextjsRoot = path.resolve(testDirectory, "..", "..");
  const [uploadPage, uploadState, sourceContentClient, outlineGeneration] = await Promise.all([
    readFile(path.join(nextjsRoot, "app", "(presentation-generator)", "upload", "components", "UploadPage.tsx"), "utf8"),
    readFile(path.join(nextjsRoot, "app", "(presentation-generator)", "upload", "components", "upload-page", "useUploadPageState.ts"), "utf8"),
    readFile(path.join(nextjsRoot, "app", "(presentation-generator)", "services", "api", "source-content", "contract.ts"), "utf8"),
    readFile(path.join(nextjsRoot, "app", "(presentation-generator)", "outline", "hooks", "usePresentationGeneration.ts"), "utf8"),
  ]);
  assert.match(uploadPage, /data-testid="source-content-mode-checkbox"/);
  assert.match(uploadPage, /sourceContentMode/);
  assert.match(uploadPage, /<TemplateSelection\s*\/>/);
  assert.match(uploadPage, /data-testid="upload-master-selection"/);
  assert.match(uploadPage, /label=\{sourceContentMode \? "추가 디자인 지시" : undefined\}/);
  assert.match(uploadState, /const analyzeSourceContent/);
  assert.match(uploadState, /cleanupDocumentAdmission/);
  assert.match(uploadState, /sourceContentMode \? sourceContentAdmission : null/);
  assert.match(uploadState, /content: sourceContentMode \? ""/);
  assert.match(uploadState, /instructions: sourceContentMode/);
  assert.match(sourceContentClient, /formData\.append\("referenceSnapshotId", referenceSnapshotId\)/);
  assert.doesNotMatch(sourceContentClient, /formData\.append\("files"/);
  assert.match(sourceContentClient, /referenceSnapshotBinding/);
  assert.match(sourceContentClient, /objectInventory/);
  assert.match(outlineGeneration, /sourceContentAdmission/);
  assert.match(outlineGeneration, /sourceContentManifestPin/);
  assert.match(outlineGeneration, /slides_markdown: isSourceContentGeneration \? undefined/);
});
