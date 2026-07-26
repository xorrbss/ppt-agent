#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const options = {
    command,
    dryRun: false,
    allowLocalRehearsal: false,
    requireSigning: false,
    requireR2: false,
  };
  for (const argument of rest) {
    if (argument === "--dry-run") options.dryRun = true;
    else if (argument === "--allow-local-rehearsal") options.allowLocalRehearsal = true;
    else if (argument === "--require-signing") options.requireSigning = true;
    else if (argument === "--require-r2") options.requireR2 = true;
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!["canary", "rollback-drain", "verify-off", "release"].includes(command)) {
    throw new Error(
      "Usage: operational-release-preflight.mjs " +
        "<canary|rollback-drain|verify-off|release> [options]"
    );
  }
  return options;
}

function isFalse(value) {
  return /^(?:0|false|no|off)$/i.test(String(value ?? "").trim());
}

function databaseDescriptor(environment, allowLocalRehearsal) {
  const raw = environment.DATABASE_URL;
  if (!raw) throw new Error("DATABASE_URL is required; no database URL was supplied");

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("DATABASE_URL is not a valid URL");
  }
  if (!/^postgres(?:ql)?(?:\+[^:]+)?:$/.test(parsed.protocol)) {
    throw new Error("DATABASE_URL must select PostgreSQL for this operational gate");
  }
  const database = parsed.pathname.replace(/^\/+/, "");
  if (!database) throw new Error("DATABASE_URL must name a database");
  const local = ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname);
  if (local && !allowLocalRehearsal) {
    throw new Error(
      "Managed canary mode rejects loopback PostgreSQL; use --allow-local-rehearsal only for a disposable local drill"
    );
  }
  return { dialect: "postgresql", database, hostKind: local ? "local" : "managed" };
}

function validateDeployment(environment, options, phase) {
  const database = databaseDescriptor(environment, options.allowLocalRehearsal);
  const tier = String(environment.TEMPLATE_V2_DEPLOYMENT_TIER ?? "").toLowerCase();
  if (!options.allowLocalRehearsal && !["staging", "production"].includes(tier)) {
    throw new Error("Managed operations require TEMPLATE_V2_DEPLOYMENT_TIER=staging or production");
  }
  if (phase === "canary") {
    if (isFalse(environment.ENABLE_TEMPLATE_V2) || !environment.ENABLE_TEMPLATE_V2) {
      throw new Error("Canary preflight requires ENABLE_TEMPLATE_V2=true");
    }
    if (!String(environment.TEMPLATE_V2_TEMPLATE_ALLOWLIST ?? "").trim()) {
      throw new Error("Canary preflight requires a non-empty TEMPLATE_V2_TEMPLATE_ALLOWLIST");
    }
  }
  if (phase === "verify-off" && !isFalse(environment.ENABLE_TEMPLATE_V2)) {
    throw new Error("OFF verification requires ENABLE_TEMPLATE_V2=false (or 0/no/off)");
  }
  return { database, tier: tier || "local-rehearsal" };
}

function operationFor(command) {
  if (command === "canary") {
    return ["uv", ["run", "python", "scripts/check_template_v2_canary.py"]];
  }
  const mode = command === "rollback-drain" ? "rollback" : "health";
  return ["uv", ["run", "python", "scripts/check_template_v2_operations.py", "--mode", mode]];
}

function runOperationalGate(options, environment = process.env, runner = spawnSync) {
  const deployment = validateDeployment(environment, options, options.command);
  const [executable, args] = operationFor(options.command);
  const summary = {
    status: options.dryRun ? "dry-run" : "running",
    phase: options.command,
    database: deployment.database,
    tier: deployment.tier,
    command: [executable, ...args],
  };
  if (options.dryRun) return summary;
  const result = runner(executable, args, {
    cwd: path.join(repoRoot, "servers", "fastapi"),
    env: environment,
    encoding: "utf8",
    stdio: "inherit",
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${options.command} gate failed with exit code ${result.status}`);
  }
  return { ...summary, status: "passed" };
}

function matchBuildValue(source, property) {
  const match = source.match(new RegExp(`\\b${property}:\\s*["']([^"']+)["']`));
  return match?.[1] ?? null;
}

async function checkReleaseInputs(options, environment = process.env) {
  const buildSource = await readFile(path.join(repoRoot, "electron", "build.js"), "utf8");
  const identityName = matchBuildValue(buildSource, "identityName");
  const publisher = matchBuildValue(buildSource, "publisher");
  const signingBlockers = [];
  const r2Blockers = [];

  if (!environment.CSC_LINK) signingBlockers.push("CSC_LINK is not configured");
  if (!identityName || identityName === "PresentonAI.Presenton") {
    signingBlockers.push("AppX identity is still the upstream/default identity");
  }
  if (!publisher || publisher === "CN=8A2C57B5-F1C6-473A-93EE-2E9B72134341") {
    signingBlockers.push("AppX publisher is still the upstream/default publisher");
  }
  for (const name of ["R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY"]) {
    if (!environment[name]) r2Blockers.push(`${name} is not configured`);
  }

  const failed =
    (options.requireSigning && signingBlockers.length > 0) ||
    (options.requireR2 && r2Blockers.length > 0);
  return {
    status: failed ? "blocked" : "ready",
    signing: {
      required: options.requireSigning,
      identityName,
      publisher,
      credentialPresent: Boolean(environment.CSC_LINK),
      passwordPresent: Boolean(environment.CSC_KEY_PASSWORD),
      blockers: signingBlockers,
    },
    r2: {
      required: options.requireR2,
      credentialsPresent: r2Blockers.length === 0,
      blockers: r2Blockers,
      note: "Credential presence is checked without printing values; bucket write access still requires the workflow probe.",
    },
  };
}

async function main(argv = process.argv.slice(2), environment = process.env) {
  const options = parseArgs(argv);
  const result =
    options.command === "release"
      ? await checkReleaseInputs(options, environment)
      : runOperationalGate(options, environment);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (result.status === "blocked") process.exitCode = 2;
  return result;
}

export {
  checkReleaseInputs,
  databaseDescriptor,
  main,
  parseArgs,
  runOperationalGate,
  validateDeployment,
};

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 2;
  });
}
