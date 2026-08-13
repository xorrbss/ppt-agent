#!/usr/bin/env node

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  concludePackageLicense,
  loadPythonPackageMetadata,
  pythonLicenseIndex,
  resolveNpmMetadataLicense,
  resolvePythonLicense,
  sha256File,
  validateSpdxLicensePolicy,
} from "./release-license-policy.mjs";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const outputPath = path.join(repositoryRoot, "compliance", "release-sbom.spdx.json");
const checkOnly = process.argv.includes("--check");

function safeId(value) {
  return value.replace(/[^A-Za-z0-9.-]+/g, "-").replace(/^-+|-+$/g, "");
}

function integrityChecksum(integrity) {
  const match = /^sha512-(.+)$/.exec(integrity || "");
  if (!match) return [];
  return [
    {
      algorithm: "SHA512",
      checksumValue: Buffer.from(match[1], "base64").toString("hex"),
    },
  ];
}

function npmPackages(scope, lockPath) {
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  return Object.entries(lock.packages)
    .filter(([location, entry]) => location && entry.version && entry.dev !== true)
    .map(([location, entry]) => {
      const name = entry.name || location.replace(/^node_modules\//, "").replace(/\/node_modules\//g, ">>");
      const packageName = name.includes(">>") ? name.split(">>").at(-1) : name;
      let declared = entry.license;
      let evidence = `${scope}/package-lock.json`;
      if (!declared) {
        const packageDirectory = path.join(path.dirname(lockPath), location);
        const metadataPath = path.join(packageDirectory, "package.json");
        const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
        assert.equal(metadata.version, entry.version, `${packageName} installed metadata version`);
        declared = typeof metadata.license === "string"
          ? metadata.license
          : resolveNpmMetadataLicense(packageName, entry.version, packageDirectory);
        evidence = `${scope} installed package.json (lockfile license field absent)`;
      }
      const packageValue = {
        SPDXID: `SPDXRef-npm-${safeId(scope)}-${safeId(location)}`,
        name: packageName,
        versionInfo: entry.version,
        downloadLocation: entry.resolved || "NOASSERTION",
        filesAnalyzed: false,
        checksums: integrityChecksum(entry.integrity),
        licenseConcluded: "",
        licenseDeclared: declared || "NOASSERTION",
        copyrightText: "NOASSERTION",
        externalRefs: [
          {
            referenceCategory: "PACKAGE-MANAGER",
            referenceType: "purl",
            referenceLocator: `pkg:npm/${encodeURIComponent(packageName).replace("%40", "@")}` +
              `@${entry.version}`,
          },
        ],
        supplier: "NOASSERTION",
        primaryPackagePurpose: "LIBRARY",
        comment: `Production dependency from ${scope}/package-lock.json; license from ${evidence}`,
      };
      packageValue.licenseConcluded = concludePackageLicense(packageValue);
      return packageValue;
    });
}

function pythonPackages() {
  const metadata = pythonLicenseIndex(loadPythonPackageMetadata());
  const result = spawnSync(
    "uv",
    [
      "export",
      "--frozen",
      "--no-dev",
      "--no-emit-project",
      "--format",
      "cyclonedx1.5",
    ],
    {
      cwd: path.join(repositoryRoot, "servers", "fastapi"),
      encoding: "utf8",
      windowsHide: true,
    }
  );
  if (result.error || result.status !== 0) {
    throw result.error || new Error(`uv SBOM export failed (${result.status}): ${result.stderr}`);
  }
  const bom = JSON.parse(result.stdout);
  return (bom.components || []).map((component) => {
    const resolved = resolvePythonLicense(component.name, component.version, metadata);
    const packageValue = {
      SPDXID: `SPDXRef-python-${safeId(component["bom-ref"] || `${component.name}-${component.version}`)}`,
      name: component.name,
      versionInfo: component.version,
      downloadLocation: "NOASSERTION",
      filesAnalyzed: false,
      checksums: [],
      licenseConcluded: "",
      licenseDeclared: resolved.expression,
      copyrightText: "NOASSERTION",
      externalRefs: component.purl
        ? [{ referenceCategory: "PACKAGE-MANAGER", referenceType: "purl", referenceLocator: component.purl }]
        : [],
      supplier: component.publisher ? `Organization: ${component.publisher}` : "NOASSERTION",
      primaryPackagePurpose: "LIBRARY",
      comment: `Production dependency from servers/fastapi/uv.lock via uv CycloneDX export; license from ${resolved.evidence}`,
    };
    packageValue.licenseConcluded = concludePackageLicense(packageValue);
    return packageValue;
  });
}

function createDocument() {
  const dependencies = [
    ...npmPackages("root", path.join(repositoryRoot, "package-lock.json")),
    ...npmPackages("nextjs", path.join(repositoryRoot, "servers", "nextjs", "package-lock.json")),
    ...npmPackages("electron", path.join(repositoryRoot, "electron", "package-lock.json")),
    ...pythonPackages(),
  ].sort((left, right) => left.SPDXID.localeCompare(right.SPDXID));
  const identity = crypto
    .createHash("sha256")
    .update(dependencies.map((item) => `${item.SPDXID}@${item.versionInfo}`).join("\n"))
    .digest("hex");
  const rootPackage = JSON.parse(fs.readFileSync(path.join(repositoryRoot, "package.json"), "utf8"));
  const product = {
    SPDXID: "SPDXRef-Package-presenton",
    name: rootPackage.name,
    versionInfo: rootPackage.version,
    downloadLocation: "NOASSERTION",
    filesAnalyzed: false,
    checksums: [],
    licenseConcluded: "Apache-2.0",
    licenseDeclared: "Apache-2.0",
    copyrightText: "NOASSERTION",
    externalRefs: [],
    supplier: "NOASSERTION",
    primaryPackagePurpose: "APPLICATION",
    comment: `Dependency identity SHA-256: ${identity}`,
  };
  const packages = [product, ...dependencies];
  const document = {
    spdxVersion: "SPDX-2.3",
    dataLicense: "CC0-1.0",
    SPDXID: "SPDXRef-DOCUMENT",
    name: "presenton-authored-vnext-release",
    documentNamespace: `https://github.com/xorrbss/ppt-agent/sbom/${identity}`,
    creationInfo: {
      created: "1970-01-01T00:00:00Z",
      creators: ["Tool: scripts/generate-release-sbom.mjs"],
      licenseListVersion: "3.27.0",
    },
    annotations: [
      {
        annotationDate: "1970-01-01T00:00:00Z",
        annotationType: "OTHER",
        annotator: "Tool: scripts/generate-release-sbom.mjs",
        comment: JSON.stringify({
          schema: "presenton.release-license-attestation/v1",
          policySha256: sha256File(path.join(repositoryRoot, "compliance", "release-license-policy.json")),
          noticeSha256: sha256File(path.join(repositoryRoot, "NOTICE")),
          sourceOfferSha256: sha256File(path.join(repositoryRoot, "compliance", "copyleft-source-offer.md")),
        }),
      },
    ],
    documentDescribes: [product.SPDXID],
    packages,
    relationships: [
      {
        spdxElementId: "SPDXRef-DOCUMENT",
        relationshipType: "DESCRIBES",
        relatedSpdxElement: product.SPDXID,
      },
      ...dependencies.map((item) => ({
        spdxElementId: product.SPDXID,
        relationshipType: "DEPENDS_ON",
        relatedSpdxElement: item.SPDXID,
      })),
    ],
  };
  validateSpdxLicensePolicy(document);
  return `${JSON.stringify(document, null, 2)}\n`;
}

const rendered = createDocument();
if (checkOnly) {
  assert.equal(
    fs.readFileSync(outputPath, "utf8"),
    rendered,
    "release SBOM is stale; run npm run generate:release-sbom"
  );
  console.log("Release SPDX SBOM is current");
} else {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, rendered, "utf8");
  console.log(`Wrote ${path.relative(repositoryRoot, outputPath)}`);
}
