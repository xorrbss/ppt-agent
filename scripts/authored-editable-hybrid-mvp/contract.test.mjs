import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import test from "node:test";

import { materializeGate0EProbe } from "./collect-gate0e-live.mjs";
import { executeGateMinus1E } from "./run-gate-minus1e.mjs";
import { executeGate0E } from "./run-gate0e.mjs";
import { assertManifestDigest, readJson } from "./lib.mjs";

const scriptDirectory = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
const repoRoot = path.resolve(scriptDirectory, "../..");
const contracts = path.join(repoRoot, "docs", "authored-editable-hybrid-mvp-v1-contracts");

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
  return filePath;
}

test("five-slide editable hybrid contracts execute and fail closed", () => {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "editable-hybrid-mvp-contract-"));
  try {
    const profile = readJson(path.join(contracts, "capability-profile.v1.json"));
    const minusManifestPath = path.join(contracts, "gate-minus1e-case-manifest.v1.json");
    const zeroManifestPath = path.join(contracts, "gate0e-case-manifest.v1.json");
    const oneManifestPath = path.join(contracts, "gate1e-artifact-manifest.v1.json");
    const minusManifest = readJson(minusManifestPath);
    const zeroManifest = readJson(zeroManifestPath);
    const oneManifest = readJson(oneManifestPath);
    for (const [label, manifest] of [["profile", profile], ["minus", minusManifest], ["zero", zeroManifest], ["one", oneManifest]]) assertManifestDigest(manifest, label);
    assert.equal(profile.expectedSlideCount, 5);
    assert.equal(minusManifest.cases.length, 15);
    assert.equal(zeroManifest.cases.length, 15);
    assert.equal(oneManifest.requiredArtifactIds.length, 35);
    assert.equal(new Set(oneManifest.requiredArtifactIds).size, 35);

    const liveProbe = {
      schemaVersion: "authored-editable-hybrid-gate0e-observations/v1",
      authorityDomain: "structural_test",
      profileId: zeroManifest.profileId,
      manifestDigest: zeroManifest.manifestDigest,
      attempt: {
        presentationId: "presentation-live-collector-test",
        presentationRevision: 1,
        generationAttemptId: "generation-live-collector-test",
        hybridExportAttemptId: "hybrid-live-collector-test",
        sceneDigest: `sha256:${"a".repeat(64)}`,
        activeManifestDigest: `sha256:${"b".repeat(64)}`,
        gateMinus1eResultSetDigest: `sha256:${"c".repeat(64)}`,
        gate0eResultSetDigest: `sha256:${"d".repeat(64)}`,
        sourceHeadCommit: "0".repeat(40),
        expectedQids: ["qid-1"],
      },
      baseline: { deckRevision: 1, completionCount: 0, completionCasVersion: 0 },
      cases: zeroManifest.cases.map((entry) => ({
        ...entry,
        mutationPayload: { caseId: entry.caseId },
        observedHttpStatus: entry.expectedHttpStatus,
        observedErrorCode: entry.expectedErrorCode,
        failClosed: true,
      })),
      allCasesPassed: true,
      stateUnchanged: true,
      promotionReady: false,
      deploymentAuthorized: false,
    };
    const collected = materializeGate0EProbe({ probe: liveProbe, manifest: zeroManifest, outputDir: path.join(temporaryRoot, "collected-zero"), probeUrl: "http://127.0.0.1/example" });
    assert.equal(collected.caseCount, 15);
    assert.equal(collected.artifactCount, 60);
    const collectedResult = executeGate0E({ manifestPath: zeroManifestPath, observationsPath: collected.observationsPath, outputDir: path.join(temporaryRoot, "collected-zero-result") });
    assert.equal(collectedResult.executedCount, 15);
    assert.equal(collectedResult.artifactInventory.length, 60);

    const sharedArtifact = writeJson(path.join(temporaryRoot, "minus", "probe.json"), { actualProductProbe: true });
    const attempt = {
      presentationId: "presentation-contract-test",
      presentationRevision: 1,
      generationAttemptId: "generation-contract-test",
      hybridExportAttemptId: "hybrid-contract-test",
      sceneDigest: `sha256:${"1".repeat(64)}`,
      activeManifestDigest: `sha256:${"2".repeat(64)}`,
      expectedQids: ["qid-1", "qid-2", "qid-3", "qid-4", "qid-5"],
    };
    const minusEnvelope = {
      schemaVersion: "authored-editable-hybrid-gate-minus1e-observations/v1",
      authorityDomain: "actual_product_capability_probe",
      attempt,
      artifacts: [{ artifactId: "actual-probe", path: sharedArtifact, mediaType: "application/json" }],
      cases: minusManifest.cases.map((entry) => ({ ordinal: entry.ordinal, caseId: entry.caseId, state: "satisfied", skipped: false, fields: structuredClone(entry.expected), evidenceArtifactIds: ["actual-probe"] })),
    };
    const minusEnvelopePath = writeJson(path.join(temporaryRoot, "minus-envelope.json"), minusEnvelope);
    const minusResult = executeGateMinus1E({ manifestPath: minusManifestPath, observationsPath: minusEnvelopePath, outputDir: path.join(temporaryRoot, "minus-result") });
    assert.equal(minusResult.executedCount, 15);
    assert.equal(minusResult.skippedCount, 0);
    const rejectedMinus = structuredClone(minusEnvelope);
    rejectedMinus.cases[14].fields.unrelatedBaselinePreserved = false;
    const rejectedMinusPath = writeJson(path.join(temporaryRoot, "minus-rejected.json"), rejectedMinus);
    assert.throws(() => executeGateMinus1E({ manifestPath: minusManifestPath, observationsPath: rejectedMinusPath, outputDir: path.join(temporaryRoot, "minus-rejected-output") }), /gate-minus1e-field-mismatch/);

    const zeroEnvelope = {
      schemaVersion: "authored-editable-hybrid-gate0e-observations/v1",
      authorityDomain: "structural_test",
      attempt,
      cases: zeroManifest.cases.map((entry) => {
        const caseDirectory = path.join(temporaryRoot, "zero", String(entry.ordinal));
        const stableState = { activeManifestDigest: attempt.activeManifestDigest, sealedCompletion: 0, revision: 1 };
        const artifacts = {
          requestArtifact: writeJson(path.join(caseDirectory, "request.json"), { caseId: entry.caseId, mutation: "attempted" }),
          responseArtifact: writeJson(path.join(caseDirectory, "response.json"), { httpStatus: entry.expectedHttpStatus, errorCode: entry.expectedErrorCode }),
          beforeStateArtifact: writeJson(path.join(caseDirectory, "before.json"), stableState),
          afterStateArtifact: writeJson(path.join(caseDirectory, "after.json"), stableState),
        };
        return {
          ordinal: entry.ordinal,
          caseId: entry.caseId,
          skipped: false,
          httpStatus: entry.expectedHttpStatus,
          errorCode: entry.expectedErrorCode,
          mutationApplied: false,
          sealedCompletionDelta: 0,
          activeBindingChanged: false,
          ...Object.fromEntries(Object.entries(artifacts).map(([key, filePath]) => [key, { artifactId: `g0e-${entry.ordinal}-${key}`, path: filePath, mediaType: "application/json" }])),
        };
      }),
    };
    const zeroEnvelopePath = writeJson(path.join(temporaryRoot, "zero-envelope.json"), zeroEnvelope);
    const zeroResult = executeGate0E({ manifestPath: zeroManifestPath, observationsPath: zeroEnvelopePath, outputDir: path.join(temporaryRoot, "zero-result") });
    assert.equal(zeroResult.executedCount, 15);
    assert.equal(zeroResult.artifactInventory.length, 60);
    writeJson(zeroEnvelope.cases[0].afterStateArtifact.path, { activeManifestDigest: attempt.activeManifestDigest, sealedCompletion: 1, revision: 2 });
    assert.throws(() => executeGate0E({ manifestPath: zeroManifestPath, observationsPath: zeroEnvelopePath, outputDir: path.join(temporaryRoot, "zero-rejected-output") }), /gate0e-state-mutated/);

    const wrapperPath = path.join(scriptDirectory, "run-powerpoint-owned-delta.ps1");
    const parserCommand = `$errors = $null; [System.Management.Automation.Language.Parser]::ParseFile('${wrapperPath.replaceAll("'", "''")}', [ref]$null, [ref]$errors) | Out-Null; if ($errors.Count -ne 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }`;
    execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", parserCommand], { stdio: "pipe" });
    const wrapper = fs.readFileSync(wrapperPath, "utf8");
    for (const sentinel of ['-ArgumentList @("/x",', "baselineBindingAttempted = $false", "ownedApplicationBindingExact", "baselinePresentationWindowsPreserved", "adoptedProcessIds = @()"] ) assert.match(wrapper, new RegExp(sentinel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
});
