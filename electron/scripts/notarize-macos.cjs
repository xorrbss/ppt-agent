/* Notarize a signed macOS bundle using only Xcode command-line tools. */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

function requiredEnvironment() {
  return ["APPLE_ID", "APPLE_APP_SPECIFIC_PASSWORD", "APPLE_TEAM_ID"];
}

function hasNotarizationCredentials(env = process.env) {
  return requiredEnvironment().every((name) => Boolean(env[name]?.trim()));
}

exports.default = async function notarizeMacos(context) {
  if (context.electronPlatformName !== "darwin") return;
  const requireNotarization = process.env.REQUIRE_MACOS_NOTARIZATION === "true";
  if (!hasNotarizationCredentials()) {
    if (requireNotarization) {
      throw new Error(
        `macOS notarization requires ${requiredEnvironment().join(", ")}`
      );
    }
    console.warn("[notarize] Skipped: Apple notarization credentials are not configured.");
    return;
  }

  const appPath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.app`
  );
  if (!fs.existsSync(appPath)) throw new Error(`macOS app bundle is missing: ${appPath}`);
  const archivePath = path.join(os.tmpdir(), `presenton-notarize-${Date.now()}.zip`);
  try {
    execFileSync("ditto", ["-c", "-k", "--keepParent", appPath, archivePath], { stdio: "inherit" });
    execFileSync(
      "xcrun",
      [
        "notarytool", "submit", archivePath, "--wait",
        "--apple-id", process.env.APPLE_ID,
        "--password", process.env.APPLE_APP_SPECIFIC_PASSWORD,
        "--team-id", process.env.APPLE_TEAM_ID,
      ],
      { stdio: "inherit" }
    );
    execFileSync("xcrun", ["stapler", "staple", appPath], { stdio: "inherit" });
    execFileSync("xcrun", ["stapler", "validate", appPath], { stdio: "inherit" });
  } finally {
    fs.rmSync(archivePath, { force: true });
  }
};

module.exports = { hasNotarizationCredentials, requiredEnvironment, default: exports.default };
