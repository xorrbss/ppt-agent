import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const policyPath = path.join(repositoryRoot, "compliance", "release-license-policy.json");
export const licensePolicy = JSON.parse(fs.readFileSync(policyPath, "utf8"));
const policyDocuments = new Map();

const PYTHON_METADATA_SCRIPT = String.raw`
import hashlib, json
from importlib import metadata

items = []
for dist in metadata.distributions():
    name = dist.metadata.get("Name")
    if not name:
        continue
    files = []
    for entry in dist.files or []:
        relative = str(entry).replace("\\", "/")
        lowered = relative.lower()
        if not any(token in lowered for token in ("license", "licence", "copying")):
            continue
        target = dist.locate_file(entry)
        try:
            if target.is_file():
                files.append({"path": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        except OSError:
            pass
    items.append({
        "name": name,
        "version": dist.version,
        "licenseExpression": (dist.metadata.get("License-Expression") or "").strip(),
        "license": (dist.metadata.get("License") or "").strip(),
        "classifiers": [value for value in dist.metadata.get_all("Classifier", []) if value.startswith("License ::")],
        "licenseFiles": sorted(files, key=lambda value: value["path"]),
    })
print(json.dumps(items, sort_keys=True))
`;

function normalizedName(value) {
  return value.toLowerCase().replace(/[-_.]+/gu, "-");
}

function expressionIds(expression) {
  return [...expression.matchAll(/[A-Za-z0-9][A-Za-z0-9.+-]*/gu)]
    .map((match) => match[0])
    .filter((value) => value !== "AND" && value !== "OR" && value !== "WITH");
}

function assertPolicyDocuments(root = repositoryRoot) {
  const cached = policyDocuments.get(root);
  if (cached) return cached;
  assert.equal(licensePolicy.schema, "presenton.release-license-policy/v1");
  const notice = fs.readFileSync(path.join(root, licensePolicy.notice), "utf8");
  const offer = fs.readFileSync(
    path.join(root, licensePolicy.correspondingSourceOffer),
    "utf8",
  );
  assert.match(notice, /REVIEWED COPYLEFT CORRESPONDING SOURCE OFFER/u);
  for (const rule of licensePolicy.conditionalCopyleftRules) {
    assert.ok(offer.includes(rule.offerMarker), `source offer is missing ${rule.offerMarker}`);
  }
  const documents = { notice, offer };
  policyDocuments.set(root, documents);
  return documents;
}

export function loadPythonPackageMetadata() {
  const result = spawnSync("uv", ["run", "--frozen", "python", "-c", PYTHON_METADATA_SCRIPT], {
    cwd: path.join(repositoryRoot, "servers", "fastapi"),
    encoding: "utf8",
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    throw result.error || new Error(`Python license metadata failed (${result.status}): ${result.stderr}`);
  }
  return JSON.parse(result.stdout);
}

function exactResolution(name, version, metadata) {
  const resolution = licensePolicy.exactMetadataResolutions.find(
    (item) => normalizedName(item.name) === normalizedName(name) && item.version === version,
  );
  if (!resolution) return null;
  assert.ok(
    metadata.licenseFiles.some((item) => item.sha256 === resolution.licenseFileSha256),
    `${name}@${version} reviewed license file changed`,
  );
  return resolution.licenseConcluded;
}

function metadataExpression(name, version, metadata) {
  const exact = exactResolution(name, version, metadata);
  if (exact) return exact;
  if (metadata.licenseExpression) return metadata.licenseExpression;
  const mapped = licensePolicy.metadataLicenseMappings[metadata.license];
  if (mapped) return mapped;
  if (/^[A-Za-z0-9().+ -]+$/u.test(metadata.license) && metadata.license.length < 120) {
    return metadata.license;
  }
  const classifiers = new Set();
  for (const classifier of metadata.classifiers) {
    if (/General Public License \(GPL\)/u.test(classifier)) classifiers.add("GPL-UNKNOWN");
    const value = licensePolicy.classifierMappings[classifier];
    if (value) classifiers.add(value);
  }
  if (classifiers.size > 0) return [...classifiers].sort().join(" AND ");
  throw new Error(`${name}@${version} has no reviewed license declaration in installed metadata`);
}

export function pythonLicenseIndex(metadataItems) {
  const index = new Map();
  for (const item of metadataItems) {
    index.set(`${normalizedName(item.name)}@${item.version}`, item);
  }
  return index;
}

export function resolvePythonLicense(name, version, index) {
  const metadata = index.get(`${normalizedName(name)}@${version}`);
  if (!metadata) {
    const platformResolution = licensePolicy.exactPlatformMetadataResolutions.find(
      (item) => normalizedName(item.name) === normalizedName(name) && item.version === version,
    );
    if (!platformResolution) {
      throw new Error(`${name}@${version} installed license metadata is missing`);
    }
    assert.equal(
      platformResolution.metadataSource,
      `https://pypi.org/pypi/${platformResolution.name}/${version}/json`,
      `${name}@${version} platform metadata source`,
    );
    return {
      expression: platformResolution.licenseConcluded,
      evidence: `reviewed platform-specific PyPI metadata ${platformResolution.metadataSource}`,
    };
  }
  return {
    expression: metadataExpression(name, version, metadata),
    evidence: exactResolution(name, version, metadata)
      ? "reviewed installed license file SHA-256"
      : "installed METADATA License-Expression/License/Classifier",
  };
}

export function resolveNpmMetadataLicense(name, version, packageDirectory) {
  const resolution = licensePolicy.exactNpmMetadataResolutions.find(
    (item) => item.name === name && item.version === version,
  );
  if (!resolution) return null;
  const candidates = ["LICENSE", "LICENSE.txt", "LICENCE", "COPYING", "license"];
  const matches = candidates
    .map((value) => path.join(packageDirectory, value))
    .filter((value) => fs.statSync(value, { throwIfNoEntry: false })?.isFile())
    .map((value) => sha256File(value));
  assert.ok(
    matches.includes(resolution.licenseFileSha256),
    `${name}@${version} reviewed npm license file changed`,
  );
  return resolution.licenseConcluded;
}

function matchingConditionalRule(packageValue, licenseId, documents) {
  return licensePolicy.conditionalCopyleftRules.find((rule) =>
    rule.licenseId === licenseId &&
    new RegExp(rule.packageNamePattern, "u").test(packageValue.name) &&
    rule.versions.includes(packageValue.versionInfo) &&
    documents.offer.includes(rule.offerMarker)
  );
}

export function concludePackageLicense(packageValue, root = repositoryRoot) {
  const documents = assertPolicyDocuments(root);
  const declared = packageValue.licenseDeclared;
  if (!declared || declared === "NOASSERTION") {
    throw new Error(`${packageValue.name}@${packageValue.versionInfo} has unknown production license`);
  }
  const conclusion = licensePolicy.preferredAlternatives[declared] || declared;
  const allowed = new Set(licensePolicy.allowedLicenseIds);
  for (const licenseId of expressionIds(conclusion)) {
    if (/^(?:AGPL|GPL)-/u.test(licenseId) || licenseId === "GPL-UNKNOWN") {
      throw new Error(`${packageValue.name}@${packageValue.versionInfo} has forbidden ${licenseId}`);
    }
    if (licenseId.startsWith("LGPL-") || licenseId === "MPL-2.0") {
      if (!matchingConditionalRule(packageValue, licenseId, documents)) {
        throw new Error(
          `${packageValue.name}@${packageValue.versionInfo} has unreviewed copyleft ${licenseId}`,
        );
      }
    } else if (!allowed.has(licenseId)) {
      throw new Error(`${packageValue.name}@${packageValue.versionInfo} has unreviewed ${licenseId}`);
    }
  }
  return conclusion;
}

export function validateSpdxLicensePolicy(document, root = repositoryRoot) {
  assertPolicyDocuments(root);
  for (const packageValue of document.packages || []) {
    assert.notEqual(packageValue.licenseDeclared, "NOASSERTION", `${packageValue.name} declared license`);
    assert.equal(
      packageValue.licenseConcluded,
      concludePackageLicense(packageValue, root),
      `${packageValue.name}@${packageValue.versionInfo} concluded license`,
    );
  }
  return { packageCount: document.packages.length, concluded: document.packages.length };
}

export function sha256File(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}
