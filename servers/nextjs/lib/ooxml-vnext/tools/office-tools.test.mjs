import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { writePptxArchive } from "../../authored-hybrid/pptx-archive.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const matrixScript = path.join(here, "office-compatibility-matrix.ps1");
const visibleWitnessScript = path.join(here, "powerpoint-visible-witness.ps1");

function runMatrix(args = []) {
  const result = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", matrixScript, ...args],
    { encoding: "utf8", windowsHide: true },
  );
  return result;
}

test("availability matrix keeps validation lanes and claims separate", (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows Office availability probe");
    return;
  }
  const result = runMatrix();
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  assert.equal(report.schemaVersion, "ooxml-vnext/office-compatibility-matrix/v1");
  assert.deepEqual(Object.keys(report.matrix), [
    "powerPointDesktop",
    "openXmlSdk",
    "libreOffice",
    "visibleRepairDialogWitness",
  ]);
  assert.equal(report.matrix.powerPointDesktop.automationStatus, "not-run");
  assert.equal(report.matrix.powerPointDesktop.repairDialog.zeroClaimSupported, false);
  assert.equal(report.claims.powerPointReadBack, "not-established");
  assert.equal(report.claims.powerPointRepairDialogZero, "not-established");
  assert.equal(report.safety.powerPointAutomationCalls, 0);
  assert.equal(report.safety.existingPowerPointProcessAttachOrTermination, false);
});

test("Open XML SDK validator is exact-locked and opens presentations read-only", () => {
  const project = readFileSync(
    path.join(here, "openxml-validator", "OpenXmlValidator.csproj"),
    "utf8",
  );
  const lock = JSON.parse(
    readFileSync(path.join(here, "openxml-validator", "packages.lock.json"), "utf8"),
  );
  const source = readFileSync(
    path.join(here, "openxml-validator", "Program.cs"),
    "utf8",
  );
  assert.match(project, /Version="\[3\.5\.1\]"/);
  assert.equal(lock.dependencies["net9.0"]["DocumentFormat.OpenXml"].resolved, "3.5.1");
  assert.match(source, /PresentationDocument\.Open\(inputPath, false\)/);
  assert.match(source, /FileFormatVersions\.Microsoft365/);
});

test("manual repair-dialog witness creates but never launches a disposable copy", async (t) => {
  if (process.platform !== "win32") {
    t.skip("Windows manual witness helper");
    return;
  }
  const temporaryDirectory = await mkdtemp(path.join(os.tmpdir(), "ooxml-office-tools-test-"));
  t.after(() => rm(temporaryDirectory, { recursive: true, force: true }));
  const input = path.join(temporaryDirectory, "source.pptx");
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from("<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"/>")],
    ["ppt/presentation.xml", Buffer.from("<p:presentation xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\"/>")],
  ]);
  await writeFile(input, writePptxArchive(entries));
  const result = runMatrix([
    "-InputPath", input,
    "-CreateManualWitness",
    "-ManualWitnessRoot", temporaryDirectory,
  ]);
  assert.equal(result.status, 0, result.stderr);
  const report = JSON.parse(result.stdout);
  const witness = report.matrix.visibleRepairDialogWitness;
  assert.equal(witness.status, "awaiting-human-witness");
  assert.equal(witness.launchedPowerPoint, false);
  assert.equal(witness.repairDialogZeroClaimSupported, false);
  const template = JSON.parse(await readFile(witness.template, "utf8"));
  assert.equal(template.status, "awaiting-human-witness");
  assert.equal(template.repairDialogObserved, null);
  assert.equal(template.saved, false);
  assert.notEqual(witness.disposableCopy, input);
});

test("matrix implementation contains no PowerPoint COM or termination primitive", () => {
  const source = readFileSync(matrixScript, "utf8");
  assert.doesNotMatch(source, /-ComObject\s+PowerPoint/i);
  assert.doesNotMatch(source, /Presentations\.Open/i);
  assert.doesNotMatch(source, /Stop-Process[^\r\n]*POWERPNT/i);
  assert.doesNotMatch(source, /taskkill[^\r\n]*POWERPNT/i);
});

test("visible witness uses a disposable /x process and forbids attach, save, or termination", () => {
  const source = readFileSync(visibleWitnessScript, "utf8");
  assert.match(source, /Start-Process/);
  assert.match(source, /"\/x"/);
  assert.match(source, /blocked-existing-powerpoint-process/);
  assert.doesNotMatch(source, /-ComObject\s+PowerPoint/i);
  assert.doesNotMatch(source, /GetActiveObject/i);
  assert.doesNotMatch(source, /Presentations\.Open/i);
  assert.doesNotMatch(source, /Stop-Process/i);
  assert.doesNotMatch(source, /taskkill/i);
  assert.doesNotMatch(source, /\.Kill\(/i);
  assert.doesNotMatch(source, /\.Save(?:As|CopyAs)?\(/i);
});

test("interactive witness requires the complete PowerPoint product observation set", () => {
  const source = readFileSync(visibleWitnessScript, "utf8");
  for (const field of [
    "targetedEditCreated",
    "companyMasterConnected",
    "layoutVariantsConnected",
    "logoConnected",
    "placeholderMappingValid",
    "pageNumberFieldValid",
    "fontManifestAndComplexTextValid",
    "reopenTargetedEditPersisted",
  ]) {
    assert.match(source, new RegExp(`\\b${field}\\b`));
  }
  assert.match(source, /failed-required-product-observation/);
  assert.match(source, /failed-targeted-edit-not-persisted/);
});
