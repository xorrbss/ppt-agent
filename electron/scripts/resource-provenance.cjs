const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const SCHEMA = "presenton.resource-provenance/v1";
const MANIFEST_NAME = ".source-provenance.json";
const COMMON_EXCLUDED_DIRECTORIES = new Set([
  ".git",
  ".next",
  ".next-build",
  ".pytest_cache",
  ".venv",
  "__pycache__",
  "app_data",
  "coverage",
  "cypress",
  "dist",
  "fixtures",
  "htmlcov",
  "node_modules",
  "playwright-report",
  "tests",
  "__tests__",
  "test-results",
]);

function normalizedRelative(root, target) {
  return path.relative(root, target).split(path.sep).join("/");
}

function listSourceFiles(sourceRoot) {
  const root = path.resolve(sourceRoot);
  if (!fs.statSync(root, { throwIfNoEntry: false })?.isDirectory()) {
    throw new Error(`Provenance source root is missing: ${root}`);
  }
  const files = [];
  const pending = [root];
  while (pending.length > 0) {
    const current = pending.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      if (entry.name === ".env" || entry.name.startsWith(".env.")) continue;
      if (
        entry.isDirectory() &&
        (COMMON_EXCLUDED_DIRECTORIES.has(entry.name) || entry.name.startsWith(".pytest"))
      ) continue;
      const candidate = path.join(current, entry.name);
      if (entry.isDirectory()) pending.push(candidate);
      else if (
        entry.isFile() &&
        !/\.(?:test|spec|cy)\.[^.]+$/u.test(entry.name)
      ) files.push(candidate);
    }
  }
  return files.sort((left, right) =>
    normalizedRelative(root, left).localeCompare(normalizedRelative(root, right))
  );
}

function digestSourceTree(sourceRoot) {
  const root = path.resolve(sourceRoot);
  const digest = crypto.createHash("sha256");
  let bytes = 0;
  const files = listSourceFiles(root);
  for (const file of files) {
    const content = fs.readFileSync(file);
    const relative = normalizedRelative(root, file);
    digest.update(Buffer.from(`${relative}\0${content.length}\0`, "utf8"));
    digest.update(content);
    digest.update(Buffer.from("\0", "utf8"));
    bytes += content.length;
  }
  return { digest: digest.digest("hex"), files: files.length, bytes };
}

function gitHead(workspaceRoot) {
  const result = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: workspaceRoot,
    encoding: "utf8",
    windowsHide: true,
  });
  const candidate = result.status === 0 ? result.stdout.trim() : "";
  return /^[0-9a-f]{40}$/i.test(candidate) ? candidate.toLowerCase() : null;
}

function buildManifest({ kind, sourceRoot, workspaceRoot }) {
  const identity = digestSourceTree(sourceRoot);
  return {
    schema: SCHEMA,
    kind,
    source: {
      root: normalizedRelative(path.resolve(workspaceRoot), path.resolve(sourceRoot)),
      sha256: identity.digest,
      fileCount: identity.files,
      byteCount: identity.bytes,
      gitHead: gitHead(workspaceRoot),
    },
  };
}

function writeResourceProvenance({ kind, sourceRoot, resourceRoot, workspaceRoot }) {
  const manifest = buildManifest({ kind, sourceRoot, workspaceRoot });
  fs.mkdirSync(resourceRoot, { recursive: true });
  const target = path.join(resourceRoot, MANIFEST_NAME);
  fs.writeFileSync(target, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return manifest;
}

function verifyResourceProvenance({ kind, sourceRoot, resourceRoot, workspaceRoot }) {
  const manifestPath = path.join(resourceRoot, MANIFEST_NAME);
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  } catch (error) {
    throw new Error(`Resource provenance is missing or invalid for ${kind}: ${error.message}`);
  }
  const current = buildManifest({ kind, sourceRoot, workspaceRoot });
  if (manifest.schema !== SCHEMA || manifest.kind !== kind) {
    throw new Error(`Resource provenance identity is invalid for ${kind}`);
  }
  for (const key of ["root", "sha256", "fileCount", "byteCount"]) {
    if (manifest.source?.[key] !== current.source[key]) {
      throw new Error(
        `Packaged ${kind} resource is stale: source ${key} changed; rebuild the resource`
      );
    }
  }
  return manifest;
}

module.exports = {
  MANIFEST_NAME,
  SCHEMA,
  buildManifest,
  digestSourceTree,
  verifyResourceProvenance,
  writeResourceProvenance,
};
