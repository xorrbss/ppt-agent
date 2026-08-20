const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const PLATFORM_ASSET_NAMES = Object.freeze({
  "linux-x64": "export-Linux-X64.zip",
  "linux-arm64": "export-Linux-ARM64.zip",
  "darwin-x64": "export-macOS-X64.zip",
  "darwin-arm64": "export-macOS-ARM64.zip",
  "win32-x64": "export-Windows-X64.zip",
  "win32-ia32": "export-Windows-ia32.zip",
});

const SUPPORTED_EXPORT_RUNTIME_ASSETS = Object.freeze(
  Object.values(PLATFORM_ASSET_NAMES)
);

function getPlatformAssetName(platform = process.platform, arch = process.arch) {
  const platformArch = `${platform}-${arch}`;
  const assetName = PLATFORM_ASSET_NAMES[platformArch];
  if (!assetName) {
    throw new Error(`Unsupported export runtime platform: ${platformArch}`);
  }
  return assetName;
}

function expectedAssetSha256({ tag, assetName, manifestPath }) {
  if (!manifestPath) throw new Error("Export checksum manifest path is required");
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (error) {
    throw new Error(
      `Cannot read export checksum manifest ${path.resolve(manifestPath)}: ${error.message}`
    );
  }
  const runtime = manifest.exportRuntime;
  if (!runtime || runtime.version !== tag) {
    throw new Error(`No checksum manifest is pinned for presentation-export ${tag}`);
  }
  const checksum = runtime.assets?.[assetName];
  if (typeof checksum !== "string" || !/^[0-9a-f]{64}$/.test(checksum)) {
    throw new Error(`Missing or invalid SHA-256 for ${assetName}@${tag}`);
  }
  return checksum;
}

function verifyDownloadedAsset(filePath, tag, assetName, options = {}) {
  const expected = expectedAssetSha256({ tag, assetName, ...options });
  const actual = crypto
    .createHash("sha256")
    .update(fs.readFileSync(filePath))
    .digest("hex");
  if (
    !crypto.timingSafeEqual(
      Buffer.from(actual, "hex"),
      Buffer.from(expected, "hex")
    )
  ) {
    throw new Error(
      `Checksum mismatch for ${assetName}@${tag}: expected ${expected}, got ${actual}`
    );
  }
  return actual;
}

module.exports = {
  PLATFORM_ASSET_NAMES,
  SUPPORTED_EXPORT_RUNTIME_ASSETS,
  expectedAssetSha256,
  getPlatformAssetName,
  verifyDownloadedAsset,
};
