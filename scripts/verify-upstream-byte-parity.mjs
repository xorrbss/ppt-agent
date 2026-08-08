import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const compatibilityPath = path.join(repoRoot, "compatibility", "upstream-compatibility.json");
const parityPath = path.join(repoRoot, "compatibility", "upstream-byte-parity.json");
const compatibility = JSON.parse(readFileSync(compatibilityPath, "utf8"));
const baselineSha = compatibility.baseline.upstreamSha;
const write = process.argv.includes("--write");
const treeArgIndex = process.argv.indexOf("--tree");
const currentTree = treeArgIndex >= 0 ? process.argv[treeArgIndex + 1] : "HEAD";

if (treeArgIndex >= 0 && !currentTree) {
  throw new Error("--tree requires a Git tree-ish");
}

function git(args, options = {}) {
  return execFileSync("git", args, {
    cwd: repoRoot,
    encoding: options.encoding ?? "utf8",
    maxBuffer: 256 * 1024 * 1024,
  });
}

function readTree(treeish) {
  git(["cat-file", "-e", `${treeish}^{commit}`]);
  const raw = git(["ls-tree", "-r", "-z", "--full-tree", treeish]);
  const entries = new Map();
  for (const record of raw.split("\0")) {
    if (!record) continue;
    const match = /^(\d+) (\w+) ([0-9a-f]+)\t([\s\S]+)$/.exec(record);
    assert.ok(match, `unexpected ls-tree record: ${record}`);
    const [, mode, type, oid, filePath] = match;
    entries.set(filePath, { mode, type, oid });
  }
  return entries;
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
  if (filePath.startsWith("compatibility/") || filePath.startsWith("scripts/verify-upstream") || filePath.startsWith(".github/workflows/upstream-")) {
    return "compatibility-control";
  }
  if (filePath.startsWith("servers/nextjs/components/slide-editor/") || filePath.includes("Tiptap")) {
    return "tiptap3-editor-adapter";
  }
  if (filePath.startsWith("servers/nextjs/app/template-v2-studio/") || filePath.startsWith("servers/nextjs/lib/template-v2") || filePath.includes("template-preview")) {
    return "studio-template-v2";
  }
  if (filePath.startsWith("servers/fastapi/")) {
    return "auth-ownership-transaction";
  }
  if (filePath.startsWith("electron/") || filePath.startsWith("presentation-export/") || filePath.includes("export-presentation") || filePath.includes("authored")) {
    return "authored-export-runtime";
  }
  return "local-fork-replacement";
}

function buildManifest() {
  const upstream = readTree(baselineSha);
  const local = readTree(currentTree);
  const identical = [];
  const exceptions = [];

  for (const [filePath, upstreamEntry] of [...upstream].sort(([a], [b]) => a.localeCompare(b))) {
    const localEntry = local.get(filePath);
    if (localEntry && localEntry.mode === upstreamEntry.mode && localEntry.type === upstreamEntry.type && localEntry.oid === upstreamEntry.oid) {
      identical.push({ path: filePath, mode: upstreamEntry.mode, blob: upstreamEntry.oid });
      continue;
    }
    exceptions.push({
      path: filePath,
      upstreamMode: upstreamEntry.mode,
      upstreamBlob: upstreamEntry.oid,
      localState: localEntry ? "modified" : "deleted",
      reasonCode: reasonCode(filePath),
    });
  }

  const localOnly = [...local.keys()].filter((filePath) => !upstream.has(filePath)).sort();
  return {
    schemaVersion: 1,
    baseline: {
      repository: compatibility.baseline.repository,
      sha: baselineSha,
    },
    policy: {
      exactDefinition: "Git mode, object type, and blob object ID all match the pinned upstream commit.",
      exceptionRule: "Every non-identical upstream-owned path must remain explicitly listed with a reviewed reason code.",
      localOnlyRule: "Paths absent from the pinned upstream tree are local extensions and are listed separately.",
    },
    exceptionReasons,
    counts: {
      upstreamOwned: upstream.size,
      identical: identical.length,
      exceptions: exceptions.length,
      localOnly: localOnly.length,
    },
    identical,
    exceptions,
    localOnly,
  };
}

const actual = buildManifest();
if (write) {
  writeFileSync(parityPath, `${JSON.stringify(actual, null, 2)}\n`);
  console.log(`Wrote upstream byte parity manifest: ${actual.counts.identical} identical, ${actual.counts.exceptions} exceptions, ${actual.counts.localOnly} local-only`);
} else {
  const expected = JSON.parse(readFileSync(parityPath, "utf8"));
  assert.deepEqual(actual, expected, "upstream byte parity manifest is stale; run node scripts/verify-upstream-byte-parity.mjs --write after reviewing every changed classification");
  console.log(`Upstream byte parity verified: ${actual.counts.identical} identical, ${actual.counts.exceptions} documented exceptions, ${actual.counts.localOnly} local-only`);
}
