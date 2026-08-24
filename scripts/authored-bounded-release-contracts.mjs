import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const SHA256 = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const INT64_MIN = -(2n ** 63n);
const INT64_MAX = (2n ** 63n) - 1n;

/** The v2 profile is intentionally stricter than JSON.parse/stringify. */
export function normalizeString(value) {
  return value.replace(/\r\n?/g, "\n").normalize("NFC");
}

function utf8Compare(left, right) {
  return Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));
}

function canonicalInteger(value, pathName) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`canonical-number-${Number.isNaN(value) ? "nan" : "infinity"}:${pathName}`);
  }
  if (Object.is(value, -0)) throw new TypeError(`canonical-number-negative-zero:${pathName}`);
  if (!Number.isInteger(value)) throw new TypeError(`canonical-number-fractional:${pathName}`);
  if (!Number.isSafeInteger(value)) throw new TypeError(`canonical-number-out-of-safe-range:${pathName}`);
  const integer = BigInt(value);
  if (integer < INT64_MIN || integer > INT64_MAX) throw new TypeError(`canonical-number-int64:${pathName}`);
  return String(integer);
}

function arraySortKey(pathName) {
  const keys = {
    "$.capabilityIds": "scalar",
    "$.receiptDigests": "scalar",
    "$.orderedResults": "ordinal,caseId",
    "$.cases": "ordinal,caseId",
    "$.artifacts": "artifactId,sha256",
    "$.fontFaces": "family,subfamily,weight,italic,ttcCollectionIndex",
  };
  return keys[pathName] || null;
}

function compareArrayEntries(left, right, sortKey) {
  if (sortKey === "scalar") return utf8Compare(String(left), String(right));
  const fields = sortKey.split(",");
  for (const field of fields) {
    const l = left?.[field];
    const r = right?.[field];
    if (typeof l === "number" && typeof r === "number" && l !== r) return l - r;
    const result = utf8Compare(String(l ?? ""), String(r ?? ""));
    if (result !== 0) return result;
  }
  return 0;
}

/** Canonical JSON v2. Arrays are sorted only when their exact path is registered. */
export function canonicalJson(value, pathName = "$", seen = new Set()) {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(normalizeString(value));
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return canonicalInteger(value, pathName);
  if (Array.isArray(value)) {
    if (seen.has(value)) throw new TypeError(`canonical-cycle:${pathName}`);
    seen.add(value);
    const sortKey = arraySortKey(pathName);
    const entries = sortKey ? [...value].sort((a, b) => compareArrayEntries(a, b, sortKey)) : value;
    const result = `[${entries.map((entry, index) => canonicalJson(entry, `${pathName}[${index}]`, seen)).join(",")}]`;
    seen.delete(value);
    return result;
  }
  if (typeof value === "object") {
    if (seen.has(value)) throw new TypeError(`canonical-cycle:${pathName}`);
    seen.add(value);
    const entries = Object.entries(value).map(([key, entry]) => [normalizeString(key), entry]);
    if (new Set(entries.map(([key]) => key)).size !== entries.length) throw new TypeError(`canonical-key-nfc-collision:${pathName}`);
    entries.sort(([left], [right]) => utf8Compare(left, right));
    const result = `{${entries.map(([key, entry]) => `${JSON.stringify(key)}:${canonicalJson(entry, `${pathName}.${key}`, seen)}`).join(",")}}`;
    seen.delete(value);
    return result;
  }
  throw new TypeError(`canonical-unsupported-type:${pathName}`);
}

export function canonicalSha256(value, options = {}) {
  return `sha256:${crypto.createHash("sha256").update(canonicalJson(value), "utf8").digest("hex")}`;
}

function splitJsonPath(jsonPath) {
  if (jsonPath === "$") return [];
  if (!jsonPath.startsWith("$.")) throw new TypeError(`digest-path-invalid:${jsonPath}`);
  return jsonPath.slice(2).split(".");
}

/** Remove only the exact declared path; nested/similarly named fields remain included. */
export function projectExcluding(value, excludedJsonPaths = []) {
  if (!Array.isArray(excludedJsonPaths) || excludedJsonPaths.some((entry) => typeof entry !== "string")) {
    throw new TypeError("digest-exclusions-invalid");
  }
  const paths = excludedJsonPaths.map(splitJsonPath);
  const clone = structuredClone(value);
  const remove = (node, segments, fullPath) => {
    for (const candidate of paths) {
      if (candidate.length === segments.length && candidate.every((part, i) => part === segments[i])) {
        return undefined;
      }
    }
    if (Array.isArray(node)) return node.map((entry, index) => remove(entry, [...segments, String(index)], `${fullPath}[${index}]`));
    if (node && typeof node === "object") {
      for (const [key, child] of Object.entries(node)) {
        const updated = remove(child, [...segments, key], `${fullPath}.${key}`);
        if (updated === undefined && paths.some((candidate) => candidate.length === segments.length + 1 && candidate.at(-1) === key && candidate.slice(0, -1).every((part, i) => part === segments[i]))) delete node[key];
        else node[key] = updated;
      }
    }
    return node;
  };
  return remove(clone, [], "$");
}

export function canonicalSha256Excluding(value, excludedJsonPaths) {
  return canonicalSha256(projectExcluding(value, excludedJsonPaths));
}

export function assertDigest(value, label = "digest") {
  if (typeof value !== "string" || !SHA256.test(value)) throw new TypeError(`invalid-digest:${label}`);
  return value;
}

export function encodeHexCarrier(prefix, value, expectedBytes) {
  const bytes = Buffer.isBuffer(value) ? value : Buffer.from(value, "utf8");
  if (expectedBytes !== undefined && bytes.length !== expectedBytes) throw new TypeError(`carrier-length:${prefix}`);
  return `${prefix}_${bytes.toString("hex").toUpperCase()}`;
}

export function decodeHexCarrier(value, prefix, expectedBytes) {
  if (typeof value !== "string" || !new RegExp(`^${prefix}_[0-9A-F]+$`).test(value)) throw new TypeError(`carrier-uppercase-required:${prefix}`);
  const hex = value.slice(prefix.length + 1);
  if (hex.length % 2 !== 0 || (expectedBytes !== undefined && hex.length / 2 !== expectedBytes)) throw new TypeError(`carrier-length:${prefix}`);
  return Buffer.from(hex, "hex");
}

export function encodeDigestCarrier(digest) {
  assertDigest(digest);
  return encodeHexCarrier("D2", Buffer.from(digest.slice("sha256:".length), "hex"), 32);
}

export function decodeDigestCarrier(value) {
  const bytes = decodeHexCarrier(value, "D2", 32);
  return `sha256:${bytes.toString("hex")}`;
}

export function encodeUuidCarrier(uuid) {
  if (typeof uuid !== "string" || !UUID.test(uuid)) throw new TypeError("invalid-uuid");
  return encodeHexCarrier("U2", Buffer.from(uuid.replaceAll("-", ""), "hex"), 16);
}

export function decodeUuidCarrier(value) {
  const bytes = decodeHexCarrier(value, "U2", 16);
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function validateCountContract(gate, manifest, result = null) {
  const expected = { "gate-minus1": 18, gate0: 34, gate1: 37 }[gate];
  if (!expected) throw new TypeError(`unknown-gate:${gate}`);
  const entries = gate === "gate1" ? manifest.phases : manifest.cases;
  if (!Array.isArray(entries) || entries.length !== expected) throw new TypeError(`manifest-count:${gate}:${entries?.length ?? "missing"}`);
  const ids = entries.map((entry) => entry.caseId ?? entry.phaseId);
  if (new Set(ids).size !== expected || entries.some((entry, index) => entry.ordinal !== index + 1)) throw new TypeError(`manifest-id-order:${gate}`);
  if (result) {
    const resultEntries = result.orderedResults;
    if (!Array.isArray(resultEntries) || resultEntries.length !== expected) throw new TypeError(`result-count:${gate}`);
    const resultIds = resultEntries.map((entry) => entry.caseId ?? entry.phaseId);
    if (JSON.stringify(resultIds) !== JSON.stringify(ids)) throw new TypeError(`result-id-set:${gate}`);
    if (result.skippedCaseCount !== undefined && result.skippedCaseCount !== 0) throw new TypeError(`result-skipped:${gate}`);
    if (result.skippedPhaseCount !== undefined && result.skippedPhaseCount !== 0) throw new TypeError(`result-skipped:${gate}`);
  }
  return expected;
}

export function validateGate1Ledger(ledger, slideCount = 8) {
  const exportModes = ["fidelity", "hybrid"];
  const comPhases = ["first-open", "reopen"];
  const defectFields = ["repair-dialog", "qid-missing", "qid-duplicate-or-unexpected", "clipping", "overlap", "ghosting", "native-raster-collision", "line-break-drift", "font-substitution", "chart-data-or-style-mismatch", "crop-or-sentinel-mismatch", "css-paint-or-isolation-drift"];
  const expected = new Set();
  for (let slide = 1; slide <= slideCount; slide += 1) for (const mode of exportModes) for (const phase of comPhases) for (const defect of defectFields) expected.add(`${slide}:${mode}:${phase}:${defect}`);
  const actual = new Set((Array.isArray(ledger) ? ledger : []).map((cell) => typeof cell === "string" ? cell : `${cell.slideNumber}:${cell.exportMode}:${cell.comPhase}:${cell.defectFieldId}`));
  if (actual.size !== expected.size || [...expected].some((key) => !actual.has(key))) throw new TypeError(`ledger-cardinality:${actual.size}/${expected.size}`);
  return expected.size;
}

export function validateGate1Artifacts(manifest, result = null) {
  const required = manifest?.requiredArtifactIds;
  if (!Array.isArray(required) || required.length !== 70 || new Set(required).size !== 70) throw new TypeError(`artifact-manifest-cardinality:${required?.length ?? "missing"}/70`);
  if (!result) return 70;
  const inventory = result.artifactInventory;
  if (!Array.isArray(inventory) || inventory.length !== 70) throw new TypeError(`artifact-result-cardinality:${inventory?.length ?? "missing"}/70`);
  const ids = inventory.map((artifact) => artifact.artifactId);
  if (new Set(ids).size !== 70 || [...required].some((id) => !ids.includes(id))) throw new TypeError("artifact-id-set-mismatch");
  for (const artifact of inventory) {
    if (typeof artifact.versionId !== "string" || !artifact.versionId) throw new TypeError(`artifact-version-missing:${artifact.artifactId}`);
    assertDigest(artifact.sha256, `artifact:${artifact.artifactId}`);
    if (!Number.isInteger(artifact.byteLength) || artifact.byteLength < 1) throw new TypeError(`artifact-byte-length:${artifact.artifactId}`);
    if (typeof artifact.mediaType !== "string" || !artifact.mediaType) throw new TypeError(`artifact-media-type:${artifact.artifactId}`);
  }
  return 70;
}

export function validatePolicyBinding(manifest, policy) {
  const policyDigest = manifest?.evidencePolicyDigest ?? manifest?.policyCandidateDigest;
  if (!assertDigest(policyDigest, "manifest-policy")) return false;
  if (policy) {
    const projection = policy.digestProjection?.excludedJsonPaths;
    if (JSON.stringify(projection) !== JSON.stringify(["$.policyDigest"])) throw new TypeError("policy-digest-projection-not-self-only");
    if (policy.policyDigest !== policyDigest) throw new TypeError("policy-digest-mismatch");
    if (canonicalSha256Excluding(policy, ["$.policyDigest"]) !== policyDigest) throw new TypeError("policy-digest-invalid");
  }
  return true;
}

function git(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], { encoding: "utf8" }).trim();
}

function gitBuffer(cwd, ...args) {
  return execFileSync("git", ["-C", cwd, ...args], { maxBuffer: 64 * 1024 * 1024 });
}

function gitPathList(cwd, ...args) {
  return gitBuffer(cwd, ...args).toString("utf8").split("\0").filter(Boolean).map((value) => value.replaceAll("\\", "/")).sort();
}

function digestBytes(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}

function pathDigest(root, relativePath, index = false) {
  const absolute = path.join(root, relativePath);
  try {
    const bytes = index ? gitBuffer(root, "show", `:${relativePath}`) : fs.readFileSync(absolute);
    return { path: relativePath, present: true, byteLength: bytes.length, sha256: digestBytes(bytes) };
  } catch {
    try {
      const bytes = gitBuffer(root, "show", `HEAD:${relativePath}`);
      return { path: relativePath, present: false, byteLength: bytes.length, sha256: digestBytes(bytes) };
    } catch {
      return { path: relativePath, present: false, byteLength: 0, sha256: null };
    }
  }
}

export function captureSourceTreeCensus(repoRoot, options = {}) {
  const root = path.resolve(repoRoot);
  const repositoryId = options.repositoryId || (() => {
    try { return git(root, "config", "--get", "remote.origin.url"); } catch { return root; }
  })();
  const headCommit = git(root, "rev-parse", "HEAD");
  const stagedPaths = gitPathList(root, "diff", "--cached", "--name-only", "--no-renames", "-z");
  const trackedModifiedPaths = gitPathList(root, "diff", "--name-only", "--no-renames", "-z");
  const untrackedPaths = gitPathList(root, "ls-files", "--others", "--exclude-standard", "-z");
  const ignoredPaths = gitPathList(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z");
  const generatedCacheAllowlistPrefixes = [...new Set((options.generatedCacheAllowlistPrefixes || []).map((value) => String(value).replaceAll("\\", "/").replace(/^\.\//, "")))].sort();
  const generatedIgnoredDirectoryNames = [...new Set((options.generatedIgnoredDirectoryNames || []).map((value) => String(value).trim()).filter(Boolean))].sort();
  const isGeneratedCachePath = (relativePath) => generatedCacheAllowlistPrefixes.some((prefix) => prefix && relativePath.startsWith(prefix));
  const isGeneratedIgnoredDirectoryPath = (relativePath) => relativePath.split("/").some((segment) => generatedIgnoredDirectoryNames.includes(segment));
  const isGeneratedIgnoredPath = (relativePath) => isGeneratedCachePath(relativePath) || isGeneratedIgnoredDirectoryPath(relativePath);
  const generatedCacheObservedPathSet = [...new Set(untrackedPaths.filter(isGeneratedCachePath).concat(ignoredPaths.filter(isGeneratedIgnoredPath)))].sort();
  const sourceUntrackedPaths = untrackedPaths.filter((relativePath) => !isGeneratedCachePath(relativePath));
  const sourceIgnoredPaths = ignoredPaths.filter((relativePath) => !isGeneratedIgnoredPath(relativePath));
  const allowlist = [...new Set((options.ignoredAllowlist || []).map((value) => String(value).replaceAll("\\", "/").replace(/^\.\//, "")))].sort();
  const isAllowed = (relativePath) => allowlist.some((allowed) => relativePath === allowed || relativePath.startsWith(`${allowed}/`));
  const unallowlisted = sourceIgnoredPaths.filter((relativePath) => !isAllowed(relativePath));
  if (unallowlisted.length > 0) throw new TypeError(`source-census-ignored-path-not-allowlisted:${unallowlisted[0]}`);
  const ignorePolicy = { schemaVersion: "SourceTreeIgnoreAllowlistV1", paths: allowlist };
  const ignoredAllowlistPolicyDigest = options.ignoredAllowlistPolicyDigest || canonicalSha256(ignorePolicy);
  assertDigest(ignoredAllowlistPolicyDigest, "ignored-allowlist-policy");
  const indexListing = gitBuffer(root, "ls-files", "--stage", "-z");
  const runtimeExecutionPins = (options.runtimeExecutionPins || []).map((entry) => ({
    runtimeId: String(entry?.runtimeId || "").trim(),
    selectionRule: String(entry?.selectionRule || "").trim(),
    selectedPath: String(entry?.selectedPath || "").replaceAll("\\", "/").replace(/^\.\//, ""),
  })).sort((left, right) => utf8Compare(left.runtimeId, right.runtimeId));
  if (runtimeExecutionPins.some((entry) => !entry.runtimeId || !entry.selectionRule || !entry.selectedPath || /^(?:[A-Za-z]:)?\//.test(entry.selectedPath) || entry.selectedPath.split("/").some((segment) => segment === "..")) || new Set(runtimeExecutionPins.map((entry) => entry.runtimeId)).size !== runtimeExecutionPins.length) {
    throw new TypeError("source-census-runtime-execution-pins-invalid");
  }
  const runtimeExecutionPathDigests = runtimeExecutionPins.map((entry) => {
    const record = pathDigest(root, entry.selectedPath);
    if (!record.present || !record.sha256) throw new TypeError(`source-census-runtime-execution-path-missing:${entry.selectedPath}`);
    return { ...entry, byteLength: record.byteLength, sha256: record.sha256 };
  });
  const core = {
    schemaVersion: "SourceTreeCensusV1",
    repositoryId,
    repositoryRoot: root,
    headCommit,
    head: headCommit,
    indexTreeDigest: digestBytes(indexListing),
    stagedPathDigests: stagedPaths.map((relativePath) => pathDigest(root, relativePath, true)),
    trackedModifiedPathDigests: trackedModifiedPaths.map((relativePath) => pathDigest(root, relativePath)),
    untrackedPathDigests: sourceUntrackedPaths.map((relativePath) => pathDigest(root, relativePath)),
    ignoredAllowlistPolicyDigest,
    ignoredAllowlistPathDigests: sourceIgnoredPaths.map((relativePath) => pathDigest(root, relativePath)),
    runtimeExecutionPathDigests,
    generatedCacheAllowlistPrefixes,
    generatedIgnoredDirectoryNames,
    generatedCachePolicyDigest: options.generatedCachePolicyDigest || canonicalSha256({ schemaVersion: "SourceTreeGeneratedCachePolicyV1", prefixes: generatedCacheAllowlistPrefixes, ignoredDirectoryNames: generatedIgnoredDirectoryNames }),
    toolVersion: options.toolVersion || "presenton-source-tree-census/v1",
  };
  assertDigest(core.generatedCachePolicyDigest, "generated-cache-policy");
  const dirtyPaths = [...new Set([...stagedPaths, ...trackedModifiedPaths, ...sourceUntrackedPaths, ...sourceIgnoredPaths, ...generatedCacheObservedPathSet])].sort();
  const censusDigest = canonicalSha256(core);
  return { ...core, generatedCacheObservedPathSet, generatedCacheObservedPathSetDigest: canonicalSha256(generatedCacheObservedPathSet), dirtyPaths, captureTime: options.captureTime || new Date().toISOString(), censusDigest };
}

export function assertSourceTreeCensusUnchanged(opening, closing) {
  if (!opening || !closing || opening.censusDigest !== closing.censusDigest) throw new TypeError("source-census-changed");
  const stable = (value) => {
    const copy = structuredClone(value);
    delete copy.captureTime;
    delete copy.censusDigest;
    delete copy.generatedCacheObservedPathSet;
    delete copy.generatedCacheObservedPathSetDigest;
    delete copy.dirtyPaths;
    return canonicalJson(copy);
  };
  if (stable(opening) !== stable(closing)) throw new TypeError("source-census-changed");
  return true;
}
