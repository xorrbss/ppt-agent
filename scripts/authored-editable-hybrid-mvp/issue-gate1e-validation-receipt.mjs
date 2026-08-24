import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  digestExcluding,
  ensureFreshDirectory,
  parsePairs,
  readJson,
  sha256Bytes,
  writeFreshJson,
} from "./lib.mjs";

function initialize(options) {
  const outputDir = path.resolve(options.outputDir);
  ensureFreshDirectory(outputDir, "gate1e-verifier-authority-output");
  const authorityScope = options.authorityScope || "local-shadow";
  if (!["local-shadow", "external-independent"].includes(authorityScope)) throw new Error("gate1e-verifier-authority-scope-invalid");
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const publicDer = publicKey.export({ type: "spki", format: "der" });
  const privateDer = privateKey.export({ type: "pkcs8", format: "der" });
  const signerKeyId = sha256Bytes(publicDer);
  const now = Date.now();
  const trust = {
    schemaVersion: "authored-editable-hybrid-verifier-trust/v1",
    issuerId: options.issuerId || `actual-product-editable-hybrid-validator/${authorityScope}`,
    signerKeyId,
    keyPurpose: "editable-hybrid-mvp-gate1e",
    algorithm: "Ed25519",
    authorityScope,
    promotionAuthorized: false,
    deploymentAuthorized: false,
    revoked: false,
    notBefore: new Date(now - 60_000).toISOString(),
    expiresAt: new Date(now + 24 * 60 * 60 * 1000).toISOString(),
    publicKeySpkiBase64: publicDer.toString("base64"),
  };
  const trustPath = path.join(outputDir, "verifier-trust.json");
  const privateKeyPath = path.join(outputDir, "verifier-private-key.pkcs8.base64");
  writeFreshJson(trustPath, trust);
  fs.writeFileSync(privateKeyPath, `${privateDer.toString("base64")}\n`, { flag: "wx", mode: 0o600 });
  return {
    trustPath,
    trustSha256: sha256Bytes(fs.readFileSync(trustPath)),
    signerKeyId,
    privateKeyPath,
  };
}

function issue(options) {
  const request = readJson(path.resolve(options.validationRequestPath));
  const trustPath = path.resolve(options.trustPath);
  const trust = readJson(trustPath);
  const privateKey = crypto.createPrivateKey({
    key: Buffer.from(fs.readFileSync(path.resolve(options.privateKeyPath), "utf8").trim(), "base64"),
    type: "pkcs8",
    format: "der",
  });
  const now = Date.now();
  const receipt = {
    schemaVersion: "authored-editable-hybrid-validation-receipt/v1",
    issuerId: trust.issuerId,
    signerKeyId: trust.signerKeyId,
    authorityScope: trust.authorityScope,
    promotionAuthorized: false,
    deploymentAuthorized: false,
    nonce: crypto.randomBytes(24).toString("base64url"),
    issuedAt: new Date(now).toISOString(),
    expiresAt: new Date(now + 20 * 60 * 1000).toISOString(),
    subjectManifestDigest: request.subjectManifestDigest,
    subjectProfileDigest: request.subjectProfileDigest,
    subjectResultCoreDigest: request.subjectResultCoreDigest,
    subjectArtifactInventoryDigest: request.subjectArtifactInventoryDigest,
    presentationId: request.presentationId,
    presentationRevision: request.presentationRevision,
    generationAttemptId: request.generationAttemptId,
    hybridExportAttemptId: request.hybridExportAttemptId,
    receiptDigest: "",
    signatureBase64: "",
  };
  receipt.receiptDigest = digestExcluding(receipt, ["receiptDigest", "signatureBase64"]);
  receipt.signatureBase64 = crypto.sign(null, Buffer.from(receipt.receiptDigest, "ascii"), privateKey).toString("base64");
  const receiptPath = path.resolve(options.receiptPath);
  writeFreshJson(receiptPath, receipt);
  return { receiptPath, receiptSha256: sha256Bytes(fs.readFileSync(receiptPath)), receiptDigest: receipt.receiptDigest };
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const command = process.argv[2];
    const options = parsePairs(process.argv.slice(3));
    const result = command === "init" ? initialize(options) : command === "issue" ? issue(options) : (() => { throw new Error("usage: issue-gate1e-validation-receipt.mjs init|issue --..."); })();
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
