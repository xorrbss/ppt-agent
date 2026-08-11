const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

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

function getPlatformAssetName(platform = process.platform, arch = process.arch) {
  const platformArch = `${platform}-${arch}`;
  if (platformArch === "linux-x64") return "export-Linux-X64.zip";
  if (platformArch === "linux-arm64") return "export-Linux-ARM64.zip";
  if (platformArch === "darwin-x64") return "export-macOS-X64.zip";
  if (platformArch === "darwin-arm64") return "export-macOS-ARM64.zip";
  if (platformArch === "win32-x64") return "export-Windows-X64.zip";
  if (platformArch === "win32-ia32") return "export-Windows-ia32.zip";
  throw new Error(
    `Unsupported export runtime platform: ${platformArch}`
  );
}

function expectedAssetSha256({
  tag,
  assetName,
  manifestPath = path.join(__dirname, "..", "..", "compatibility", "upstream-compatibility.json"),
}) {
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (error) {
    throw new Error(`Cannot read export checksum manifest ${manifestPath}: ${error.message}`);
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
  const actual = crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
  if (!crypto.timingSafeEqual(Buffer.from(actual), Buffer.from(expected))) {
    throw new Error(
      `Checksum mismatch for ${assetName}@${tag}: expected ${expected}, got ${actual}`
    );
  }
  return actual;
}

module.exports = {
  expectedAssetSha256,
  getPlatformAssetName,
  helpText,
  parseSyncCliArgs,
  resolveTargetVersion,
  verifyDownloadedAsset,
};
