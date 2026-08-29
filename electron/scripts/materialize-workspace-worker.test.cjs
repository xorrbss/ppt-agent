const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const {
  materializeWorkspaceWorker,
} = require("./materialize-workspace-worker.cjs");

test("materializes a dedicated worker executable from the shared frozen runtime", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-worker-binary-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const api = path.join(root, "fastapi.exe");
  fs.writeFileSync(api, Buffer.from("frozen-runtime"));

  const worker = materializeWorkspaceWorker(root, "win32");

  assert.equal(path.basename(worker), "workspace-operation-worker.exe");
  assert.deepEqual(fs.readFileSync(worker), fs.readFileSync(api));
});

test("fails closed when the frozen API runtime is absent", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-worker-binary-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.throws(
    () => materializeWorkspaceWorker(root, "linux"),
    /FastAPI executable is missing/,
  );
});
