const path = require("node:path");
const {
  SUPPORTED_EXPORT_RUNTIME_ASSETS,
  expectedAssetSha256: expectedSharedAssetSha256,
  getPlatformAssetName,
  verifyDownloadedAsset: verifySharedDownloadedAsset,
} = require("../../scripts/lib/presentation-export-assets.cjs");

const helpText = [
  "Usage: node sync_export_runtime.js [options]",
  "",
  "Options:",
  "  --force                   Re-download even when the runtime is valid",
  "  --check-only              Verify installed artifacts without changing them",
  "  --allow-version-override  Honor EXPORT_RUNTIME_VERSION instead of the package pin",
  "  --help                    Show this help",
].join("\n");

function parseSyncCliArgs(args = process.argv.slice(2)) {
  const supported = new Set([
    "--force",
    "--check-only",
    "--allow-version-override",
    "--help",
  ]);
  const unknown = args.filter((arg) => !supported.has(arg));
  if (unknown.length > 0) {
    throw new Error(
      `Unknown option${unknown.length === 1 ? "" : "s"}: ${unknown.join(", ")}\n\n${helpText}`
    );
  }
  const parsed = new Set(args);
  return {
    forceDownload: parsed.has("--force"),
    checkOnly: parsed.has("--check-only"),
    allowVersionOverride: parsed.has("--allow-version-override"),
    showHelp: parsed.has("--help"),
  };
}

async function resolveTargetVersion({
  env = process.env,
  pinnedVersion,
  allowVersionOverride = false,
  resolveLatest,
}) {
  const ambient = String(env.EXPORT_RUNTIME_VERSION || "").trim();
  const requested = allowVersionOverride && ambient ? ambient : pinnedVersion;
  if (!requested) throw new Error("Electron package.json must pin exportVersion");
  return requested === "latest" ? resolveLatest() : requested;
}

function expectedAssetSha256({
  tag,
  assetName,
  manifestPath = path.join(__dirname, "..", "..", "compatibility", "upstream-compatibility.json"),
}) {
  return expectedSharedAssetSha256({ tag, assetName, manifestPath });
}

function verifyDownloadedAsset(filePath, tag, assetName, options = {}) {
  return verifySharedDownloadedAsset(filePath, tag, assetName, {
    manifestPath: path.join(
      __dirname,
      "..",
      "..",
      "compatibility",
      "upstream-compatibility.json"
    ),
    ...options,
  });
}

module.exports = {
  expectedAssetSha256,
  getPlatformAssetName,
  SUPPORTED_EXPORT_RUNTIME_ASSETS,
  helpText,
  parseSyncCliArgs,
  resolveTargetVersion,
  verifyDownloadedAsset,
};
