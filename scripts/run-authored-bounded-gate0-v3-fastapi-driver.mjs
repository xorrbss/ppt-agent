import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

const BASE_URL = (process.env.AUTHORED_GATE0_BASE_URL || "http://127.0.0.1:8010").replace(/\/$/, "");
const TOKEN = process.env.AUTHORED_GATE0_DRIVER_TOKEN || "";

function headers() {
  return { "content-type": "application/json", ...(TOKEN ? { authorization: `Bearer ${TOKEN}` } : {}) };
}

function assertLoopback(url) {
  const parsed = new URL(url);
  if (!["127.0.0.1", "localhost", "::1"].includes(parsed.hostname)) throw new Error("gate0-driver-non-loopback-base-url");
}

async function fetchEvidence(evidenceRef, caseId) {
  const evidenceUrl = new URL(evidenceRef, `${BASE_URL}/`);
  assertLoopback(evidenceUrl.href);
  if (evidenceUrl.origin !== new URL(BASE_URL).origin) throw new Error(`gate0-driver-cross-origin-evidence:${caseId}`);
  const response = await fetch(evidenceUrl, { headers: headers() });
  if (!response.ok) throw new Error(`gate0-driver-evidence-query-failed:${caseId}:${response.status}`);
  const body = await response.json();
  return { body, evidenceUrl: evidenceUrl.href };
}

function digestBytes(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}

export async function runGate0Case({ caseContract, manifest, caseRoot, runRoot, runId }) {
  assertLoopback(BASE_URL);
  const requestBody = {
    schemaVersion: "authored-gate0-structural-case-execution/v1",
    runId,
    manifestDigest: manifest.manifestDigest,
    caseId: caseContract.caseId,
    ordinal: caseContract.ordinal,
    mutation: caseContract.caseId === "g0.positive.structural-projection" ? null : {
      jsonPaths: caseContract.jsonPaths,
      operator: caseContract.operator,
      phase: caseContract.mutationPhase || "payload-before-issue",
    },
  };
  const requestPath = path.join(caseRoot, "http-request.json");
  fs.writeFileSync(requestPath, `${JSON.stringify(requestBody, null, 2)}\n`, { flag: "wx" });
  const executeUrl = `${BASE_URL}/api/v1/ppt/authored-emission/gate0-structural-v3/cases/${encodeURIComponent(caseContract.caseId)}`;
  const response = await fetch(executeUrl, { method: "POST", headers: headers(), body: JSON.stringify(requestBody) });
  const responseText = await response.text();
  let responseBody;
  try { responseBody = JSON.parse(responseText); } catch { throw new Error(`gate0-driver-non-json-response:${caseContract.caseId}:${response.status}`); }
  const responsePath = path.join(caseRoot, "http-response.json");
  fs.writeFileSync(responsePath, `${JSON.stringify({ status: response.status, headers: Object.fromEntries(response.headers), body: responseBody }, null, 2)}\n`, { flag: "wx" });
  const trace = responseBody.executionTrace;
  if (!trace || trace.schemaVersion !== "authored-gate0-server-execution-trace/v1" || trace.source !== "fastapi-server-issued") throw new Error(`gate0-driver-server-trace-missing:${caseContract.caseId}`);
  const tracePath = path.join(caseRoot, "server-execution-trace.json");
  fs.writeFileSync(tracePath, `${JSON.stringify(trace, null, 2)}\n`, { flag: "wx" });
  const serverEvidenceArtifacts = [];
  const serverEvidenceBySeam = new Map();
  for (const span of trace.spans || []) {
    const { body: evidence } = await fetchEvidence(span.evidenceRef, caseContract.caseId);
    const evidencePath = path.join(caseRoot, `server-evidence-${span.seam}.json`);
    fs.writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, { flag: "wx" });
    const binding = {
      artifactId: `gate0-${String(caseContract.ordinal).padStart(2, "0")}-server-evidence-${span.seam}`,
      evidenceRef: span.evidenceRef,
      path: path.relative(runRoot, evidencePath),
      mediaType: "application/json",
    };
    serverEvidenceArtifacts.push(binding);
    serverEvidenceBySeam.set(span.seam, evidence);
  }
  const artifactEvidence = serverEvidenceBySeam.get("artifactEndpoint");
  const artifactRef = artifactEvidence?.payload?.artifact;
  const contentBase64 = artifactEvidence?.payload?.contentBase64;
  if (!artifactRef || typeof contentBase64 !== "string" || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(contentBase64)) {
    throw new Error(`gate0-driver-subject-artifact-evidence-missing:${caseContract.caseId}`);
  }
  const subjectBytes = Buffer.from(contentBase64, "base64");
  if (digestBytes(subjectBytes) !== artifactRef.sha256 || subjectBytes.length !== artifactRef.byteLength || artifactRef.mediaType !== "application/json" || artifactRef.state !== "bound") {
    throw new Error(`gate0-driver-subject-artifact-binding-invalid:${caseContract.caseId}`);
  }
  if (trace.mutationEvidence && trace.mutationEvidence.subjectArtifactId !== artifactRef.artifactId) {
    throw new Error(`gate0-driver-mutation-subject-id-mismatch:${caseContract.caseId}`);
  }
  const mutatedSubjectPath = path.join(caseRoot, "mutated-subject.json");
  fs.writeFileSync(mutatedSubjectPath, subjectBytes, { flag: "wx" });

  const completionUrl = `${BASE_URL}/api/v1/ppt/authored-emission/gate0-structural-v3/runs/${encodeURIComponent(runId)}/cases/${encodeURIComponent(caseContract.caseId)}/sealed-completions`;
  const completionResponse = await fetch(completionUrl, { headers: headers() });
  if (!completionResponse.ok) throw new Error(`gate0-driver-completion-query-failed:${caseContract.caseId}:${completionResponse.status}`);
  const completionBody = await completionResponse.json();
  const completionPath = path.join(caseRoot, "sealed-completion-query.json");
  fs.writeFileSync(completionPath, `${JSON.stringify(completionBody, null, 2)}\n`, { flag: "wx" });

  const artifacts = [
    { artifactId: `gate0-${String(caseContract.ordinal).padStart(2, "0")}-http-request`, path: path.relative(runRoot, requestPath), mediaType: "application/json" },
    { artifactId: `gate0-${String(caseContract.ordinal).padStart(2, "0")}-http-response`, path: path.relative(runRoot, responsePath), mediaType: "application/json" },
    { artifactId: `gate0-${String(caseContract.ordinal).padStart(2, "0")}-server-trace`, path: path.relative(runRoot, tracePath), mediaType: "application/json" },
    { artifactId: `gate0-${String(caseContract.ordinal).padStart(2, "0")}-completion-query`, path: path.relative(runRoot, completionPath), mediaType: "application/json" },
    ...serverEvidenceArtifacts,
    { artifactId: artifactRef.artifactId, versionId: artifactRef.versionId, sha256: artifactRef.sha256, byteLength: artifactRef.byteLength, path: path.relative(runRoot, mutatedSubjectPath), mediaType: artifactRef.mediaType },
  ];
  return {
    driverId: "authored-gate0-fastapi-isolated-db/v1",
    caseId: caseContract.caseId,
    ordinal: caseContract.ordinal,
    authorityDomain: responseBody.authorityDomain,
    evidenceClass: responseBody.evidenceClass,
    productionAuthorityGranted: responseBody.productionAuthorityGranted,
    productionValidatorAccepted: responseBody.productionValidatorAccepted,
    httpStatus: response.status,
    errorCode: responseBody.errorCode || responseBody.detail?.code,
    sealedCompletionCount: completionBody.sealedCompletionCount,
    isolatedProjectionPromotionReady: responseBody.isolatedProjectionPromotionReady,
    releaseReportStatus: responseBody.releaseReportStatus,
    mutation: requestBody.mutation,
    serverTrace: trace,
    sourceTreeCensusDigest: responseBody.sourceTreeCensusDigest,
    artifacts,
  };
}
