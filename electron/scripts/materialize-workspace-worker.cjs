const fs = require("node:fs");
const path = require("node:path");

function materializeWorkspaceWorker(fastapiRoot, platform = process.platform) {
  const extension = platform === "win32" ? ".exe" : "";
  const apiBinary = path.join(fastapiRoot, `fastapi${extension}`);
  const workerBinary = path.join(
    fastapiRoot,
    `workspace-operation-worker${extension}`,
  );
  if (!fs.statSync(apiBinary, { throwIfNoEntry: false })?.isFile()) {
    throw new Error(`FastAPI executable is missing: ${apiBinary}`);
  }
  fs.copyFileSync(apiBinary, workerBinary);
  if (platform !== "win32") fs.chmodSync(workerBinary, 0o755);
  return workerBinary;
}

if (require.main === module) {
  try {
    const root = path.join(__dirname, "..", "resources", "fastapi");
    console.log(`[workspace-worker] Materialized ${materializeWorkspaceWorker(root)}`);
  } catch (error) {
    console.error(`[workspace-worker] ${error.message}`);
    process.exitCode = 1;
  }
} else {
  module.exports = { materializeWorkspaceWorker };
}
