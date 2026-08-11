#!/usr/bin/env node

import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const defaultPolicyPath = "compatibility/upstream-intake-policy.json";
const severityRank = { none: 0, info: 1, review: 2, "contract-risk": 3 };

class IntakeError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = "IntakeError";
    this.code = code;
    this.details = details;
  }
}

function parseArgs(argv) {
  const options = {
    policy: defaultPolicyPath,
    fixture: null,
    output: null,
    summary: null,
    json: false,
    failOnRisk: false,
    prepareFinalize: false,
    integrationVerified: false,
    verificationRef: null,
    authorization: null,
    finalizeOutput: null,
  };
  const valueArguments = [
    "--policy",
    "--fixture",
    "--output",
    "--summary",
    "--verification-ref",
    "--authorization",
    "--finalize-output",
  ];

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--json") options.json = true;
    else if (argument === "--fail-on-risk") options.failOnRisk = true;
    else if (argument === "--prepare-finalize") options.prepareFinalize = true;
    else if (argument === "--integration-verified") options.integrationVerified = true;
    else if (valueArguments.includes(argument)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) {
        throw new IntakeError("INVALID_ARGUMENT", `${argument} requires a value`);
      }
      const key = argument
        .slice(2)
        .replaceAll(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      options[key] = value;
      index += 1;
    } else {
      throw new IntakeError("INVALID_ARGUMENT", `unknown argument: ${argument}`);
    }
  }
  return options;
}

async function readJson(relativeOrAbsolutePath) {
  const resolved = path.resolve(repoRoot, relativeOrAbsolutePath);
  let text;
  try {
    text = await readFile(resolved, "utf8");
  } catch (error) {
    throw new IntakeError("FILE_READ_ERROR", `cannot read ${relativeOrAbsolutePath}`, {
      cause: error.message,
    });
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new IntakeError("INVALID_JSON", `invalid JSON in ${relativeOrAbsolutePath}`, {
      cause: error.message,
    });
  }
}

function assertSha(value, label) {
  if (!/^[0-9a-f]{40}$/.test(value ?? "")) {
    throw new IntakeError("INVALID_RESPONSE", `${label} is not a full lowercase SHA`);
  }
  return value;
}

function acceptedSha(policy) {
  return policy.acceptedBaseline?.sha ?? policy.baselineSha;
}

function validatePolicy(policy) {
  if (
    policy.schemaVersion !== 2 ||
    !/^[^/]+\/[^/]+$/.test(policy.repository ?? "") ||
    !Array.isArray(policy.domains)
  ) {
    throw new IntakeError("INVALID_POLICY", "Upstream Intake V2 policy is malformed");
  }
  assertSha(acceptedSha(policy), "accepted baseline SHA");
  assertSha(policy.observedUpstream?.sha, "observed upstream SHA");
}

function maxSeverity(left, right) {
  return severityRank[right] > severityRank[left] ? right : left;
}

function protectedPathSet(registry) {
  return new Set(
    (registry.patches ?? []).flatMap((patch) =>
      (patch.files ?? []).map((file) => file.path)
    )
  );
}

function compile(expression) {
  return new RegExp(expression, "i");
}

function isApiV1RouteRemoval(file) {
  return (
    /^servers\/fastapi\/api\/v1\//.test(file.filename ?? "") &&
    /^-.*(?:\/api\/v1|include_router|@[^\n]+\.(?:get|post|put|patch|delete)\()/im.test(
      file.patch ?? ""
    )
  );
}

function isExporterVersionMutation(file, domain) {
  return (
    domain.id === "renderer-export-runtime" &&
    /(?:presentationExportVersion|exportVersion|presentation-export)/.test(file.patch ?? "")
  );
}

function domainSeverity(file, domain, evidenceUnavailable) {
  if (isApiV1RouteRemoval(file) || isExporterVersionMutation(file, domain)) {
    return "contract-risk";
  }
  if (evidenceUnavailable && domain.severity === "contract-risk") {
    return "review";
  }
  return domain.severity;
}

function contractEntries(policy, registry) {
  return Object.entries(policy.contractManifest ?? {}).map(([id, rule]) => ({
    id,
    rule,
    protectedPaths: id === "forkOwnedOverlap" ? protectedPathSet(registry) : null,
  }));
}

function matchesContract(file, contract) {
  const pathname = file.filename ?? "";
  if (contract.protectedPaths) {
    return contract.protectedPaths.has(pathname);
  }
  const evidence = `${pathname}\n${file.patch ?? ""}`;
  return (contract.rule.paths ?? []).some((needle) => evidence.includes(needle));
}

export function classifyFiles(files, policy, registry) {
  const protectedPaths = protectedPathSet(registry);
  const findings = [];
  let risk = "none";

  for (const file of files ?? []) {
    const pathname = file.filename ?? "";
    const evidenceUnavailable = !file.patch;
    const categories = [];

    for (const domain of policy.domains) {
      if (!compile(domain.pathRegex).test(pathname)) continue;
      const severity = domainSeverity(file, domain, evidenceUnavailable);
      categories.push({ id: domain.id, label: domain.label, severity });
    }

    // `templates/v2` is not a URL route. Only a literal API route directory may
    // receive the backend-v2 contract classification.
    if (/^servers\/fastapi\/api\/v2\//.test(pathname)) {
      categories.push({
        id: "api-v2-backend-route",
        label: "/api/v2 backend route",
        severity: "contract-risk",
      });
    }

    if (protectedPaths.has(pathname)) {
      categories.push({
        id: "protected-local-patch-overlap",
        label: "fork-owned overlap",
        severity: "contract-risk",
      });
    }

    if (!categories.length) {
      categories.push({
        id: "unclassified-upstream-change",
        label: "unclassified",
        severity: "info",
      });
    }

    for (const category of categories) {
      risk = maxSeverity(risk, category.severity);
    }
    findings.push({
      path: pathname,
      status: file.status ?? "unknown",
      additions: file.additions ?? 0,
      deletions: file.deletions ?? 0,
      categories,
    });
  }
  return { findings, risk };
}

function buildContractDiff(files, policy, registry) {
  const contracts = {};
  for (const { id, rule, protectedPaths } of contractEntries(policy, registry)) {
    const changes = (files ?? [])
      .filter((file) => matchesContract(file, { rule, protectedPaths }))
      .map((file) => ({
        path: file.filename,
        status: file.status ?? "unknown",
        additions: file.additions ?? 0,
        deletions: file.deletions ?? 0,
        evidenceAvailable: Boolean(file.patch),
      }));
    contracts[id] = {
      status: changes.length ? "changed" : "unchanged",
      severity: rule.severity,
      paths: changes.map((change) => change.path),
      changes,
    };
  }
  return contracts;
}

function commitMetadata(commit) {
  const message = commit?.commit?.message;
  return {
    sha: assertSha(commit?.sha, "commit SHA"),
    committedAt: commit?.commit?.committer?.date ?? null,
    subject: typeof message === "string" ? message.split(/\r?\n/, 1)[0] : null,
    url: commit?.html_url ?? null,
  };
}

function commitClassifications(snapshot, policy, registry) {
  return (snapshot.commits ?? []).map((commit) => {
    const classified = classifyFiles(commit.files ?? [], policy, registry);
    return {
      sha: assertSha(commit.sha, "commit SHA"),
      subject: commit.subject ?? null,
      risk: classified.risk,
      domains: [
        ...new Set(
          classified.findings.flatMap((finding) =>
            finding.categories.map((category) => category.id)
          )
        ),
      ],
      files: classified.findings,
    };
  });
}

function verifyAcceptedMetadata(policy, current) {
  const expected = policy.acceptedBaseline;
  const mismatches = [];

  if (expected.committedAt) {
    const expectedInstant = Date.parse(expected.committedAt);
    const currentInstant = Date.parse(current.committedAt ?? "");
    if (
      !Number.isFinite(expectedInstant) ||
      !Number.isFinite(currentInstant) ||
      expectedInstant !== currentInstant
    ) {
      mismatches.push(
        `committedAt expected ${expected.committedAt}, got ${current.committedAt}`
      );
    }
  }
  if (expected.subject && expected.subject !== current.subject) {
    mismatches.push(`subject expected ${expected.subject}, got ${current.subject}`);
  }
  return mismatches;
}

function intakeErrorResult(snapshot, policy, registry) {
  return {
    schemaVersion: 2,
    status: "intake-error",
    changeDetected: false,
    risk: "none",
    classificationComplete: false,
    acceptedBaseline: policy.acceptedBaseline,
    observedUpstream: null,
    error: snapshot.error,
    findings: [],
    contractDiff: buildContractDiff([], policy, registry),
  };
}

function unchangedResult(policy, current, mismatches, registry) {
  return {
    schemaVersion: 2,
    status: mismatches.length ? "intake-error" : "unchanged",
    changeDetected: false,
    risk: mismatches.length ? "contract-risk" : "none",
    classificationComplete: !mismatches.length,
    acceptedBaseline: policy.acceptedBaseline,
    observedUpstream: current,
    metadataMismatches: mismatches,
    findings: [],
    contractDiff: buildContractDiff([], policy, registry),
    ...(mismatches.length
      ? {
          error: {
            code: "BASELINE_METADATA_MISMATCH",
            message: "accepted baseline metadata differs",
          },
        }
      : {}),
  };
}

export function assessSnapshot(snapshot, policy, registry) {
  if (snapshot?.error) {
    return intakeErrorResult(snapshot, policy, registry);
  }

  const observedSha = assertSha(snapshot?.ref?.object?.sha, "remote main SHA");
  const current = commitMetadata(snapshot.commit);
  if (observedSha !== current.sha) {
    throw new IntakeError("INVALID_RESPONSE", "ref SHA and commit SHA differ");
  }

  const accepted = acceptedSha(policy);
  if (observedSha === accepted) {
    return unchangedResult(policy, current, verifyAcceptedMetadata(policy, current), registry);
  }

  if (!snapshot.compare || !Array.isArray(snapshot.compare.files)) {
    throw new IntakeError("INVALID_RESPONSE", "changed upstream requires compare file evidence");
  }

  const classification = classifyFiles(snapshot.compare.files, policy, registry);
  const compareIsComplete =
    snapshot.compare.files.length < policy.maximumCompareFiles &&
    snapshot.compare.status === "ahead";
  const observedMatchesPinned = observedSha === policy.observedUpstream.sha;
  const classificationComplete = compareIsComplete && observedMatchesPinned;
  const risk = classificationComplete
    ? classification.risk
    : maxSeverity(classification.risk, "contract-risk");

  return {
    schemaVersion: 2,
    status: "change-detected",
    changeDetected: true,
    risk,
    classificationComplete,
    range: {
      acceptedSha: accepted,
      observedSha,
      exactPinnedObservation: observedMatchesPinned,
    },
    acceptedBaseline: policy.acceptedBaseline,
    observedUpstream: current,
    compare: {
      status: snapshot.compare.status ?? "unknown",
      aheadBy: snapshot.compare.ahead_by ?? null,
      behindBy: snapshot.compare.behind_by ?? null,
      totalCommits: snapshot.compare.total_commits ?? null,
      files: snapshot.compare.files.length,
    },
    findings: classification.findings,
    commitClassifications: commitClassifications(snapshot, policy, registry),
    contractDiff: buildContractDiff(snapshot.compare.files, policy, registry),
    notes: [
      "Accepted baseline is immutable during intake.",
      "Observed upstream is evidence only; it does not advance the accepted SHA.",
      ...(observedMatchesPinned
        ? []
        : ["Observed upstream differs from the pinned review target; a new intake is required."]),
      ...(!compareIsComplete
        ? ["Compare evidence is incomplete or non-forward; manual review is required."]
        : []),
    ],
  };
}

export function renderMarkdown(result) {
  const lines = [
    "# Upstream main intake V2",
    "",
    `- Status: \`${result.status}\``,
    `- Risk: \`${result.risk}\``,
    // This is deliberately distinct from compatibility/upstream-compatibility.json's
    // compatibility baseline. Intake only verifies the accepted remote observation.
    `- Intake accepted baseline: \`${result.acceptedBaseline?.sha ?? "unavailable"}\``,
    `- Intake observed upstream: \`${result.observedUpstream?.sha ?? "unavailable"}\``,
    `- Classification complete: \`${result.classificationComplete}\``,
  ];
  if (result.range) {
    lines.push(`- Exact pinned observation: \`${result.range.exactPinnedObservation}\``);
  }
  if (result.error) {
    lines.push(
      "",
      "## Intake error",
      "",
      `\`${result.error.code ?? "FIXTURE_ERROR"}\`: ${result.error.message}`,
      "",
      "**This operational/configuration error is not an upstream change.**"
    );
  }
  if (result.commitClassifications?.length) {
    lines.push("", "## Commit classifications", "", "| Commit | Risk | Domains |", "| --- | --- | --- |");
    for (const commit of result.commitClassifications) {
      lines.push(
        `| ${commit.sha.slice(0, 8)} ${commit.subject ?? ""} | ${commit.risk} | ${commit.domains.join(", ")} |`
      );
    }
  }
  lines.push("", "## Contract diff", "", "```json", JSON.stringify(result.contractDiff, null, 2), "``");
  if (result.notes?.length) {
    lines.push("", "## Guardrails", "", ...result.notes.map((note) => `- ${note}`));
  }
  return `${lines.join("\n")}\n`;
}

function finalizationCandidate(policy, result, options) {
  if (
    !options.integrationVerified ||
    !options.verificationRef ||
    !options.authorization ||
    !options.finalizeOutput
  ) {
    throw new IntakeError(
      "FINALIZATION_REQUIREMENTS_MISSING",
      "finalization requires integration verification, evidence, authorization, and a separate output path"
    );
  }
  if (
    result.status !== "change-detected" ||
    !result.classificationComplete ||
    !result.range?.exactPinnedObservation
  ) {
    throw new IntakeError(
      "FINALIZATION_NOT_READY",
      "the pinned observation is not fully classified and ready for finalization"
    );
  }
  const target = path.resolve(repoRoot, options.finalizeOutput);
  if (target === path.resolve(repoRoot, options.policy)) {
    throw new IntakeError(
      "IN_PLACE_FINALIZATION_FORBIDDEN",
      "finalization must write a separate candidate policy"
    );
  }
  return {
    ...policy,
    acceptedBaseline: {
      sha: result.observedUpstream.sha,
      committedAt: result.observedUpstream.committedAt,
      subject: result.observedUpstream.subject,
    },
    observedUpstream: {
      ...policy.observedUpstream,
      finalizedFrom: policy.acceptedBaseline.sha,
      verificationRef: options.verificationRef,
      authorizedBy: options.authorization,
    },
    finalization: {
      ...policy.finalization,
      preparedAt: new Date().toISOString(),
      candidateOnly: true,
    },
  };
}

function requestHeaders() {
  const token = process.env.UPSTREAM_GITHUB_TOKEN || process.env.GITHUB_TOKEN;
  return {
    Accept: "application/vnd.github+json",
    "User-Agent": "ppt-agent-upstream-intake",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function fetchJson(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      headers: requestHeaders(),
      signal: controller.signal,
      redirect: "error",
    });
    if (!response.ok) {
      throw new IntakeError(`HTTP_${response.status}`, "GitHub API request failed", {
        httpStatus: response.status,
      });
    }
    return response.json();
  } catch (error) {
    if (error instanceof IntakeError) throw error;
    throw new IntakeError(
      error.name === "AbortError" ? "TIMEOUT" : "NETWORK_ERROR",
      "GitHub API request failed",
      { cause: error.message }
    );
  } finally {
    clearTimeout(timer);
  }
}

async function queryLive(policy) {
  const root = `https://api.github.com/repos/${policy.repository}`;
  const refUrl = `${root}/git/ref/heads/${encodeURIComponent(policy.branch)}`;
  const ref = await fetchJson(refUrl, policy.requestTimeoutMs);
  const observed = assertSha(ref?.object?.sha, "remote main SHA");
  const commit = await fetchJson(`${root}/commits/${observed}`, policy.requestTimeoutMs);
  const compare =
    observed === acceptedSha(policy)
      ? null
      : await fetchJson(
          `${root}/compare/${acceptedSha(policy)}...${observed}`,
          policy.requestTimeoutMs
        );
  const finalRef = await fetchJson(refUrl, policy.requestTimeoutMs);
  if (assertSha(finalRef?.object?.sha, "final remote main SHA") !== observed) {
    throw new IntakeError(
      "REMOTE_MOVED_DURING_INTAKE",
      "upstream main moved while the snapshot was being inspected"
    );
  }
  return { ref, commit, compare };
}

function errorResult(error, policy) {
  const known = error instanceof IntakeError;
  return {
    schemaVersion: 2,
    status: "intake-error",
    changeDetected: false,
    risk: "none",
    classificationComplete: false,
    acceptedBaseline: policy?.acceptedBaseline ?? null,
    observedUpstream: null,
    error: {
      code: known ? error.code : "UNEXPECTED_ERROR",
      message: known ? error.message : "unexpected intake failure",
      ...(known ? error.details : {}),
    },
    findings: [],
    contractDiff: {},
  };
}

async function writeExplicitOutput(filename, content, append = false) {
  const resolved = path.resolve(repoRoot, filename);
  await mkdir(path.dirname(resolved), { recursive: true });
  if (append) await appendFile(resolved, content, "utf8");
  else await writeFile(resolved, content, "utf8");
}

export async function run(argv = process.argv.slice(2)) {
  let options;
  let policy;
  let result;
  try {
    options = parseArgs(argv);
    policy = await readJson(options.policy);
    validatePolicy(policy);
    const registry = await readJson("compatibility/protected-local-patches.json");
    const snapshot = options.fixture ? await readJson(options.fixture) : await queryLive(policy);
    result = assessSnapshot(snapshot, policy, registry);

    if (options.prepareFinalize) {
      const candidate = finalizationCandidate(policy, result, options);
      await writeExplicitOutput(options.finalizeOutput, `${JSON.stringify(candidate, null, 2)}\n`);
      result.finalizationCandidate = path.relative(
        repoRoot,
        path.resolve(repoRoot, options.finalizeOutput)
      );
    }
  } catch (error) {
    result = errorResult(error, policy);
  }

  const markdown = renderMarkdown(result);
  if (options?.output) await writeExplicitOutput(options.output, markdown);
  if (options?.summary) await writeExplicitOutput(options.summary, markdown, true);
  process.stdout.write(options?.json ? `${JSON.stringify(result, null, 2)}\n` : markdown);

  if (result.status === "intake-error") return 1;
  if (options?.failOnRisk && result.risk === "contract-risk") return 2;
  return 0;
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  process.exitCode = await run();
}
