import path from "node:path";

import {
  artifactInventory,
  assertManifestDigest,
  canonicalSha256,
  digestExcluding,
  ensureFreshDirectory,
  loadArtifact,
  parsePairs,
  readJson,
  writeFreshJson,
} from "./lib.mjs";

function jsonRecord(binding, label) {
  const record = loadArtifact(binding, label);
  let value;
  try { value = JSON.parse(record.bytes.toString("utf8")); } catch { throw new Error(`${label}-json-invalid`); }
  return { record, value };
}

export function executeGate0E(options) {
  const manifest = readJson(path.resolve(options.manifestPath));
  const envelope = readJson(path.resolve(options.observationsPath));
  assertManifestDigest(manifest, "gate0e");
  if (manifest.schemaVersion !== "authored-editable-hybrid-gate0e-case-manifest/v1" || manifest.noSkip !== true || manifest.requiredCaseCount !== 15 || manifest.cases.length !== 15) throw new Error("gate0e-manifest-shape-invalid");
  if (envelope.schemaVersion !== "authored-editable-hybrid-gate0e-observations/v1" || envelope.authorityDomain !== "structural_test") throw new Error("gate0e-observation-envelope-invalid");
  const observations = new Map((envelope.cases || []).map((entry) => [entry.caseId, entry]));
  if (observations.size !== 15 || envelope.cases.length !== 15) throw new Error("gate0e-case-cardinality-invalid");
  const allRecords = [];
  const seenArtifactIds = new Set();
  const orderedResults = manifest.cases.map((contract, index) => {
    if (contract.ordinal !== index + 1) throw new Error(`gate0e-manifest-ordinal:${contract.caseId}`);
    const observation = observations.get(contract.caseId);
    if (!observation || observation.ordinal !== contract.ordinal || observation.skipped === true) throw new Error(`gate0e-observation-missing:${contract.caseId}`);
    if (observation.httpStatus !== contract.expectedHttpStatus || observation.errorCode !== contract.expectedErrorCode) throw new Error(`gate0e-rejection-mismatch:${contract.caseId}`);
    if (observation.mutationApplied !== false || observation.sealedCompletionDelta !== 0 || observation.activeBindingChanged !== false) throw new Error(`gate0e-fail-closed-invariant:${contract.caseId}`);
    const bindings = [observation.requestArtifact, observation.responseArtifact, observation.beforeStateArtifact, observation.afterStateArtifact];
    const loaded = bindings.map((binding, artifactIndex) => jsonRecord(binding, `gate0e-${contract.caseId}-${artifactIndex}`));
    for (const { record } of loaded) {
      if (seenArtifactIds.has(record.artifactId)) throw new Error(`gate0e-duplicate-artifact-id:${record.artifactId}`);
      seenArtifactIds.add(record.artifactId); allRecords.push(record);
    }
    if (canonicalSha256(loaded[2].value) !== canonicalSha256(loaded[3].value)) throw new Error(`gate0e-state-mutated:${contract.caseId}`);
    const response = loaded[1].value;
    if (response.httpStatus !== contract.expectedHttpStatus || response.errorCode !== contract.expectedErrorCode) throw new Error(`gate0e-response-artifact-mismatch:${contract.caseId}`);
    const core = { ordinal: contract.ordinal, caseId: contract.caseId, state: "rejected-fail-closed", observationDigest: canonicalSha256(observation), artifactIds: loaded.map(({ record }) => record.artifactId) };
    return { ordinal: core.ordinal, caseId: core.caseId, state: core.state, resultDigest: canonicalSha256(core) };
  });
  ensureFreshDirectory(path.resolve(options.outputDir), "gate0e-output");
  const inventory = artifactInventory(allRecords);
  const result = {
    schemaVersion: "authored-editable-hybrid-gate0e-result-set/v1",
    profileId: manifest.profileId,
    authorityDomain: "structural_test",
    productionAuthorityGranted: false,
    manifestDigest: manifest.manifestDigest,
    attempt: envelope.attempt,
    suiteStatus: "structurally-reachable",
    executedCount: orderedResults.length,
    skippedCount: 0,
    orderedResults,
    artifactInventory: inventory,
    artifactInventoryDigest: canonicalSha256(inventory),
    resultCoreDigest: "",
    resultSetDigest: "",
  };
  result.resultCoreDigest = digestExcluding(result, ["resultCoreDigest", "resultSetDigest"]);
  result.resultSetDigest = digestExcluding(result, ["resultSetDigest"]);
  writeFreshJson(path.join(path.resolve(options.outputDir), "gate0e-result-set.json"), result);
  writeFreshJson(path.join(path.resolve(options.outputDir), "gate0e-summary.json"), { suiteStatus: result.suiteStatus, executedCount: result.executedCount, skippedCount: 0, resultSetDigest: result.resultSetDigest });
  return result;
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = executeGate0E(parsePairs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify({ suiteStatus: result.suiteStatus, executedCount: result.executedCount, resultSetDigest: result.resultSetDigest })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
