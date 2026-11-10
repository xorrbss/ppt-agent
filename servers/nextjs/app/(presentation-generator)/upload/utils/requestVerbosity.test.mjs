import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Isolated worktrees can run these small regressions without installing frontend dependencies.
const transform = await import("esbuild").then(
  (module) => module.transform,
  async (error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    const { stripTypeScriptTypes } = await import("node:module");
    return (source) => ({ code: stripTypeScriptTypes(source, { mode: "transform" }) });
  },
);

const testDirectory = path.dirname(fileURLToPath(import.meta.url));

async function moduleUrl(source) {
  const { code } = await transform(source, { loader: "ts", format: "esm" });
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}

async function loadModule() {
  const source = await readFile(path.join(testDirectory, "requestVerbosity.ts"), "utf8");
  return import(await moduleUrl(source));
}

async function loadRestoration() {
  const types = await moduleUrl(await readFile(path.join(testDirectory, "../type.ts"), "utf8"));
  const source = await readFile(path.join(testDirectory, "../../services/api/svg-template-restore.ts"), "utf8");
  // The canvas re-export is unrelated to restoring saved generation settings.
  const isolated = source.replace('from "../../upload/type"', `from "${types}"`)
    .replace(/^export \{ savedSvgTemplateCanvas, templateSourceCanvasSelector \}.*$/m, "");
  return import(await moduleUrl(isolated));
}

const redesign = { svgTemplatePin: { id: "t" }, svgTemplateMode: "template-redesign" };

test("template redesign leaves verbosity unset until the user picks one", async () => {
  const { requestVerbosity } = await loadModule();
  assert.equal(requestVerbosity({ verbosity: "standard", ...redesign }), undefined);
  assert.equal(
    requestVerbosity({ verbosity: "concise", verbosityTouched: true, ...redesign }),
    "concise",
  );
});

test("other decks keep the configured verbosity", async () => {
  const { requestVerbosity } = await loadModule();
  assert.equal(requestVerbosity({ verbosity: "standard" }), "standard");
  assert.equal(
    requestVerbosity({ verbosity: "standard", svgTemplatePin: { id: "t" }, svgTemplateMode: "free-design" }),
    "standard",
  );
  assert.equal(requestVerbosity(undefined), undefined);
});

test("restored template redesign preserves every saved verbosity", async () => {
  const { requestVerbosity } = await loadModule();
  const { templateRestoreConfig } = await loadRestoration();
  for (const verbosity of ["concise", "standard", "text-heavy"]) {
    const config = templateRestoreConfig(redesign, { verbosity });
    assert.equal(requestVerbosity(config), verbosity);
  }
  for (const verbosity of [undefined, null, "invalid"]) {
    assert.equal(requestVerbosity(templateRestoreConfig(redesign, { verbosity })), undefined);
  }
});

test("saving a picked standard verbosity records it even when it matches the stored default", async () => {
  const { requestVerbosity } = await loadModule();
  const source = await readFile(path.join(testDirectory, "../components/AdvanceSettings.tsx"), "utf8");
  const start = source.indexOf("    const handleSaveAdvanced = () => {");
  const end = source.indexOf("\n    useEffect(", start);
  assert.ok(start >= 0 && end > start);
  for (const touched of [false, true]) {
    const config = { ...redesign, verbosity: "standard" };
    const save = new Function("advancedDraft", "config", "onConfigChange", "setOpenAdvanced",
      `${source.slice(start, end)}; return handleSaveAdvanced;`)(
      { verbosity: "standard", verbosityTouched: touched }, config,
      (key, value) => { config[key] = value; }, () => {},
    );
    save();
    assert.equal(requestVerbosity(config), touched ? "standard" : undefined);
  }
  assert.match(source, /verbosity: requestVerbosity\(config\) === undefined \? VerbosityType\.Text_Heavy : config\.verbosity/);
});

test("draft polling accepts only the current template operation and ignores superseded responses", async () => {
  const source = (await readFile(path.join(testDirectory, "../components/SvgTemplatePicker.tsx"), "utf8")).replace(/\r\n/g, "\n");
  const start = source.indexOf("  useEffect(() => {", source.indexOf("  const analyzingTemplateId ="));
  const end = source.indexOf("\n  const prepare =", start);
  assert.ok(start >= 0 && end > start);
  const effect = source.slice(start, end);
  const draft = { template: { templateId: "a", registrationState: "analyzing" }, operationId: "op-a" };
  const completed = { ...draft, template: { ...draft.template, registrationState: "review-required" } };
  for (const scenario of ["current", "closed", "superseded", "other-template", "other-operation", "session-changed"]) {
    const writes = [];
    const epoch = { current: 0 };
    let cleanup, fireTimer, resolveRequest;
    let ownerLive = true;
    const pending = new Promise(resolve => { resolveRequest = resolve; });
    new Function("useEffect", "analyzingTemplateId", "draft", "ownerEpoch", "draftEpoch", "live", "window", "SvgTemplatesApi",
      "setDraft", "setReviewDirty", "setConfirmed", "setRefresh", "setSelectedFrames", "frameReviewBlockReason", effect)(
      callback => { cleanup = callback(); }, "a", draft, { current: 0 }, epoch, () => ownerLive,
      { setTimeout: callback => { fireTimer = callback; return 1; }, clearTimeout: () => {} },
      { draft: () => pending },
      ...Array.from({ length: 5 }, () => value => writes.push(value)), () => null,
    );
    fireTimer();
    if (scenario === "closed") cleanup();
    if (scenario === "superseded") epoch.current += 1;
    if (scenario === "session-changed") ownerLive = false;
    resolveRequest(scenario === "other-template" ? { ...completed, template: { ...completed.template, templateId: "b" } }
      : scenario === "other-operation" ? { ...completed, operationId: "op-b" } : completed);
    await pending;
    await Promise.resolve();
    assert.equal(writes.length, scenario === "current" ? 5 : 0, scenario);
    cleanup();
  }
});


test("publish responses update only their current draft and leave superseding work untouched", async () => {
  const source = (await readFile(path.join(testDirectory, "../components/SvgTemplatePicker.tsx"), "utf8")).replace(/\r\n/g, "\n");
  const start = source.indexOf("  const publish = async ");
  const end = source.indexOf("\n\n  if (ownerInvalidated)", start);
  assert.ok(start >= 0 && end > start);
  const close = source.match(/onClick=\{\(\) => \{ (draftEpoch\.current \+= 1;.*?) \}\}>\{t\("close"\)\}/);
  assert.ok(close);
  for (const synchronousRef of [false, true]) {
  for (const styleOnly of [false, true]) {
    for (const failed of [false, true]) {
      for (const scenario of ["current", "closed", "reopened", "other-template", "other-operation", "session-changed"]) {
        const draft = { template: { templateId: "a", pin: { revision: 2 } }, operationId: "op-a", package: { frames: [{ frame_id: "frame-a" }] } };
        const currentDraft = { current: draft };
        const epoch = { current: 0 };
        const writes = [];
        const requests = [];
        let ownerLive = true;
        let resolveRequest, rejectRequest;
        let serverPublished = false;
        const pending = new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
        // Cover both a deferred render and a synchronous external-store flush.
        const setDraft = value => {
          if (synchronousRef) currentDraft.current = value;
          writes.push(["draft", value]);
        };
        const setBusy = value => writes.push(["busy", value]);
        const publish = new Function("draft", "busy", "confirmed", "reviewDirty", "ownerAlive", "available", "selectedFrames", "frameReviewBlockReason", "ownerEpoch", "draftEpoch", "currentDraft", "live", "SvgTemplatesApi", "setBusy", "setDraft", "setRefresh", "choose", "notify", "t",
          `${source.slice(start, end)}; return publish;`)(
          draft, false, true, false, { current: true }, true, ["frame-a"], () => null,
          { current: 0 }, epoch, currentDraft, () => ownerLive,
          { publish: (id, payload) => { requests.push([id, payload]); return pending; } },
          setBusy, setDraft, value => writes.push(["refresh", value]), value => writes.push(["choose", value]),
          { error: value => writes.push(["error", value]) }, value => value,
        );
        const completion = publish(styleOnly);
        assert.equal(requests.length, 1);
        assert.equal(requests[0][0], "a");
        assert.equal(requests[0][1].operationId, "op-a");
        assert.equal(Boolean(requests[0][1].styleOnly), styleOnly);
        if (scenario === "closed" || scenario === "reopened") {
          new Function("draftEpoch", "setDraft", "setBusy", close[1])(epoch, setDraft, setBusy);
          currentDraft.current = null;
          assert.equal(writes.at(-1)[1], false, "closing releases busy state");
          if (scenario === "reopened") { epoch.current += 1; currentDraft.current = draft; setBusy(true); }
        }
        if (scenario === "other-template") currentDraft.current = { ...draft, template: { templateId: "b" } };
        if (scenario === "other-operation") currentDraft.current = { ...draft, operationId: "op-b" };
        if (scenario === "session-changed") ownerLive = false;
        const beforeResponse = writes.length;
        if (failed) rejectRequest(new Error("publish failed"));
        else { serverPublished = true; resolveRequest({ template: { templateId: "a", registrationState: "ready" } }); }
        await completion;
        assert.equal(serverPublished, !failed, "stale UI handling does not cancel server publication");
        const responseWrites = writes.slice(beforeResponse);
        assert.deepEqual(responseWrites.map(([kind]) => kind), scenario === "current"
          ? failed ? ["busy", "error"] : ["busy", "draft", "refresh", "choose"] : [], `${scenario}, styleOnly=${styleOnly}, failed=${failed}, synchronousRef=${synchronousRef}`);
        if (scenario === "current") assert.deepEqual(writes.filter(([kind]) => kind === "busy").at(-1), ["busy", false]);
      }
    }
  }
  }
});

test("a retried request restores its stored verbosity as an explicit choice", async () => {
  const { requestVerbosity, restoredVerbosity } = await loadModule();
  const allowed = ["concise", "standard", "text-heavy"];
  const current = { verbosity: "standard", verbosityTouched: false };
  const restored = { ...redesign, ...restoredVerbosity("concise", current, allowed) };
  assert.deepEqual(restoredVerbosity("concise", current, allowed), { verbosity: "concise", verbosityTouched: true });
  assert.equal(requestVerbosity(restored), "concise");
  // A missing or unknown stored value keeps the current fields untouched.
  assert.deepEqual(restoredVerbosity(undefined, current, allowed), current);
  assert.deepEqual(restoredVerbosity("verbose", current, allowed), current);
  assert.equal(requestVerbosity({ ...redesign, ...restoredVerbosity(null, current, allowed) }), undefined);
});
