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

test("source-locked mode latches on attachment and unlocks only after all files are removed", async () => {
  const {
    default: reducer,
    setSourceLockedGenerationMode,
    syncSourceLockedAttachmentLatch,
  } = await loadSlice();
  let state = reducer(undefined, { type: "init" });
  state = reducer(state, setSourceLockedGenerationMode("source_locked"));
  state = reducer(state, syncSourceLockedAttachmentLatch(1));
  state = reducer(state, setSourceLockedGenerationMode("adaptive"));
  assert.equal(state.sourceLockedGenerationMode, "source_locked");
  assert.equal(state.sourceLockedGenerationModeLatched, true);
  state = reducer(state, syncSourceLockedAttachmentLatch(0));
  state = reducer(state, setSourceLockedGenerationMode("adaptive"));
  assert.equal(state.sourceLockedGenerationMode, "adaptive");
  assert.equal(state.sourceLockedGenerationModeLatched, false);
});

test("partial upload-state updates cannot bypass a latched source-locked mode", async () => {
  const {
    default: reducer,
    setPptGenUploadState,
    setSourceLockedGenerationMode,
    syncSourceLockedAttachmentLatch,
  } = await loadSlice();
  let state = reducer(undefined, { type: "init" });
  state = reducer(state, setSourceLockedGenerationMode("source_locked"));
  state = reducer(state, syncSourceLockedAttachmentLatch(1));
  state = reducer(state, setPptGenUploadState({ sourceLockedGenerationMode: "adaptive" }));
  assert.equal(state.sourceLockedGenerationMode, "source_locked");
});

test("primary and advanced source-locked checkboxes bind to the same Redux action/state", async () => {
  const nextjsRoot = path.resolve(testDirectory, "..", "..");
  const [uploadPage, advancedSettings] = await Promise.all([
    readFile(path.join(nextjsRoot, "app", "(presentation-generator)", "upload", "components", "UploadPage.tsx"), "utf8"),
    readFile(path.join(nextjsRoot, "app", "(presentation-generator)", "upload", "components", "AdvanceSettings.tsx"), "utf8"),
  ]);
  for (const [source, testId] of [
    [uploadPage, "source-locked-generation-checkbox"],
    [advancedSettings, "source-locked-generation-checkbox-advanced"],
  ]) {
    assert.match(source, new RegExp(`data-testid=\\"${testId}\\"`));
    assert.match(source, /state\.pptGenUpload\.sourceLockedGenerationMode/);
    assert.match(source, /setSourceLockedGenerationMode/);
  }
});
