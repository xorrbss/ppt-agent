const fs = require("node:fs");
const path = require("node:path");
const {
  ensureRendererReadinessOverlay,
} = require("../scripts/lib/presentation-export-readiness.cjs");

function readInstalledVersion(markerFile) {
  if (!fs.existsSync(markerFile)) return null;
  return fs.readFileSync(markerFile, "utf8").trim() || null;
}

function writeInstalledVersionAtomic(version, markerFile) {
  fs.mkdirSync(path.dirname(markerFile), { recursive: true });
  const tempFile = `${markerFile}.${process.pid}.${Date.now()}.tmp`;
  try {
    fs.writeFileSync(tempFile, `${version}\n`, "utf8");
    try {
      fs.renameSync(tempFile, markerFile);
    } catch {
      fs.copyFileSync(tempFile, markerFile);
      fs.rmSync(tempFile, { force: true });
    }
  } finally {
    fs.rmSync(tempFile, { force: true });
  }
}

function validateRendererReadiness(runtimeRoot, { repair = true } = {}) {
  return ensureRendererReadinessOverlay(runtimeRoot, { repair });
}

function prepareRuntimeReadiness(runtimeRoot) {
  const result = validateRendererReadiness(runtimeRoot, { repair: true });
  if (!result.ok) throw new Error(result.reason);
}

module.exports = {
  prepareRuntimeReadiness,
  readInstalledVersion,
  validateRendererReadiness,
  writeInstalledVersionAtomic,
};
