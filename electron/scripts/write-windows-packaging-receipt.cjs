const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

function sha256(filePath) {
  const hash = crypto.createHash("sha256");
  const descriptor = fs.openSync(filePath, "r");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytesRead;
    do {
      bytesRead = fs.readSync(descriptor, buffer, 0, buffer.length, null);
      if (bytesRead > 0) hash.update(buffer.subarray(0, bytesRead));
    } while (bytesRead > 0);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest("hex");
}

function inventory(root) {
  const files = [];
  let treeFileCount = 0;
  let totalBytes = 0;
  const pending = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(fullPath);
      if (entry.isFile()) {
        const stat = fs.statSync(fullPath);
        treeFileCount += 1;
        totalBytes += stat.size;
        if (current === root) {
          files.push({
            path: entry.name,
            bytes: stat.size,
            sha256: sha256(fullPath),
          });
        }
      }
    }
  }
  files.sort((left, right) => left.path.localeCompare(right.path));
  return { files, treeFileCount, totalBytes };
}

function main() {
  const [rootArg, outputArg, kind = "windows-release-artifacts"] = process.argv.slice(2);
  if (!rootArg || !outputArg) {
    throw new Error("Usage: node write-windows-packaging-receipt.cjs <root> <output> [kind]");
  }
  const root = path.resolve(rootArg);
  const output = path.resolve(outputArg);
  const inventoryResult = inventory(root);
  const receipt = {
    schema: "presenton.windows-packaging-receipt/v1",
    kind,
    root: path.relative(path.dirname(output), root).replaceAll(path.sep, "/"),
    treeFileCount: inventoryResult.treeFileCount,
    totalBytes: inventoryResult.totalBytes,
    topLevelFiles: inventoryResult.files,
  };
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, `${JSON.stringify(receipt, null, 2)}\n`, "utf8");
  console.log(
    `Wrote ${output}: ${receipt.treeFileCount} files, ${receipt.totalBytes} bytes, ` +
      `${receipt.topLevelFiles.length} top-level hashes`
  );
}

try {
  main();
} catch (error) {
  console.error(`[windows-packaging-receipt] ${error.message}`);
  process.exitCode = 1;
}
