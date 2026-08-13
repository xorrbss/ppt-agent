const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function sha256(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function requireRegularFile(filePath, label, minimumBytes = 1) {
  let stat;
  try {
    stat = fs.statSync(filePath);
  } catch {
    throw new Error(`${label} is missing: ${filePath}`);
  }
  if (!stat.isFile() || stat.size < minimumBytes) {
    throw new Error(`${label} is not a populated regular file: ${filePath}`);
  }
  return filePath;
}

function validateSpdxDocument(filePath) {
  let document;
  try {
    document = JSON.parse(fs.readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new Error(`Release SBOM is invalid JSON: ${error.message}`);
  }
  if (document.spdxVersion !== "SPDX-2.3" || document.dataLicense !== "CC0-1.0") {
    throw new Error("Release SBOM must be an SPDX 2.3 document under CC0-1.0");
  }
  if (!Array.isArray(document.packages) || document.packages.length === 0) {
    throw new Error("Release SBOM contains no packages");
  }
  const identifiers = new Set(document.packages.map((item) => item.SPDXID));
  if (identifiers.size !== document.packages.length || identifiers.has(undefined)) {
    throw new Error("Release SBOM package identifiers are missing or duplicated");
  }
  for (const packageValue of document.packages) {
    if (!packageValue.licenseDeclared || packageValue.licenseDeclared === "NOASSERTION" ||
        !packageValue.licenseConcluded || packageValue.licenseConcluded === "NOASSERTION") {
      throw new Error(`Release SBOM has an unknown license: ${packageValue.name || packageValue.SPDXID}`);
    }
    if (/(?:^|[^L])(?:AGPL|GPL)-/u.test(packageValue.licenseConcluded)) {
      throw new Error(`Release SBOM has a forbidden license: ${packageValue.name || packageValue.SPDXID}`);
    }
  }
  const workspaceRoot = path.dirname(path.dirname(filePath));
  const policyPath = requireRegularFile(
    path.join(workspaceRoot, "compliance", "release-license-policy.json"),
    "Release license policy",
  );
  const sourceOfferPath = requireRegularFile(
    path.join(workspaceRoot, "compliance", "copyleft-source-offer.md"),
    "Copyleft corresponding source offer",
  );
  const annotation = document.annotations?.find(
    (item) => item.annotator === "Tool: scripts/generate-release-sbom.mjs",
  );
  let attestation;
  try {
    attestation = JSON.parse(annotation?.comment || "");
  } catch {
    throw new Error("Release SBOM license attestation is missing or invalid");
  }
  const expected = {
    schema: "presenton.release-license-attestation/v1",
    policySha256: sha256(policyPath),
    noticeSha256: sha256(path.join(workspaceRoot, "NOTICE")),
    sourceOfferSha256: sha256(sourceOfferPath),
  };
  if (JSON.stringify(attestation) !== JSON.stringify(expected)) {
    throw new Error("Release SBOM license attestation is stale");
  }
  return { packageCount: document.packages.length, concludedLicenses: document.packages.length };
}

function validateReleaseCompliance(workspaceRoot) {
  const required = [
    ["LICENSE", "Product license", 1000],
    ["NOTICE", "Third-party notice", 1000],
    ["compliance/fonts/OFL-1.1.txt", "Bundled font OFL text", 1000],
    ["compliance/fonts/README.md", "Bundled font inventory", 100],
    ["compliance/release-license-policy.json", "Release license policy", 100],
    ["compliance/copyleft-source-offer.md", "Copyleft corresponding source offer", 100],
    [
      "docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/LICENSE.genoffice.txt",
      "GenOffice Apache license",
      1000,
    ],
    [
      "docs/experiments/authored-vnext/03-ooxml-roundtrip/compliance/NOTICE.genoffice.txt",
      "GenOffice notice",
      100,
    ],
  ];
  for (const [relative, label, minimumBytes] of required) {
    requireRegularFile(path.join(workspaceRoot, relative), label, minimumBytes);
  }
  const sbomPath = requireRegularFile(
    path.join(workspaceRoot, "compliance", "release-sbom.spdx.json"),
    "Release SBOM",
    1000
  );
  return validateSpdxDocument(sbomPath);
}

module.exports = { validateReleaseCompliance, validateSpdxDocument };
