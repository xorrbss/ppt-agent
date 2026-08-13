#!/usr/bin/env node
/** Static verification used when Docker is unavailable; it is not GA evidence. */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const experiment = path.resolve(root, "..");
const dockerfile = fs.readFileSync(path.join(root, "Dockerfile"), "utf8");
const runner = fs.readFileSync(path.join(root, "run-harness.ps1"), "utf8");
const runtime = fs.readFileSync(path.join(root, "runtime.mjs"), "utf8");
const manifest = JSON.parse(fs.readFileSync(path.join(experiment, "corpus", "manifest.json"), "utf8"));
const checks = [];
function check(id, condition) {
  checks.push({ id, pass: Boolean(condition) });
  assert.ok(condition, id);
}

check("image-non-root-user", /USER 65532:65532/.test(dockerfile));
check("runtime-network-none", /--network none/.test(runner));
check("read-only-root", /--read-only/.test(runner));
check("isolated-tmpfs", /--tmpfs \/tmp:rw,noexec,nosuid,nodev,size=96m/.test(runner));
check("stdout-evidence-no-host-mount", /\$RuntimeObservation = docker start --attach/.test(runner));
check("all-capabilities-dropped", /--cap-drop ALL/.test(runner));
check("no-new-privileges", /--security-opt no-new-privileges/.test(runner));
check("pid-memory-cpu-limits", /--pids-limit 64/.test(runner) && /--memory 512m/.test(runner) && /--cpus 1/.test(runner));
check("private-ipc", /--ipc private/.test(runner));
check("no-host-mount", !/(?:--mount|--volume|\s-v\s)/i.test(runner));
check("no-privileged-container", !/--privileged/.test(runner));
check("no-machine-wide-firewall", !/\b(?:netsh|iptables|nft|ufw|firewall-cmd)\b/i.test(`${runner}\n${runtime}`));
check("data-document-only", /data:text\/html/.test(runtime) && !/pathToFileURL\([^)]*\.html/.test(runtime));
check("restrictive-csp", /default-src 'none'/.test(runtime) && /connect-src 'none'/.test(runtime) && /object-src 'none'/.test(runtime));
check("browser-unsafe-options-rejected", /unsafe-browser-option/.test(runtime) && /allow-file-access/.test(runtime));
check("minimal-browser-environment", /env:\s*\{[\s\S]*HOME:[\s\S]*PATH:/m.test(runtime) && !/\.\.\.process\.env/.test(runtime));
check("all-corpus-inert", manifest.defaultExecutionAllowed === false && manifest.cases.every((item) => item.executionAllowed === false));
check("metadata-documentation-token", manifest.networkPolicy.metadataToken === "__METADATA_ENDPOINT_DO_NOT_RESOLVE__");

const result = {
  schema_version: "authored-harness-static-self-check/v1",
  status: "PASS",
  ga_evidence: false,
  dynamic_harness: "NOT_RUN",
  reason: "Static configuration validation cannot establish runtime containment or resource escape.",
  checks,
};
const output = path.join(experiment, "results", "harness-static-self-check.json");
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, `${JSON.stringify(result, null, 2)}\n`, "utf8");
console.log(output);
