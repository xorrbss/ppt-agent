import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, readFileSync, readlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// This override is deliberately test-only: it lets the behavior tests exercise
// real Git repositories without ever changing this checkout's index or refs.
const repoRoot = process.env.UPSTREAM_BYTE_PARITY_REPO
  ? path.resolve(process.env.UPSTREAM_BYTE_PARITY_REPO)
  : scriptRoot;
const compatibilityPath = path.join(repoRoot, "compatibility", "upstream-compatibility.json");
const parityPath = path.join(repoRoot, "compatibility", "upstream-byte-parity.json");
const compatibility = JSON.parse(readFileSync(compatibilityPath, "utf8"));
const baselineSha = compatibility.baseline.upstreamSha;

function parseArguments(argv) {
  let write = false;
  let source = { mode: "tree", treeish: "HEAD" };
  let sourceSelected = false;

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--write") {
      write = true;
      continue;
    }
    if (argument === "--tree") {
      const treeish = argv[index + 1];
      if (!treeish || treeish.startsWith("--")) {
        throw new Error("--tree requires a Git tree-ish");
      }
      if (sourceSelected) {
        throw new Error("choose exactly one source: --tree, --index, or --working-tree");
      }
      source = { mode: "tree", treeish };
      sourceSelected = true;
      index += 1;
      continue;
    }
    if (argument === "--index" || argument === "--working-tree") {
      if (sourceSelected) {
        throw new Error("choose exactly one source: --tree, --index, or --working-tree");
      }
      source = { mode: argument.slice(2) };
      sourceSelected = true;
      continue;
    }
    throw new Error(`unknown argument: ${argument}`);
  }

  return { write, source };
}

const { write, source } = parseArguments(process.argv.slice(2));

function git(args, options = {}) {
  return execFileSync("git", args, {
    cwd: repoRoot,
    encoding: options.encoding ?? "utf8",
    input: options.input,
    maxBuffer: 256 * 1024 * 1024,
  });
}

function gitStatus(args) {
  try {
    git(args);
    return true;
  } catch (error) {
    if (error?.status === 1) return false;
    throw error;
  }
}

function parseNullDelimited(value) {
  return value.split("\0").filter(Boolean);
}

function readTree(treeish) {
  const commit = git(["rev-parse", "--verify", `${treeish}^{commit}`]).trim();
  const raw = git(["ls-tree", "-r", "-z", "--full-tree", commit]);
  const entries = new Map();
  for (const record of parseNullDelimited(raw)) {
    const match = /^(\d+) (\w+) ([0-9a-f]+)\t([\s\S]+)$/.exec(record);
    assert.ok(match, `unexpected ls-tree record: ${record}`);
    const [, mode, type, oid, filePath] = match;
    assert.ok(!entries.has(filePath), `duplicate tree entry: ${filePath}`);
    entries.set(filePath, { mode, type, oid });
  }
  return { commit, entries };
}

function parseIndex() {
  const entries = new Map();
  for (const record of parseNullDelimited(git(["ls-files", "--stage", "-z"]))) {
    const match = /^(\d+) ([0-9a-f]+) (\d)\t([\s\S]+)$/.exec(record);
    assert.ok(match, `unexpected ls-files record: ${record}`);
    const [, mode, oid, stage, filePath] = match;
    assert.equal(stage, "0", `cannot verify an unmerged index entry: ${filePath}`);
    assert.ok(!entries.has(filePath), `duplicate index entry: ${filePath}`);
    entries.set(filePath, { mode, type: mode === "160000" ? "commit" : "blob", oid });
  }
  return entries;
}

function absoluteWorkingPath(filePath) {
  const absolutePath = path.resolve(repoRoot, filePath);
  assert.ok(
    absolutePath === repoRoot || absolutePath.startsWith(`${repoRoot}${path.sep}`),
    `Git path escapes repository root: ${filePath}`,
  );
  return absolutePath;
}

function workingEntry(filePath, indexEntry) {
  const absolutePath = absoluteWorkingPath(filePath);
  const stats = lstatSync(absolutePath);
  if (stats.isSymbolicLink()) {
    return {
      mode: "120000",
      type: "blob",
      oid: git(["hash-object", "--stdin"], { input: readlinkSync(absolutePath) }).trim(),
    };
  }
  assert.ok(stats.isFile(), `working-tree path is not a regular file or symlink: ${filePath}`);

  // Windows does not preserve POSIX executable bits in a checkout. Preserve the
  // tracked index mode there; on POSIX, lstat makes chmod-only changes visible.
  const mode = process.platform === "win32" && indexEntry
    ? indexEntry.mode
    : stats.mode & 0o111 ? "100755" : "100644";
  assert.ok(mode === "100644" || mode === "100755", `unsupported working-tree mode for ${filePath}: ${mode}`);
  return {
    mode,
    type: "blob",
    oid: git(["hash-object", `--path=${filePath}`, "--stdin"], { input: readFileSync(absolutePath) }).trim(),
  };
}

function workingModeChanges(index) {
  if (process.platform === "win32") return [];
  const changed = [];
  for (const [filePath, indexEntry] of index) {
    if (indexEntry.type !== "blob" || indexEntry.mode === "120000") continue;
    try {
      const stats = lstatSync(absoluteWorkingPath(filePath));
      if (stats.isFile()) {
        const mode = stats.mode & 0o111 ? "100755" : "100644";
        if (mode !== indexEntry.mode) changed.push(filePath);
      }
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
  }
  return changed;
}

function readWorkingTree(upstream) {
  const entries = parseIndex();
  const changedPaths = new Set([
    ...parseNullDelimited(git(["diff-files", "--name-only", "-z"])),
    ...workingModeChanges(entries),
  ]);
  for (const filePath of changedPaths) {
    const indexEntry = entries.get(filePath);
    try {
      entries.set(filePath, workingEntry(filePath, indexEntry));
    } catch (error) {
      if (error?.code === "ENOENT") {
        entries.delete(filePath);
      } else {
        throw error;
      }
    }
  }

  const localOnly = [];
  for (const filePath of parseNullDelimited(git(["ls-files", "--others", "--exclude-standard", "-z"]))) {
    if (upstream.has(filePath)) {
      entries.set(filePath, workingEntry(filePath));
    } else {
      // This deliberately records only the path. It keeps this manifest's own
      // generated bytes outside its classification input and avoids a duplicate
      // large source snapshot.
      localOnly.push(filePath);
    }
  }
  return { entries, localOnly };
}

function sourceReceipt(source) {
  if (source.mode === "tree") {
    // The receipt preserves both the requested ref and the resolved immutable
    // tree. The policy deliberately does not compare its own generated bytes,
    // so committing this receipt cannot make the classification self-referential.
    return { mode: "tree", ref: source.treeish, commit: readTree(source.treeish).commit };
  }
  if (source.mode === "index") return { mode: "index" };
  return {
    mode: "working-tree",
    trackedRule: "Index entries overlaid with unstaged tracked file changes, deletions, and POSIX chmod changes.",
    untrackedRule: "Nonignored untracked paths reported by git ls-files --others --exclude-standard.",
  };
}

function readSource(source, upstream) {
  if (source.mode === "tree") return { entries: readTree(source.treeish).entries, localOnly: [] };
  if (source.mode === "index") return { entries: parseIndex(), localOnly: [] };
  return readWorkingTree(upstream);
}

function assertCleanHeadEquivalent(source) {
  if (source.mode !== "tree") return;
  const sourceCommit = git(["rev-parse", "--verify", `${source.treeish}^{commit}`]).trim();
  const headCommit = git(["rev-parse", "--verify", "HEAD^{commit}"]).trim();
  if (sourceCommit !== headCommit) return;

  const isClean = gitStatus(["diff", "--quiet", "--ignore-submodules", "--cached", "HEAD", "--"])
    && gitStatus(["diff-files", "--quiet", "--ignore-submodules", "--"])
    && parseNullDelimited(git(["ls-files", "--others", "--exclude-standard", "-z"])).length === 0;
  assert.ok(
    isClean,
    "--tree HEAD requires a clean index-equivalent working tree (no staged/unstaged changes or nonignored untracked paths); use --index or --working-tree for an explicit mutable snapshot",
  );
}

const exceptionReasons = {
  "auth-ownership-transaction": "Local authentication, ownership, revision, and transactional workspace contracts intentionally extend the upstream backend.",
  "authored-export-runtime": "Local authored, PDF/PPTX, Electron, and bounded export-runtime behavior is protected by dedicated regression and security tests.",
  "compatibility-control": "Local compatibility policy, ledgers, CI, and verification code have no upstream byte-for-byte counterpart.",
  "local-fork-replacement": "The fork intentionally replaces or removes this upstream path; the path remains an explicit reviewed exception rather than an implicit drift.",
  "studio-template-v2": "Local Template V2 Studio editing, strict validation, table round-trip, and lossless payload adapters extend the upstream editor contract.",
  "tiptap3-editor-adapter": "The local editor preserves the Tiptap 3 contract while upstream editor sources currently target Tiptap 2 APIs.",
};

function reasonCode(filePath) {
  if (filePath.startsWith("compatibility/") || filePath.startsWith("scripts/verify-upstream") || filePath.startsWith(".github/workflows/upstream-")) return "compatibility-control";
  if (filePath.startsWith("servers/nextjs/components/slide-editor/") || filePath.includes("Tiptap")) return "tiptap3-editor-adapter";
  if (filePath.startsWith("servers/nextjs/app/template-v2-studio/") || filePath.startsWith("servers/nextjs/lib/template-v2") || filePath.includes("template-preview")) return "studio-template-v2";
  if (filePath.startsWith("servers/fastapi/")) return "auth-ownership-transaction";
  if (filePath.startsWith("electron/") || filePath.startsWith("presentation-export/") || filePath.includes("export-presentation") || filePath.includes("authored")) return "authored-export-runtime";
  return "local-fork-replacement";
}

function buildPolicyAndCounts() {
  const upstream = readTree(baselineSha).entries;
  const local = readSource(source, upstream);
  const exceptions = [];
  for (const [filePath, upstreamEntry] of [...upstream].sort(([a], [b]) => a.localeCompare(b))) {
    const localEntry = local.entries.get(filePath);
    if (localEntry && localEntry.mode === upstreamEntry.mode && localEntry.type === upstreamEntry.type && localEntry.oid === upstreamEntry.oid) continue;
    exceptions.push({
      path: filePath,
      upstreamMode: upstreamEntry.mode,
      upstreamBlob: upstreamEntry.oid,
      localState: localEntry ? "modified" : "deleted",
      reasonCode: reasonCode(filePath),
    });
  }
  const localOnly = [...new Set([...local.entries.keys(), ...local.localOnly])]
    .filter((filePath) => !upstream.has(filePath))
    .sort();
  const policy = {
    exactDefinition: "Git mode, object type, and blob object ID all match the pinned upstream commit.",
    exceptionRule: "Every non-identical upstream-owned path must remain explicitly listed with a reviewed reason code.",
    localOnlyRule: "Paths absent from the pinned upstream tree are local extensions and are listed separately.",
    selfReferentialManifestRule: "The parity manifest is classified by its path, not its generated bytes, so a working-tree manifest can verify itself safely.",
    exceptionReasons,
    exceptions,
    localOnly,
  };
  return {
    policy,
    counts: {
      upstreamOwned: upstream.size,
      identical: upstream.size - exceptions.length,
      exceptions: exceptions.length,
      localOnly: localOnly.length,
    },
  };
}

function receiptFor(policy, counts) {
  return {
    source: sourceReceipt(source),
    counts,
    policySha256: createHash("sha256").update(JSON.stringify(policy)).digest("hex"),
  };
}

function buildManifest() {
  const { policy, counts } = buildPolicyAndCounts();
  return {
    schemaVersion: 2,
    baseline: { repository: compatibility.baseline.repository, sha: baselineSha },
    policy,
    receipt: receiptFor(policy, counts),
  };
}

assertCleanHeadEquivalent(source);
const actual = buildManifest();
if (write) {
  writeFileSync(parityPath, `${JSON.stringify(actual, null, 2)}\n`);
  console.log(`Wrote upstream byte parity policy and receipt: ${actual.receipt.counts.identical} identical, ${actual.receipt.counts.exceptions} exceptions, ${actual.receipt.counts.localOnly} local-only`);
} else {
  const expected = JSON.parse(readFileSync(parityPath, "utf8"));
  assert.equal(expected.schemaVersion, 2, "upstream byte parity manifest must use schemaVersion 2");
  assert.deepEqual(expected.baseline, actual.baseline, "upstream byte parity baseline differs");
  assert.equal(expected.receipt.policySha256, createHash("sha256").update(JSON.stringify(expected.policy)).digest("hex"), "stored byte parity policy digest is invalid");
  assert.deepEqual(actual.policy, expected.policy, "upstream byte parity policy is stale; regenerate it with --write after reviewing every changed classification");
  assert.deepEqual(actual.receipt.counts, expected.receipt.counts, "upstream byte parity receipt counts are stale");
  console.log(`Upstream byte parity verified: ${actual.receipt.counts.identical} identical, ${actual.receipt.counts.exceptions} documented exceptions, ${actual.receipt.counts.localOnly} local-only`);
}
