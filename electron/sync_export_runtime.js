const fs = require("fs");
const path = require("path");
const targetRoot = path.join(__dirname, "resources", "export");
const targetPyDir = path.join(targetRoot, "py");
const targetIndex = path.join(targetRoot, "index.js");
const targetConvertDefault = path.join(targetPyDir, "convert");

function getConverterCandidates() {
  const tagged = path.join(
    targetPyDir,
    `convert-${process.platform}-${process.arch}${process.platform === "win32" ? ".exe" : ""}`
  );
  const platformOnly = path.join(
    targetPyDir,
    `convert-${process.platform}${process.platform === "win32" ? ".exe" : ""}`
  );
  const legacy = process.platform === "win32"
    ? [path.join(targetPyDir, "convert.exe"), targetConvertDefault]
    : [targetConvertDefault];

  return [tagged, platformOnly, ...legacy];
}

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

function detectBinaryFormat(filePath) {
  const fd = fs.openSync(filePath, "r");
  try {
    const header = Buffer.alloc(4);
    fs.readSync(fd, header, 0, 4, 0);

    if (header[0] === 0x7f && header[1] === 0x45 && header[2] === 0x4c && header[3] === 0x46) {
      return "elf";
    }

    if (header[0] === 0x4d && header[1] === 0x5a) {
      return "pe";
    }

    const magic = header.readUInt32BE(0);
    if (
      magic === 0xfeedface ||
      magic === 0xcefaedfe ||
      magic === 0xfeedfacf ||
      magic === 0xcffaedfe ||
      magic === 0xcafebabe ||
      magic === 0xbebafeca
    ) {
      return "mach-o";
    }

    return "unknown";
  } finally {
    fs.closeSync(fd);
  }
}

function isFormatCompatible(format) {
  if (process.platform === "darwin") return format === "mach-o";
  if (process.platform === "linux") return format === "elf";
  if (process.platform === "win32") return format === "pe";
  return true;
}

function main() {
  ensureExists(
    targetIndex,
    "Committed runtime JS bundle (electron/resources/export/index.js)"
  );

  const converterCandidates = getConverterCandidates();
  const converterPath = converterCandidates.find((candidate) => fs.existsSync(candidate));

  if (!converterPath) {
    throw new Error(
      [
        "No converter binary found in electron/resources/export/py.",
        "Expected one of:",
        ...converterCandidates.map((candidate) => `  - ${candidate}`),
      ].join("\n")
    );
  }

  const binaryFormat = detectBinaryFormat(converterPath);
  if (!isFormatCompatible(binaryFormat)) {
    throw new Error(
      [
        `Converter binary is not valid for ${process.platform}/${process.arch}.`,
        `Selected converter: ${converterPath}`,
        `Detected format: ${binaryFormat}`,
        "Bundle a platform-correct converter binary (e.g. convert-darwin-arm64, convert-darwin-x64, convert-linux-x64, convert.exe).",
      ].join("\n")
    );
  }

  chmodIfPossible(converterPath);

  console.log("[export-runtime] Using committed runtime artifacts:");
  console.log(`  - ${targetIndex}`);
  console.log(`  - ${converterPath}`);
}

main();
