const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  expectedAssetSha256,
  getPlatformAssetName,
  helpText,
  parseSyncCliArgs,
  resolveTargetVersion,
  SUPPORTED_EXPORT_RUNTIME_ASSETS,
  verifyDownloadedAsset,
} = require("./export-runtime-sync-contract.cjs");

const electronRoot = path.join(__dirname, "..");
const repoRoot = path.join(electronRoot, "..");
const canonicalManifest = path.join(repoRoot, "compatibility", "upstream-compatibility.json");

function withTempManifest(value, run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "electron-export-contract-"));
  const manifestPath = path.join(root, "manifest.json");
  fs.writeFileSync(manifestPath, JSON.stringify(value));
  try {
    return run({ root, manifestPath });
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
}

test("sync CLI rejects unknown options and documents explicit version override", () => {
  assert.deepEqual(parseSyncCliArgs(["--force", "--allow-version-override"]), {
    forceDownload: true,
    checkOnly: false,
    allowVersionOverride: true,
    showHelp: false,
  });
  assert.throws(() => parseSyncCliArgs(["--ambient-override"]), /Unknown option/);
  assert.match(helpText, /--allow-version-override\s+Honor EXPORT_RUNTIME_VERSION/);
});

test("ambient version cannot bypass the package pin without explicit opt-in", async () => {
  let latestRequests = 0;
  assert.equal(
    await resolveTargetVersion({
      env: { EXPORT_RUNTIME_VERSION: "latest" },
      pinnedVersion: "v0.4.8",
      resolveLatest: async () => {
        latestRequests += 1;
        return "v9.9.9";
      },
    }),
    "v0.4.8"
  );
  assert.equal(latestRequests, 0);
});

test("explicit dev override resolves both fixed and latest versions", async () => {
  assert.equal(
    await resolveTargetVersion({
      env: { EXPORT_RUNTIME_VERSION: "v0.4.9" },
      pinnedVersion: "v0.4.8",
      allowVersionOverride: true,
      resolveLatest: async () => "unused",
    }),
    "v0.4.9"
  );
  assert.equal(
    await resolveTargetVersion({
      env: { EXPORT_RUNTIME_VERSION: "latest" },
      pinnedVersion: "v0.4.8",
      allowVersionOverride: true,
      resolveLatest: async () => "v0.5.0",
    }),
    "v0.5.0"
  );
});

test("supported Electron platforms map to checksum-manifest asset names", () => {
  assert.equal(getPlatformAssetName("linux", "x64"), "export-Linux-X64.zip");
  assert.equal(getPlatformAssetName("linux", "arm64"), "export-Linux-ARM64.zip");
  assert.equal(getPlatformAssetName("darwin", "x64"), "export-macOS-X64.zip");
  assert.equal(getPlatformAssetName("darwin", "arm64"), "export-macOS-ARM64.zip");
  assert.equal(getPlatformAssetName("win32", "x64"), "export-Windows-X64.zip");
  assert.equal(getPlatformAssetName("win32", "ia32"), "export-Windows-ia32.zip");
  assert.throws(() => getPlatformAssetName("win32", "arm64"), /Unsupported/);
});

test("canonical v0.4.8 manifest pins exactly the shared six assets", () => {
  const manifest = JSON.parse(fs.readFileSync(canonicalManifest, "utf8"));
  assert.deepEqual(
    Object.keys(manifest.exportRuntime.assets),
    SUPPORTED_EXPORT_RUNTIME_ASSETS
  );
  for (const assetName of SUPPORTED_EXPORT_RUNTIME_ASSETS) {
    assert.match(
      expectedAssetSha256({ tag: "v0.4.8", assetName, manifestPath: canonicalManifest }),
      /^[0-9a-f]{64}$/
    );
  }
});

test("missing version, asset, or valid SHA-256 fails closed", () => {
  withTempManifest({ exportRuntime: { version: "v0.4.7", assets: {} } }, ({ manifestPath }) => {
    assert.throws(
      () => expectedAssetSha256({ tag: "v0.4.8", assetName: "runtime.zip", manifestPath }),
      /No checksum manifest/
    );
  });
  withTempManifest(
    { exportRuntime: { version: "v0.4.8", assets: { "runtime.zip": "bad" } } },
    ({ manifestPath }) => {
      assert.throws(
        () => expectedAssetSha256({ tag: "v0.4.8", assetName: "runtime.zip", manifestPath }),
        /Missing or invalid SHA-256/
      );
    }
  );
});

test("downloaded archive tampering is rejected before extraction", () => {
  const expectedBytes = Buffer.from("trusted archive bytes");
  const expectedHash = crypto.createHash("sha256").update(expectedBytes).digest("hex");
  withTempManifest(
    { exportRuntime: { version: "v0.4.8", assets: { "runtime.zip": expectedHash } } },
    ({ root, manifestPath }) => {
      const archive = path.join(root, "runtime.zip");
      fs.writeFileSync(archive, expectedBytes);
      assert.equal(
        verifyDownloadedAsset(archive, "v0.4.8", "runtime.zip", { manifestPath }),
        expectedHash
      );
      fs.appendFileSync(archive, "tampered");
      assert.throws(
        () => verifyDownloadedAsset(archive, "v0.4.8", "runtime.zip", { manifestPath }),
        /Checksum mismatch/
      );
    }
  );

  const syncSource = fs.readFileSync(path.join(electronRoot, "sync_export_runtime.js"), "utf8");
  const installSource = syncSource.slice(syncSource.indexOf("async function downloadAndInstallRuntime"));
  assert.ok(
    installSource.indexOf("verifyDownloadedAsset(zipPath") <
      installSource.indexOf("unzipArchive(zipPath")
  );
});

test("only the dev latest script opts into overrides; package flows remain pinned", () => {
  const packageJson = JSON.parse(fs.readFileSync(path.join(electronRoot, "package.json"), "utf8"));
  assert.match(packageJson.scripts["fetch:export-runtime:latest"], /--allow-version-override/);
  for (const name of ["build:export-runtime", "build:electron", "dist"]) {
    assert.doesNotMatch(packageJson.scripts[name], /EXPORT_RUNTIME_VERSION|allow-version-override/);
  }
});
