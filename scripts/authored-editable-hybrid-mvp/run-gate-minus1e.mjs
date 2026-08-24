import fs from "node:fs";
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
  sameStrings,
  writeFreshJson,
} from "./lib.mjs";

function attemptIdentity(attempt) {
  for (const key of ["presentationId", "generationAttemptId", "hybridExportAttemptId", "sceneDigest", "activeManifestDigest"]) {
    if (typeof attempt?.[key] !== "string" || !attempt[key]) throw new Error(`gate-minus1e-attempt-pin-missing:${key}`);
  }
  if (!Number.isInteger(attempt.presentationRevision) || attempt.presentationRevision < 1) {
    throw new Error("gate-minus1e-presentation-revision-invalid");
  }
  return attempt;
}

export function executeGateMinus1E(options) {
  const manifest = readJson(path.resolve(options.manifestPath));
  const envelope = readJson(path.resolve(options.observationsPath));
  assertManifestDigest(manifest, "gate-minus1e");
  if (manifest.schemaVersion !== "authored-editable-hybrid-gate-minus1e-case-manifest/v1" || manifest.noSkip !== true || manifest.requiredCaseCount !== 15 || manifest.cases.length !== 15) {
    throw new Error("gate-minus1e-manifest-shape-invalid");
  }
  if (envelope.schemaVersion !== "authored-editable-hybrid-gate-minus1e-observations/v1" || envelope.authorityDomain !== manifest.authorityDomain) {
    throw new Error("gate-minus1e-observation-envelope-invalid");
  }
  const attempt = attemptIdentity(envelope.attempt);
  const records = (envelope.artifacts || []).map((binding) => loadArtifact(binding, "gate-minus1e-artifact"));
  const recordIds = new Set(records.map((entry) => entry.artifactId));
  if (recordIds.size !== records.length || records.length < 1) throw new Error("gate-minus1e-artifact-set-invalid");
  const observations = new Map((envelope.cases || []).map((entry) => [entry.caseId, entry]));
  if (observations.size !== 15 || envelope.cases.length !== 15) throw new Error("gate-minus1e-case-cardinality-invalid");

  const orderedResults = manifest.cases.map((contract, index) => {
    if (contract.ordinal !== index + 1) throw new Error(`gate-minus1e-manifest-ordinal:${contract.caseId}`);
    const observation = observations.get(contract.caseId);
    if (!observation || observation.ordinal !== contract.ordinal || observation.state !== "satisfied" || observation.skipped === true) {
      throw new Error(`gate-minus1e-case-not-satisfied:${contract.caseId}`);
    }
    if (!Array.isArray(observation.evidenceArtifactIds) || observation.evidenceArtifactIds.length < 1 || observation.evidenceArtifactIds.some((id) => !recordIds.has(id))) {
      throw new Error(`gate-minus1e-case-evidence-invalid:${contract.caseId}`);
    }
    for (const [field, expected] of Object.entries(contract.expected)) {
      if (observation.fields?.[field] !== expected) throw new Error(`gate-minus1e-field-mismatch:${contract.caseId}:${field}`);
    }
    if (Array.isArray(observation.fields?.expectedQids) && !sameStrings(observation.fields.expectedQids, attempt.expectedQids || [])) {
      throw new Error(`gate-minus1e-qid-pin-mismatch:${contract.caseId}`);
    }
    const core = { ordinal: contract.ordinal, caseId: contract.caseId, state: "satisfied", observationDigest: canonicalSha256(observation), evidenceArtifactIds: observation.evidenceArtifactIds };
    return { ordinal: core.ordinal, caseId: core.caseId, state: core.state, resultDigest: canonicalSha256(core) };
  });

  ensureFreshDirectory(path.resolve(options.outputDir), "gate-minus1e-output");
  const inventory = artifactInventory(records);
  const result = {
    schemaVersion: "authored-editable-hybrid-gate-minus1e-result-set/v1",
    profileId: manifest.profileId,
    authorityDomain: manifest.authorityDomain,
    manifestDigest: manifest.manifestDigest,
    attempt,
    suiteStatus: "feasible",
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
  writeFreshJson(path.join(path.resolve(options.outputDir), "gate-minus1e-result-set.json"), result);
  writeFreshJson(path.join(path.resolve(options.outputDir), "gate-minus1e-summary.json"), { suiteStatus: result.suiteStatus, executedCount: result.executedCount, skippedCount: 0, resultSetDigest: result.resultSetDigest });
  return result;
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = executeGateMinus1E(parsePairs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify({ suiteStatus: result.suiteStatus, executedCount: result.executedCount, resultSetDigest: result.resultSetDigest })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
