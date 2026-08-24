import fs from "node:fs";
import path from "node:path";

import {
  assertManifestDigest,
  ensureFreshDirectory,
  parsePairs,
  readJson,
  sha256Bytes,
  writeFreshJson,
} from "./lib.mjs";

function jsonBinding(artifactId, filePath, value) {
  writeFreshJson(filePath, value);
  const bytes = fs.readFileSync(filePath);
  return {
    artifactId,
    path: path.resolve(filePath),
    mediaType: "application/json",
    sha256: sha256Bytes(bytes),
    byteLength: bytes.length,
  };
}

function assertProbe(probe, manifest) {
  assertManifestDigest(manifest, "gate0e");
  if (
    manifest.schemaVersion !== "authored-editable-hybrid-gate0e-case-manifest/v1"
    || manifest.authorityDomain !== "structural_test"
    || manifest.noSkip !== true
    || manifest.requiredCaseCount !== 15
    || manifest.cases?.length !== 15
  ) throw new Error("gate0e-collector-manifest-invalid");
  if (
    probe?.schemaVersion !== "authored-editable-hybrid-gate0e-observations/v1"
    || probe.authorityDomain !== "structural_test"
    || probe.manifestDigest !== manifest.manifestDigest
    || probe.cases?.length !== 15
  ) throw new Error("gate0e-collector-probe-invalid");
  if (
    probe.allCasesPassed !== true
    || probe.stateUnchanged !== true
    || probe.promotionReady !== false
    || probe.deploymentAuthorized !== false
  ) throw new Error("gate0e-collector-authority-or-state-invalid");
}

export function materializeGate0EProbe({ probe, manifest, outputDir, probeUrl }) {
  assertProbe(probe, manifest);
  const resolvedOutput = path.resolve(outputDir);
  ensureFreshDirectory(resolvedOutput, "gate0e-collection-output");
  const probeResponsePath = path.join(resolvedOutput, "gate0e-probe-response.json");
  writeFreshJson(probeResponsePath, probe);

  const observationsById = new Map(probe.cases.map((entry) => [entry.caseId, entry]));
  if (observationsById.size !== 15) throw new Error("gate0e-collector-case-cardinality-invalid");
  const cases = manifest.cases.map((contract, index) => {
    const observation = observationsById.get(contract.caseId);
    if (
      contract.ordinal !== index + 1
      || observation?.ordinal !== contract.ordinal
      || observation.expectedHttpStatus !== contract.expectedHttpStatus
      || observation.expectedErrorCode !== contract.expectedErrorCode
      || observation.observedHttpStatus !== contract.expectedHttpStatus
      || observation.observedErrorCode !== contract.expectedErrorCode
      || observation.failClosed !== true
    ) throw new Error(`gate0e-collector-case-mismatch:${contract.caseId}`);

    const caseDirectory = path.join(resolvedOutput, String(contract.ordinal).padStart(2, "0"));
    const prefix = `gate0e-${String(contract.ordinal).padStart(2, "0")}`;
    const stableState = structuredClone(probe.baseline);
    const requestArtifact = jsonBinding(
      `${prefix}-request`,
      path.join(caseDirectory, "request.json"),
      {
        caseId: contract.caseId,
        mutationPayload: structuredClone(observation.mutationPayload),
        presentationId: probe.attempt.presentationId,
        presentationRevision: probe.attempt.presentationRevision,
        activeManifestDigest: probe.attempt.activeManifestDigest,
      },
    );
    const responseArtifact = jsonBinding(
      `${prefix}-response`,
      path.join(caseDirectory, "response.json"),
      {
        httpStatus: observation.observedHttpStatus,
        errorCode: observation.observedErrorCode,
        failClosed: true,
        mutationApplied: false,
      },
    );
    const beforeStateArtifact = jsonBinding(
      `${prefix}-before-state`,
      path.join(caseDirectory, "before-state.json"),
      stableState,
    );
    const afterStateArtifact = jsonBinding(
      `${prefix}-after-state`,
      path.join(caseDirectory, "after-state.json"),
      structuredClone(stableState),
    );
    return {
      ordinal: contract.ordinal,
      caseId: contract.caseId,
      skipped: false,
      httpStatus: observation.observedHttpStatus,
      errorCode: observation.observedErrorCode,
      mutationApplied: false,
      sealedCompletionDelta: 0,
      activeBindingChanged: false,
      requestArtifact,
      responseArtifact,
      beforeStateArtifact,
      afterStateArtifact,
    };
  });

  const envelope = {
    schemaVersion: "authored-editable-hybrid-gate0e-observations/v1",
    authorityDomain: "structural_test",
    attempt: structuredClone(probe.attempt),
    cases,
  };
  const envelopePath = path.join(resolvedOutput, "gate0e-observations.json");
  writeFreshJson(envelopePath, envelope);
  const collection = {
    probeUrl: probeUrl || null,
    probeResponsePath: path.resolve(probeResponsePath),
    observationsPath: path.resolve(envelopePath),
    caseCount: cases.length,
    artifactCount: cases.length * 4,
    authorityDomain: envelope.authorityDomain,
    promotionReady: false,
    deploymentAuthorized: false,
  };
  writeFreshJson(path.join(resolvedOutput, "gate0e-collection-summary.json"), collection);
  return collection;
}

export async function collectGate0ELive(options) {
  if (!options.probeUrl || !options.manifestPath || !options.outputDir) {
    throw new Error("gate0e-collector-required-arguments-missing");
  }
  const response = await fetch(options.probeUrl, { headers: { accept: "application/json" } });
  if (!response.ok) throw new Error(`gate0e-collector-http:${response.status}`);
  const probe = await response.json();
  const manifest = readJson(path.resolve(options.manifestPath));
  return materializeGate0EProbe({
    probe,
    manifest,
    outputDir: options.outputDir,
    probeUrl: options.probeUrl,
  });
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = await collectGate0ELive(parsePairs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
