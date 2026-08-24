import fs from "node:fs";
import path from "node:path";

import {
  assertManifestDigest,
  loadArtifact,
  parsePairs,
  readJson,
  sameStrings,
  writeFreshJson,
} from "./lib.mjs";

export function buildGate1ESourceMap(options) {
  const manifest = readJson(path.resolve(options.manifestPath));
  const attempt = readJson(path.resolve(options.attemptPath));
  const bindings = readJson(path.resolve(options.bindingsPath));
  assertManifestDigest(manifest, "gate1e");
  if (!bindings || Array.isArray(bindings) || typeof bindings !== "object") {
    throw new Error("gate1e-bindings-map-invalid");
  }
  if (!sameStrings(Object.keys(bindings), manifest.requiredArtifactIds)) {
    throw new Error("gate1e-bindings-id-set-mismatch");
  }
  const artifacts = manifest.requiredArtifactIds.map((artifactId) => {
    const binding = bindings[artifactId];
    if (typeof binding === "string") {
      const record = loadArtifact({ artifactId, path: binding }, "gate1e-source");
      return { artifactId, path: record.path, versionId: record.versionId, sha256: record.sha256, byteLength: record.byteLength, mediaType: record.mediaType };
    }
    const record = loadArtifact({ artifactId, ...binding }, "gate1e-source");
    return { artifactId, path: record.path, versionId: record.versionId, sha256: record.sha256, byteLength: record.byteLength, mediaType: record.mediaType };
  });
  const sourceMap = {
    schemaVersion: "authored-editable-hybrid-gate1e-artifact-source-map/v1",
    authorityDomain: "actual_product",
    attempt,
    artifacts,
  };
  const outputPath = path.resolve(options.outputPath);
  if (fs.existsSync(outputPath)) throw new Error("gate1e-source-map-output-must-be-fresh");
  writeFreshJson(outputPath, sourceMap);
  return sourceMap;
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = buildGate1ESourceMap(parsePairs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify({ artifactCount: result.artifacts.length, authorityDomain: result.authorityDomain })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
