const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const repoRoot = path.resolve(__dirname, "..");
const exportProjectDir = path.join(repoRoot, "presenton-export-opensource");
const sourceIndex = path.join(exportProjectDir, "dist", "index.js");
const sourceConvert = path.join(exportProjectDir, "dist", "py", "convert");
const targetRoot = path.join(__dirname, "resources", "export");
const targetPyDir = path.join(targetRoot, "py");
const targetIndex = path.join(targetRoot, "index.js");
const targetConvert = path.join(targetPyDir, "convert");

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    shell: process.platform === "win32",
  });

  if (result.status !== 0) {
    throw new Error(`Command failed: ${command} ${args.join(" ")}`);
  }
}

function ensureExists(filePath, label) {
  if (!fs.existsSync(filePath)) {
    throw new Error(`${label} not found at: ${filePath}`);
  }
}

function copyFile(source, target) {
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.copyFileSync(source, target);
}

function chmodIfPossible(filePath) {
  if (process.platform !== "win32") {
    fs.chmodSync(filePath, 0o755);
  }
}

function main() {
  console.log("[export-runtime] Building export runtime artifacts...");
  run("bun", ["run", "build:all"], exportProjectDir);

  ensureExists(sourceIndex, "Export runtime JS bundle");
  ensureExists(sourceConvert, "Export runtime converter binary");

  copyFile(sourceIndex, targetIndex);
  copyFile(sourceConvert, targetConvert);
  chmodIfPossible(targetConvert);

  console.log("[export-runtime] Synced files:");
  console.log(`  - ${targetIndex}`);
  console.log(`  - ${targetConvert}`);
}

main();
