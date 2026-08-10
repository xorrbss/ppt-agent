import { mkdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceCommit = process.argv[2];
const sourcePath = process.argv[3];

if (!/^[0-9a-f]{7,40}$/i.test(sourceCommit ?? "")) {
  throw new Error("usage: import-upstream-template-package.mjs <commit> templates/<id>");
}
if (!/^templates\/[a-z0-9][a-z0-9-]*$/.test(sourcePath ?? "")) {
  throw new Error("template source path must be one confined templates/<id> directory");
}

function git(args, { encoding = null } = {}) {
  const result = spawnSync("git", args, {
    cwd: repositoryRoot,
    encoding,
    maxBuffer: 32 * 1024 * 1024,
  });
  if (result.status !== 0) {
    throw new Error(`git ${args[0]} failed: ${String(result.stderr)}`);
  }
  return result.stdout;
}

const listing = git(
  ["ls-tree", "-r", "--format=%(objectmode) %(objecttype) %(objectname) %(path)", sourceCommit, "--", sourcePath],
  { encoding: "utf8" },
)
  .trim()
  .split(/\r?\n/)
  .filter(Boolean);

if (listing.length === 0) throw new Error("upstream template tree is empty");
for (const line of listing) {
  const match = /^(\d{6}) (\S+) ([0-9a-f]{40}) (.+)$/.exec(line);
  if (!match) throw new Error(`unexpected git tree record: ${line}`);
  const [, mode, objectType, objectId, relative] = match;
  if (mode !== "100644" || objectType !== "blob") {
    throw new Error(`upstream template special entry rejected: ${relative}`);
  }
  const normalized = relative.replaceAll("\\", "/");
  if (
    !normalized.startsWith(`${sourcePath}/`) ||
    normalized.includes("/../") ||
    path.posix.isAbsolute(normalized)
  ) {
    throw new Error(`upstream template path escaped root: ${relative}`);
  }
  const destination = path.resolve(repositoryRoot, ...normalized.split("/"));
  const allowedRoot = path.resolve(repositoryRoot, ...sourcePath.split("/"));
  if (!destination.startsWith(`${allowedRoot}${path.sep}`)) {
    throw new Error(`upstream template destination escaped root: ${relative}`);
  }
  const bytes = git(["cat-file", "blob", objectId]);
  const expectedSize = Number(git(["cat-file", "-s", objectId], { encoding: "utf8" }).trim());
  if (bytes.length !== expectedSize) {
    throw new Error(`upstream template blob size mismatch: ${relative}`);
  }
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, bytes);
  if ((await stat(destination)).size !== expectedSize) {
    throw new Error(`upstream template write verification failed: ${relative}`);
  }
}

console.log(`Imported ${listing.length} byte-exact files from ${sourceCommit}:${sourcePath}.`);
