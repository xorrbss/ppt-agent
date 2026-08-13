"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const SCHEMA = "presenton.authored-renderer-policy-provenance/v1";
const MANIFEST_NAME = ".authored-renderer-policy-provenance.json";
const PATHS = Object.freeze({
  source: "app/utils/authored-renderer-policy.ts",
  compiled: "app_dist/utils/authored-renderer-policy.js",
  tsconfig: "tsconfig.json",
});

function sha256(filePath) {
  return crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex");
}

function requiredFile(electronRoot, relative) {
  const filePath = path.join(electronRoot, ...relative.split("/"));
  if (!fs.statSync(filePath, { throwIfNoEntry: false })?.isFile()) {
    throw new Error(`Authored renderer policy provenance input is missing: ${relative}`);
  }
  return filePath;
}

function currentPolicyProvenance(electronRoot) {
  const values = {};
  for (const [name, relative] of Object.entries(PATHS)) {
    values[name] = { path: relative, sha256: sha256(requiredFile(electronRoot, relative)) };
  }
  return { schema: SCHEMA, generatedBy: "npm run build:ts", bindings: values };
}

function manifestPath(electronRoot) {
  return path.join(electronRoot, "app_dist", MANIFEST_NAME);
}

function writePolicyProvenance(electronRoot) {
  const manifest = currentPolicyProvenance(electronRoot);
  fs.writeFileSync(manifestPath(electronRoot), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  return manifest;
}

function readPolicyProvenance(electronRoot) {
  try {
    return JSON.parse(fs.readFileSync(manifestPath(electronRoot), "utf8"));
  } catch (error) {
    throw new Error(`Authored renderer policy provenance is missing or invalid: ${error.message}`);
  }
}

function validateIdentity(manifest) {
  if (manifest.schema !== SCHEMA || manifest.generatedBy !== "npm run build:ts") {
    throw new Error("Authored renderer policy provenance identity is invalid");
  }
  for (const [name, relative] of Object.entries(PATHS)) {
    if (manifest.bindings?.[name]?.path !== relative ||
        !/^[0-9a-f]{64}$/u.test(manifest.bindings?.[name]?.sha256 || "")) {
      throw new Error(`Authored renderer policy provenance ${name} binding is invalid`);
    }
  }
}

function verifyPolicyProvenance(electronRoot, { packaged = false } = {}) {
  const manifest = readPolicyProvenance(electronRoot);
  validateIdentity(manifest);
  const names = packaged ? ["compiled"] : Object.keys(PATHS);
  for (const name of names) {
    const actual = sha256(requiredFile(electronRoot, PATHS[name]));
    if (actual !== manifest.bindings[name].sha256) {
      throw new Error(`Authored renderer policy provenance is stale: ${name} changed after build:ts`);
    }
  }
  return manifest;
}

module.exports = {
  MANIFEST_NAME,
  PATHS,
  SCHEMA,
  currentPolicyProvenance,
  manifestPath,
  verifyPolicyProvenance,
  writePolicyProvenance,
};
