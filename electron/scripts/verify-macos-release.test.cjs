const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { validateMacosRelease } = require("./verify-macos-release.cjs");
const { hasNotarizationCredentials } = require("./notarize-macos.cjs");

function arm64MachO() {
  const value = Buffer.alloc(32);
  value.writeUInt32BE(0xfeedfacf, 0);
  value.writeUInt32BE(0x0100000c, 4);
  return value;
}

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-macos-release-"));
  const app = path.join(root, "Presenton Open Source.app");
  for (const relative of [
    "Contents/MacOS/Presenton Open Source",
    "Contents/Resources/app/resources/fastapi/fastapi",
    "Contents/Resources/app/resources/export/py/convert-darwin-arm64",
  ]) {
    const filePath = path.join(app, relative);
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, arm64MachO());
  }
  return { root, app };
}

test("validates an arm64 bundle and invokes release checks only when required", (t) => {
  const { root, app } = fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const calls = [];
  assert.deepEqual(validateMacosRelease({ appPath: app, requireSignature: true, requireNotarization: true, run: (...args) => calls.push(args) }), { appPath: app });
  assert.deepEqual(calls.map(([command]) => command), ["codesign", "xcrun", "spctl"]);
});

test("rejects a missing arm64 export converter", (t) => {
  const { root, app } = fixture();
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.unlinkSync(path.join(app, "Contents/Resources/app/resources/export/py/convert-darwin-arm64"));
  assert.throws(() => validateMacosRelease({ appPath: app }), /Export converter is missing/);
});

test("requires all three Apple credentials before notarization", () => {
  assert.equal(hasNotarizationCredentials({ APPLE_ID: "a", APPLE_APP_SPECIFIC_PASSWORD: "p" }), false);
  assert.equal(hasNotarizationCredentials({ APPLE_ID: "a", APPLE_APP_SPECIFIC_PASSWORD: "p", APPLE_TEAM_ID: "t" }), true);
});
