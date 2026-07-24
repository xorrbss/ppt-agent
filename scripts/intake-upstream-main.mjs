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
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--json") options.json = true;
    else if (argument === "--fail-on-risk") options.failOnRisk = true;
    else if (["--policy", "--fixture", "--output", "--summary"].includes(argument)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) {
        throw new IntakeError("INVALID_ARGUMENT", `${argument} requires a value`);
      }
      options[argument.slice(2).replaceAll(/-([a-z])/g, (_, letter) => letter.toUpperCase())] =
        value;
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

function validatePolicy(policy) {
  if (
    policy.schemaVersion !== 1 ||
    !/^[0-9a-f]{40}$/.test(policy.baselineSha ?? "") ||
    !/^[^/]+\/[^/]+$/.test(policy.repository ?? "") ||
    !Array.isArray(policy.categories)
  ) {
    throw new IntakeError("INVALID_POLICY", "upstream intake policy is malformed");
  }
}

function requestHeaders() {
  const token = process.env.UPSTREAM_GITHUB_TOKEN || process.env.GITHUB_TOKEN;
  return {
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "ppt-agent-upstream-intake",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function fetchJson(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response;
  try {
    response = await fetch(url, {
      headers: requestHeaders(),
      signal: controller.signal,
      redirect: "error",
    });
  } catch (error) {
    const timedOut = error.name === "AbortError";
    throw new IntakeError(
      timedOut ? "TIMEOUT" : "NETWORK_ERROR",
      timedOut ? "GitHub API request timed out" : "GitHub API request failed",
      { retryable: true, cause: error.message }
    );
  } finally {
    clearTimeout(timer);
  }

  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new IntakeError("INVALID_RESPONSE", "GitHub API returned invalid JSON", {
      httpStatus: response.status,
      retryable: response.status >= 500,
      cause: error.message,
    });
  }

  if (!response.ok) {
    const remaining = response.headers.get("x-ratelimit-remaining");
    const rateLimited =
      response.status === 429 || (response.status === 403 && remaining === "0");
    throw new IntakeError(
      rateLimited ? "RATE_LIMITED" : `HTTP_${response.status}`,
      rateLimited
        ? "GitHub API rate limit exhausted"
        : `GitHub API returned HTTP ${response.status}`,
      {
        httpStatus: response.status,
        retryable: rateLimited || response.status >= 500,
        rateLimitRemaining: remaining,
        rateLimitReset: response.headers.get("x-ratelimit-reset"),
        upstreamMessage: typeof body?.message === "string" ? body.message : undefined,
      }
    );
  }
  return body;
}

function assertSha(value, label) {
  if (!/^[0-9a-f]{40}$/.test(value ?? "")) {
    throw new IntakeError("INVALID_RESPONSE", `${label} is not a full lowercase SHA`);
  }
  return value;
}

async function queryLive(policy) {
  const root = `https://api.github.com/repos/${policy.repository}`;
  const refUrl = `${root}/git/ref/heads/${encodeURIComponent(policy.branch)}`;
  const firstRef = await fetchJson(refUrl, policy.requestTimeoutMs);
  const observedSha = assertSha(firstRef?.object?.sha, "remote main SHA");
  const commit = await fetchJson(`${root}/commits/${observedSha}`, policy.requestTimeoutMs);
  const compare =
    observedSha === policy.baselineSha
      ? null
      : await fetchJson(
          `${root}/compare/${policy.baselineSha}...${observedSha}`,
          policy.requestTimeoutMs
        );
  const finalRef = await fetchJson(refUrl, policy.requestTimeoutMs);
  const finalSha = assertSha(finalRef?.object?.sha, "final remote main SHA");
  if (finalSha !== observedSha) {
    throw new IntakeError(
      "REMOTE_MOVED_DURING_INTAKE",
      "upstream main moved while the snapshot was being inspected",
      { firstSha: observedSha, finalSha, retryable: true }
    );
  }
  return { ref: firstRef, commit, compare };
}

function compile(rule, key) {
  return rule[key] ? new RegExp(rule[key], "im") : null;
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

export function classifyFiles(files, policy, registry) {
  const protectedPaths = protectedPathSet(registry);
  const findings = [];
  let risk = "none";

  for (const file of files ?? []) {
    const evidence = [
      `"status": "${file.status ?? "unknown"}"`,
      file.filename ?? "",
      file.patch ?? "",
    ].join("\n");
    const matches = [];
    for (const rule of policy.categories) {
      const pathRegex = compile(rule, "pathRegex");
      const evidenceRegex = compile(rule, "evidenceRegex");
      if (
        (!pathRegex || pathRegex.test(file.filename ?? "")) &&
        (!evidenceRegex || evidenceRegex.test(evidence))
      ) {
        let severity = rule.severity;
        const riskRegex = compile(rule, "riskRegex");
        if (riskRegex?.test(evidence)) severity = "contract-risk";
        matches.push({ id: rule.id, label: rule.label, severity });
        risk = maxSeverity(risk, severity);
      }
    }
    if (protectedPaths.has(file.filename)) {
      matches.push({
        id: "protected-local-patch-overlap",
        label: "protected local patch overlap",
        severity: "contract-risk",
      });
      risk = "contract-risk";
    }
    findings.push({
      path: file.filename,
      status: file.status ?? "unknown",
      additions: file.additions ?? 0,
      deletions: file.deletions ?? 0,
      categories:
        matches.length > 0
          ? matches
          : [{ id: "unclassified-upstream-change", label: "unclassified", severity: "info" }],
    });
    if (matches.length === 0) risk = maxSeverity(risk, "info");
  }
  return { findings, risk };
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

function validateBaselineMetadata(metadata, policy) {
  const expected = policy.baselineMetadata ?? {};
  const mismatches = [];
  if (expected.committedAt && metadata.committedAt !== expected.committedAt) {
    mismatches.push(`committedAt expected ${expected.committedAt}, got ${metadata.committedAt}`);
  }
  if (expected.subject && metadata.subject !== expected.subject) {
    mismatches.push(`subject expected "${expected.subject}", got "${metadata.subject}"`);
  }
  return mismatches;
}

export function assessSnapshot(snapshot, policy, registry) {
  if (snapshot?.error) {
    return {
      schemaVersion: 1,
      status: "intake-error",
      changeDetected: false,
      risk: "none",
      classificationComplete: false,
      repository: policy.repository,
      branch: policy.branch,
      baselineSha: policy.baselineSha,
      observedSha: null,
      error: {
        code: snapshot.error.code ?? "FIXTURE_ERROR",
        message: snapshot.error.message ?? "fixture intake error",
        httpStatus: snapshot.error.httpStatus ?? null,
      },
      findings: [],
    };
  }

  const observedSha = assertSha(snapshot?.ref?.object?.sha, "remote main SHA");
  const metadata = commitMetadata(snapshot.commit);
  if (metadata.sha !== observedSha) {
    throw new IntakeError("INVALID_RESPONSE", "ref SHA and commit SHA differ");
  }
  const changeDetected = observedSha !== policy.baselineSha;
  const metadataMismatches = changeDetected
    ? []
    : validateBaselineMetadata(metadata, policy);

  if (!changeDetected) {
    return {
      schemaVersion: 1,
      status: metadataMismatches.length ? "intake-error" : "unchanged",
      changeDetected: false,
      risk: metadataMismatches.length ? "contract-risk" : "none",
      classificationComplete: metadataMismatches.length === 0,
      repository: policy.repository,
      branch: policy.branch,
      baselineSha: policy.baselineSha,
      observedSha,
      metadata,
      metadataMismatches,
      compare: { status: "identical", aheadBy: 0, behindBy: 0, totalCommits: 0, files: 0 },
      findings: [],
      ...(metadataMismatches.length
        ? {
            error: {
              code: "BASELINE_METADATA_MISMATCH",
              message: "the pinned SHA returned unexpected commit metadata",
            },
          }
        : {}),
    };
  }

  if (!snapshot.compare || !Array.isArray(snapshot.compare.files)) {
    throw new IntakeError("INVALID_RESPONSE", "changed upstream requires compare file evidence");
  }
  const classification = classifyFiles(snapshot.compare.files, policy, registry);
  const limit = policy.maximumCompareFiles ?? 300;
  const compareStatus = snapshot.compare.status ?? "unknown";
  const classificationComplete =
    snapshot.compare.files.length < limit &&
    ["ahead", "identical"].includes(compareStatus);
  const risk = classificationComplete
    ? classification.risk
    : maxSeverity(classification.risk, "contract-risk");

  return {
    schemaVersion: 1,
    status: "change-detected",
    changeDetected: true,
    risk,
    classificationComplete,
    repository: policy.repository,
    branch: policy.branch,
    baselineSha: policy.baselineSha,
    observedSha,
    metadata,
    metadataMismatches: [],
    compare: {
      status: compareStatus,
      aheadBy: snapshot.compare.ahead_by ?? null,
      behindBy: snapshot.compare.behind_by ?? null,
      totalCommits: snapshot.compare.total_commits ?? null,
      files: snapshot.compare.files.length,
    },
    findings: classification.findings,
    notes: [
      "Evidence is read-only; no merge, cherry-pick, fetch, checkout, registry update, or code transplant was performed.",
      "Category matches identify review scope; they do not assert that local authored/adaptive/export behavior changed.",
      ...(!classificationComplete
        ? ["Compare evidence is incomplete or non-forward; manual review is required."]
        : []),
    ],
  };
}

function escapeCell(value) {
  return String(value ?? "").replaceAll("|", "\\|").replaceAll("\n", " ");
}

export function renderMarkdown(result) {
  const lines = [
    "# Upstream main intake",
    "",
    `- Status: \`${result.status}\``,
    `- Risk: \`${result.risk}\``,
    `- Repository/branch: \`${result.repository}:${result.branch}\``,
    `- Pinned SHA: \`${result.baselineSha}\``,
    `- Observed SHA: ${result.observedSha ? `\`${result.observedSha}\`` : "_unavailable_"}`,
    `- Change detected: \`${result.changeDetected}\``,
    `- Classification complete: \`${result.classificationComplete}\``,
  ];
  if (result.metadata) {
    lines.push(
      `- Commit time: \`${result.metadata.committedAt}\``,
      `- Subject: ${result.metadata.subject}`
    );
  }
  if (result.compare) {
    lines.push(
      `- Compare: \`${result.compare.status}\`; ahead ${result.compare.aheadBy}, behind ${result.compare.behindBy}, commits ${result.compare.totalCommits}, files ${result.compare.files}`
    );
  }
  if (result.error) {
    lines.push(
      "",
      "## Intake error",
      "",
      `\`${result.error.code}\`: ${result.error.message}`,
      "",
      "**This operational/configuration error is not an upstream change.**"
    );
  }
  if (result.findings?.length) {
    lines.push(
      "",
      "## Compatibility delta",
      "",
      "| File | Status | +/- | Classification |",
      "| --- | --- | ---: | --- |"
    );
    for (const finding of result.findings) {
      const categories = finding.categories
        .map((category) => `${category.label} (${category.severity})`)
        .join("<br>");
      lines.push(
        `| ${escapeCell(finding.path)} | ${escapeCell(finding.status)} | +${finding.additions}/-${finding.deletions} | ${escapeCell(categories)} |`
      );
    }
  }
  if (result.notes?.length) {
    lines.push("", "## Guardrails", "", ...result.notes.map((note) => `- ${note}`));
  }
  lines.push("");
  return lines.join("\n");
}

function errorResult(error, policy) {
  const known = error instanceof IntakeError;
  return {
    schemaVersion: 1,
    status: "intake-error",
    changeDetected: false,
    risk: "none",
    classificationComplete: false,
    repository: policy?.repository ?? "unknown",
    branch: policy?.branch ?? "unknown",
    baselineSha: policy?.baselineSha ?? null,
    observedSha: null,
    error: {
      code: known ? error.code : "UNEXPECTED_ERROR",
      message: known ? error.message : "unexpected intake failure",
      ...(known ? error.details : {}),
    },
    findings: [],
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
    const snapshot = options.fixture
      ? await readJson(options.fixture)
      : await queryLive(policy);
    result = assessSnapshot(snapshot, policy, registry);
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
