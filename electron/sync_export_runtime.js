const fs = require("fs");
const path = require("path");
const targetRoot = path.join(__dirname, "resources", "export");
const targetPyDir = path.join(targetRoot, "py");
const targetIndex = path.join(targetRoot, "index.js");
const targetConvert = path.join(targetPyDir, "convert");

function ensureExists(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`${label} not found at: ${filePath}`);
  }
}

function chmodIfPossible(filePath) {
  if (process.platform !== "win32") {
    fs.chmodSync(filePath, 0o755);
  }
}

function main() {
  ensureExists(
    targetIndex,
    "Committed runtime JS bundle (electron/resources/export/index.js)"
  );
  ensureExists(
    targetConvert,
    "Committed runtime converter binary (electron/resources/export/py/convert)"
  );
  chmodIfPossible(targetConvert);

  console.log("[export-runtime] Using committed runtime artifacts:");
  console.log(`  - ${targetIndex}`);
  console.log(`  - ${targetConvert}`);
}

main();
