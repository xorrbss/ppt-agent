import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { captureBoundedSourceCensus } from "../run-authored-bounded-gate0-v3.mjs";

function fail(code) {
  throw new Error(`bounded-v3-gate0-runtime:${code}`);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeFresh(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) fail(`invalid-argument:${key}`);
    options[key.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
  }
  for (const key of ["repoRoot", "outputDir"]) if (!options[key]) fail(`argument-missing:${key}`);
  return options;
}

export function prepareGate0Runtime(options) {
  const repoRoot = fs.realpathSync(path.resolve(options.repoRoot));
  const outputDir = path.resolve(options.outputDir);
  if (fs.existsSync(outputDir)) fail("output-must-be-fresh");
  const contracts = path.join(repoRoot, "docs", "authored-bounded-hybrid-release-v3-contracts");
  const manifest = readJson(path.join(contracts, "gate0-case-manifest.v1.json"));
  const policy = readJson(path.join(contracts, "evidence-policy.v2.json"));
  const census = captureBoundedSourceCensus(repoRoot);
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  const privateDer = privateKey.export({ type: "pkcs8", format: "der" });
  const rawPrivate = privateDer.subarray(privateDer.length - 32);
  if (rawPrivate.length !== 32) fail("private-key-raw-length");
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  const signerKeyId = `sha256:${crypto.createHash("sha256").update(publicDer).digest("hex")}`;
  const runtime = {
    schemaVersion: "authored-bounded-v3-gate0-runtime-private/v1",
    sourceHeadCommit: census.head,
    sourceTreeCensusDigest: census.censusDigest,
    manifestDigest: manifest.manifestDigest,
    evidencePolicyDigest: policy.policyDigest,
    signerKeyId,
    privateKeyRawBase64: rawPrivate.toString("base64"),
  };
  const trust = {
    schemaVersion: "authored-bounded-v3-gate0-structural-trust/v1",
    authorityDomain: "structural_test",
    evidenceClass: "synthetic_structural_only",
    keyPurpose: "gate0-server-execution-trace",
    signerKeyId,
    publicKeySpkiBase64: publicDer.toString("base64"),
    revoked: false,
    promotionAuthorized: false,
    deploymentAuthorized: false,
    sourceHeadCommit: census.head,
    sourceTreeCensusDigest: census.censusDigest,
  };
  fs.mkdirSync(outputDir, { recursive: true });
  writeFresh(path.join(outputDir, "source-tree-census.json"), census);
  writeFresh(path.join(outputDir, "gate0-runtime-private.json"), runtime);
  writeFresh(path.join(outputDir, "gate0-structural-trust.json"), trust);
  return { outputDir, runtimePath: path.join(outputDir, "gate0-runtime-private.json"), trustPath: path.join(outputDir, "gate0-structural-trust.json"), runtime, trust };
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = prepareGate0Runtime(parseArgs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify({ outputDir: result.outputDir, runtimePath: result.runtimePath, trustPath: result.trustPath, sourceHeadCommit: result.runtime.sourceHeadCommit, sourceTreeCensusDigest: result.runtime.sourceTreeCensusDigest, manifestDigest: result.runtime.manifestDigest, evidencePolicyDigest: result.runtime.evidencePolicyDigest, signerKeyId: result.runtime.signerKeyId })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
