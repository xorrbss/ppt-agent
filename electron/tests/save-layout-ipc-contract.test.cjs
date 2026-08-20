const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "..", "..");
const apiHandlers = fs.readFileSync(
  path.join(repoRoot, "electron", "app", "ipc", "api_handlers.ts"),
  "utf8",
);

test("the orphaned save-layout writer is not registered in Electron", () => {
  assert.doesNotMatch(apiHandlers, /ipcMain\.handle\(\s*["']api:save-layout/);
  assert.equal(
    fs.existsSync(
      path.join(repoRoot, "servers", "nextjs", "app", "api", "save-layout", "route.ts"),
    ),
    false,
  );
});
