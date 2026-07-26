#!/usr/bin/env node

const { spawnSync } = require("node:child_process");
const { existsSync } = require("node:fs");
const { dirname, join } = require("node:path");

const SEVERITIES = ["info", "low", "moderate", "high", "critical"];

function parseAuditReport(raw, label = "npm audit") {
  let report;
  try {
    report = JSON.parse(raw);
  } catch (error) {
    throw new Error(`${label} did not return valid JSON: ${error.message}`);
  }

  const counts = report?.metadata?.vulnerabilities;
  if (!counts || typeof counts !== "object") {
    throw new Error(`${label} JSON is missing metadata.vulnerabilities`);
  }

  const normalized = {};
  for (const severity of SEVERITIES) {
    const value = Number(counts[severity] ?? 0);
    if (!Number.isSafeInteger(value) || value < 0) {
      throw new Error(`${label} has an invalid ${severity} count`);
    }
    normalized[severity] = value;
  }

  const calculatedTotal = SEVERITIES.reduce(
    (total, severity) => total + normalized[severity],
    0
  );
  const declaredTotal = Number(counts.total ?? calculatedTotal);
  if (!Number.isSafeInteger(declaredTotal) || declaredTotal < 0) {
    throw new Error(`${label} has an invalid total count`);
  }
  if (declaredTotal !== calculatedTotal) {
    throw new Error(
      `${label} total ${declaredTotal} does not match severity sum ${calculatedTotal}`
    );
  }

  const vulnerabilities =
    report.vulnerabilities && typeof report.vulnerabilities === "object"
      ? Object.keys(report.vulnerabilities).sort()
      : [];

  return {
    counts: { ...normalized, total: calculatedTotal },
    packages: vulnerabilities,
  };
}

function evaluateAuditReports({ production, full }) {
  const devBuildCounts = {};
  for (const severity of [...SEVERITIES, "total"]) {
    if (full.counts[severity] < production.counts[severity]) {
      throw new Error(
        `full audit ${severity} count cannot be lower than production audit`
      );
    }
    devBuildCounts[severity] =
      full.counts[severity] - production.counts[severity];
  }

  const productionPackages = new Set(production.packages);
  const devBuildPackages = full.packages.filter(
    (packageName) => !productionPackages.has(packageName)
  );

  return {
    passed: production.counts.total === 0,
    production,
    full,
    devBuild: {
      counts: devBuildCounts,
      packages: devBuildPackages,
    },
  };
}

function formatCounts(counts) {
  return [...SEVERITIES, "total"]
    .map((severity) => `${severity}=${counts[severity]}`)
    .join(" ");
}

function runNpmAudit(args, label) {
  const bundledNpmCli = join(
    dirname(process.execPath),
    "node_modules",
    "npm",
    "bin",
    "npm-cli.js"
  );
  const npmCli =
    process.env.npm_execpath ||
    (existsSync(bundledNpmCli) ? bundledNpmCli : null);
  const command = npmCli
    ? { executable: process.execPath, args: [npmCli] }
    : {
        executable: process.platform === "win32" ? "npm.cmd" : "npm",
        args: [],
      };
  const outcome = spawnSync(
    command.executable,
    [...command.args, "audit", ...args, "--json"],
    {
      cwd: process.cwd(),
      encoding: "utf8",
      shell: process.platform === "win32" && !npmCli,
      windowsHide: true,
    }
  );

  if (outcome.error) {
    throw new Error(`${label} could not start: ${outcome.error.message}`);
  }

  // npm audit exits 1 when it finds a vulnerability. JSON parsing below
  // distinguishes that expected result from registry or command failures.
  if (outcome.status !== 0 && outcome.status !== 1) {
    throw new Error(
      `${label} failed with exit ${outcome.status}: ${
        outcome.stderr.trim() || "no diagnostic output"
      }`
    );
  }

  return parseAuditReport(outcome.stdout, label);
}

function main() {
  const production = runNpmAudit(["--omit=dev"], "production dependency audit");
  const full = runNpmAudit([], "full dependency audit");
  const result = evaluateAuditReports({ production, full });

  console.log(
    `[audit:release] production dependencies: ${formatCounts(
      result.production.counts
    )}`
  );
  console.log(
    `[audit:release] dev/build-only delta: ${formatCounts(
      result.devBuild.counts
    )}`
  );

  if (result.devBuild.packages.length > 0) {
    console.log(
      `[audit:release] dev/build affected packages: ${result.devBuild.packages.join(
        ", "
      )}`
    );
  }

  if (!result.passed) {
    console.error(
      `[audit:release] FAIL: production dependency vulnerabilities were found: ${
        result.production.packages.join(", ") || "see counts above"
      }`
    );
    process.exitCode = 1;
    return;
  }

  console.log(
    "[audit:release] PASS: no production dependency vulnerabilities were reported."
  );
  if (result.devBuild.counts.total > 0) {
    console.log(
      "[audit:release] Dev/build findings remain visible but do not enter the packaged runtime gate."
    );
  }
}

module.exports = {
  SEVERITIES,
  evaluateAuditReports,
  formatCounts,
  parseAuditReport,
};

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(`[audit:release] ERROR: ${error.message}`);
    process.exitCode = 1;
  }
}
