const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

function fail(message) { throw new Error(message); }

function findAppBundle(root) {
  const pending = [root];
  while (pending.length) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const entryPath = path.join(current, entry.name);
      if (entry.isDirectory() && entry.name.endsWith(".app")) return entryPath;
      if (entry.isDirectory()) pending.push(entryPath);
    }
  }
  fail(`No .app bundle found under: ${root}`);
}

function readMachArchitectures(filePath) {
  const bytes = fs.readFileSync(filePath);
  if (bytes.length < 8) return [];
  const magic = bytes.readUInt32BE(0);
  // A thin 64-bit Mach-O header has CPU type at offset 4. CPU_TYPE_ARM64=0x0100000c.
  if (magic === 0xfeedfacf || magic === 0xcffaedfe) {
    const cpuType = magic === 0xfeedfacf ? bytes.readUInt32BE(4) : bytes.readUInt32LE(4);
    return cpuType === 0x0100000c ? ["arm64"] : ["other"];
  }
  // FAT_MAGIC/FAT_CIGAM, with 20-byte fat_arch records beginning at byte 8.
  if (magic === 0xcafebabe || magic === 0xbebafeca) {
    const littleEndian = magic === 0xbebafeca;
    const read = littleEndian ? Buffer.prototype.readUInt32LE : Buffer.prototype.readUInt32BE;
    const count = read.call(bytes, 4);
    const architectures = [];
    for (let index = 0; index < count && 8 + (index + 1) * 20 <= bytes.length; index += 1) {
      const cpuType = read.call(bytes, 8 + index * 20);
      architectures.push(cpuType === 0x0100000c ? "arm64" : "other");
    }
    return architectures;
  }
  return [];
}

function requireArm64MachO(filePath, label) {
  if (!fs.existsSync(filePath)) fail(`${label} is missing: ${filePath}`);
  if (!readMachArchitectures(filePath).includes("arm64")) {
    fail(`${label} is not an arm64 Mach-O binary: ${filePath}`);
  }
}

function validateMacosRelease(options = {}) {
  const root = path.resolve(options.distRoot || path.join(__dirname, "..", "dist"));
  const appPath = options.appPath || findAppBundle(root);
  const appName = path.basename(appPath, ".app");
  const contents = path.join(appPath, "Contents");
  requireArm64MachO(path.join(contents, "MacOS", appName), "Electron executable");
  const resources = path.join(contents, "Resources", "app", "resources");
  requireArm64MachO(path.join(resources, "fastapi", "fastapi"), "FastAPI executable");
  requireArm64MachO(path.join(resources, "export", "py", "convert-darwin-arm64"), "Export converter");

  const run = options.run || ((command, args) => execFileSync(command, args, { stdio: "pipe" }));
  if (options.requireSignature) run("codesign", ["--verify", "--deep", "--strict", appPath]);
  if (options.requireNotarization) {
    run("xcrun", ["stapler", "validate", appPath]);
    run("spctl", ["--assess", "--type", "execute", "--verbose=4", appPath]);
  }
  return { appPath };
}

function main() {
  try {
    const args = process.argv.slice(2);
    const valid = new Set(["--require-signature", "--require-notarization"]);
    const unknown = args.filter((arg) => arg.startsWith("-") && !valid.has(arg));
    if (unknown.length) fail(`Unknown option: ${unknown.join(", ")}`);
    const roots = args.filter((arg) => !arg.startsWith("-"));
    if (roots.length > 1) fail("Expected at most one dist path");
    const result = validateMacosRelease({
      distRoot: roots[0], requireSignature: args.includes("--require-signature"),
      requireNotarization: args.includes("--require-notarization"),
    });
    console.log(`[macos-release-verify] OK: ${result.appPath}`);
  } catch (error) {
    console.error(`[macos-release-verify] ${error.message}`);
    process.exitCode = 1;
  }
}

if (require.main === module) main();
else module.exports = { findAppBundle, readMachArchitectures, validateMacosRelease };
