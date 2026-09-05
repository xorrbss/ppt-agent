import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// Contract for the additive `multi` compose profile (PostgreSQL + one-shot
// migrate + API + N workers).  The file is read as text and sliced by
// indentation on purpose: the root package has no YAML dependency, and the
// invariants below are about literal keys an operator can grep for.

const read = (filePath) =>
  readFileSync(new URL(`../${filePath}`, import.meta.url), "utf8").replaceAll(
    "\r\n",
    "\n",
  );

const compose = read("docker-compose.yml");
const composeLines = compose.split("\n");

function serviceBlock(name) {
  const start = composeLines.indexOf(`  ${name}:`);
  assert.notEqual(start, -1, `compose service ${name} is missing`);
  let end = start + 1;
  while (
    end < composeLines.length &&
    (composeLines[end] === "" ||
      composeLines[end].startsWith("    ") ||
      composeLines[end].startsWith("  #"))
  ) {
    end += 1;
  }
  return composeLines.slice(start + 1, end).join("\n");
}

function environmentValue(block, key) {
  const match = block.match(new RegExp(`^      ${key}: (.*)$`, "mu"));
  assert.ok(match, `environment key ${key} is missing`);
  return match[1].trim();
}

function assertNoEnvironmentKey(block, key) {
  assert.doesNotMatch(
    block,
    new RegExp(`^\\s+${key}:`, "mu"),
    `environment key ${key} must not be set here`,
  );
}

function dependsOn(block) {
  const section = block.match(/^    depends_on:\n((?:      .*\n?)+)/mu);
  assert.ok(section, "depends_on is missing");
  return Object.fromEntries(
    [...section[1].matchAll(/^      ([a-z-]+):\n        condition: ([a-z_]+)$/gmu)].map(
      (entry) => [entry[1], entry[2]],
    ),
  );
}

const databaseTarget = (url) => {
  // The owner login may be a compose interpolation; the runtime logins are literal.
  const match = url.match(
    /^postgresql\+asyncpg:\/\/([a-z_]+|\$\{[A-Z0-9_]+:-[a-z_]+\}):[^@]*@(postgres:5432\/\S+)$/u,
  );
  assert.ok(match, `role DSN must be an asyncpg URL against the postgres service: ${url}`);
  return { login: match[1], target: match[2] };
};

const services = Object.fromEntries(
  ["postgres", "migrate", "api", "worker"].map((name) => [name, serviceBlock(name)]),
);

test("the multi profile is additive and opt-in", () => {
  for (const [name, block] of Object.entries(services)) {
    assert.match(block, /^    profiles: \["multi"\]$/mu, `${name} must be behind the multi profile`);
  }
  for (const stock of ["production", "production-gpu", "development", "development-gpu"]) {
    assert.doesNotMatch(serviceBlock(stock), /^\s+profiles:/mu, `${stock} must stay profile-less`);
  }
  assert.match(compose, /^volumes:\n(?:  [a-z0-9_]+:\n)*  presenton_multi_postgres_data:$/mu);
  assert.match(
    services.postgres,
    /^      - presenton_multi_postgres_data:\/var\/lib\/postgresql\/data$/mu,
  );
  assert.match(services.postgres, /pg_isready/u, "postgres needs a readiness healthcheck");
});

test("only the one-shot migrate service runs migrations, as the database owner", () => {
  const migrate = services.migrate;
  assert.deepEqual(dependsOn(migrate), { postgres: "service_healthy" });
  assert.match(migrate, /^    build:$/mu, "the profile image is built once, on migrate");
  assert.match(
    migrate,
    /^    command: \["python", "-m", "scripts\.provision_public_v2_local_db_roles"\]$/mu,
  );
  assert.equal(environmentValue(migrate, "MIGRATE_DATABASE_ON_STARTUP"), '"true"');
  assertNoEnvironmentKey(migrate, "PUBLIC_DATABASE_PROCESS_ROLE");
  assertNoEnvironmentKey(migrate, "PUBLIC_API_DATABASE_URL");
  assertNoEnvironmentKey(migrate, "PUBLIC_CONTROLLER_DATABASE_URL");
  assert.match(environmentValue(migrate, "DATABASE_URL"), /^postgresql\+asyncpg:\/\/.*@postgres:5432\//u);

  const provisioner = read("servers/fastapi/scripts/provision_public_v2_local_db_roles.py");
  assert.match(provisioner, /from migrations import _run_migrations/u);
  assert.match(provisioner, /API_LOGIN = "presenton_public_api_login"/u);
  assert.match(provisioner, /CONTROLLER_LOGIN = "presenton_public_controller_login"/u);

  for (const name of ["api", "worker"]) {
    assert.equal(
      environmentValue(services[name], "MIGRATE_DATABASE_ON_STARTUP"),
      '"false"',
      `${name} must never run migrations`,
    );
  }
});

test("api and worker run the external worker mode against the same PostgreSQL database with split roles", () => {
  const { api, worker } = services;
  for (const [name, block] of [["api", api], ["worker", worker]]) {
    assert.match(block, /^      <<: \*presenton-env$/mu, `${name} must inherit the shared environment`);
    assert.equal(environmentValue(block, "ENVIRONMENT"), "production", `${name} tier must be literal`);
    assert.equal(environmentValue(block, "DATABASE_URL"), '""', `${name} must not carry a generic DSN`);
    assert.equal(environmentValue(block, "WORKSPACE_OPERATION_WORKER_MODE"), "external");
    assert.equal(environmentValue(block, "PROVIDER_DURABLE_RUNTIME_REQUIRED"), '"true"');
    assert.equal(environmentValue(block, "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"), "postgresql-cluster");
    assertNoEnvironmentKey(block, "PUBLIC_MIGRATION_DATABASE_URL");
  }

  assert.equal(environmentValue(api, "PUBLIC_DATABASE_PROCESS_ROLE"), "api");
  assert.equal(environmentValue(api, "WORKSPACE_OPERATION_WORKER_LAUNCH"), "service");
  assertNoEnvironmentKey(api, "PUBLIC_CONTROLLER_DATABASE_URL");
  assert.deepEqual(dependsOn(api), {
    postgres: "service_healthy",
    migrate: "service_completed_successfully",
  });
  assert.match(api, /^      test: \["CMD", "curl", "-f", "http:\/\/localhost\/api\/health"\]$/mu);

  assert.equal(environmentValue(worker, "PUBLIC_DATABASE_PROCESS_ROLE"), "controller");
  assert.equal(environmentValue(worker, "PPT_PROCESS_PROFILE"), "generation");
  assert.equal(environmentValue(worker, "WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"), "generation");
  assert.equal(environmentValue(worker, "USER_CONFIG_PATH"), "/app_data/userConfig.json");
  assert.equal(environmentValue(worker, "NEXT_INTERNAL_URL"), "http://api");
  assertNoEnvironmentKey(worker, "PUBLIC_API_DATABASE_URL");
  assertNoEnvironmentKey(worker, "WORKSPACE_OPERATION_WORKER_LAUNCH");
  assert.deepEqual(dependsOn(worker), { postgres: "service_healthy", api: "service_healthy" });
  assert.match(worker, /^    command: \["python", "-m", "services\.workspace_operation_worker"\]$/mu);
  assert.match(worker, /^    deploy:\n      replicas: \$\{PRESENTON_MULTI_WORKER_REPLICAS:-1\}$/mu);
  assert.doesNotMatch(worker, /^    ports:/mu, "replicas cannot bind host ports");
  const metricsPort = environmentValue(worker, "WORKSPACE_OPERATION_METRICS_PORT").replaceAll('"', "");
  assert.match(worker, new RegExp(`curl", "-f", "http://127\\.0\\.0\\.1:${metricsPort}/healthz"`, "u"));

  const migrateTarget = databaseTarget(environmentValue(services.migrate, "DATABASE_URL"));
  const apiTarget = databaseTarget(environmentValue(api, "PUBLIC_API_DATABASE_URL"));
  const workerTarget = databaseTarget(environmentValue(worker, "PUBLIC_CONTROLLER_DATABASE_URL"));
  assert.equal(apiTarget.target, workerTarget.target, "api and worker must share one database");
  assert.equal(migrateTarget.target, apiTarget.target, "migrate must upgrade that same database");
  assert.equal(apiTarget.login, "presenton_public_api_login");
  assert.equal(workerTarget.login, "presenton_public_controller_login");
  assert.notEqual(migrateTarget.login, apiTarget.login);
  assert.notEqual(migrateTarget.login, workerTarget.login);

  const database = read("servers/fastapi/services/database.py");
  assert.match(database, /PUBLIC_CONTROLLER_DATABASE_ROLE = "presenton_public_controller"/u);
  assert.match(database, /PUBLIC_API_DATABASE_ROLE = "presenton_public_api"/u);
  const worker_module = read("servers/fastapi/services/workspace_operation_worker.py");
  assert.match(worker_module, /await require_public_controller_database_authority\(\)/u);
  const lifespan = read("servers/fastapi/api/lifespan.py");
  assert.match(lifespan, /await require_public_api_database_authority\(\)/u);
});

test("start.js bundles a generation worker unless the deployment provides one as a service", () => {
  const launcher = read("start.js");
  assert.match(
    launcher,
    /process\.env\.WORKSPACE_OPERATION_WORKER_LAUNCH \?\? "bundled"/u,
  );
  assert.match(launcher, /=== "external" &&\n\s*workspaceWorkerLaunch === "bundled"/u);
  assert.match(
    launcher,
    /PPT_PROCESS_PROFILE: "generation",\n\s*WORKSPACE_OPERATION_EXTERNAL_CONTROLLER: "generation",\n\s*\.\.\.fastApiEnvironment,/u,
    "explicit env must be able to override the bundled worker profile",
  );

  const registry = JSON.parse(read("servers/fastapi/constants/settings_registry.json"));
  const knob = registry.settings.find((setting) => setting.key === "WORKSPACE_OPERATION_WORKER_LAUNCH");
  assert.ok(knob, "the start.js knob must be registered");
  assert.deepEqual(knob.choices, ["bundled", "service"]);
  assert.equal(knob.default, "bundled");
});

test("the profile ships with an operator runbook and is wired into the shared CI suite", () => {
  const runbook = read("docs/multi-process-compose-runbook-20260906.md");
  assert.match(runbook, /--profile multi/u);
  assert.match(runbook, /--scale worker=2/u);
  assert.match(runbook, /restart api/u);
  assert.ok(runbook.split("\n").length <= 150, "the runbook must stay under 150 lines");

  const rootPackage = JSON.parse(read("package.json"));
  assert.match(rootPackage.scripts["test:next-ci"], /scripts\/multi-process-compose-contract\.test\.mjs/u);
});
