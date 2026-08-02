import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (name) => readFileSync(resolve(root, name), "utf8");
const dockerfile = read("Dockerfile");
const compose = read("docker-compose.yml");
const canary = read("docker-compose.chat-first-canary.yml");

assert.doesNotMatch(canary, /\bextends:\s*\n\s*service:\s*production/);
assert.doesNotMatch(canary, /"5000:80"|"1455:1455"/);

assert.match(dockerfile, /ARG NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED=false/);
assert.match(
  dockerfile,
  /NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED=\$\{NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED\}/,
);

for (const required of [
  "CHAT_FIRST_WORKSPACE_ENABLED: ${CHAT_FIRST_WORKSPACE_ENABLED:-false}",
  "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE: ${CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE:-}",
  "NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED: ${NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED:-false}",
  "WORKSPACE_ASSET_MALWARE_SCAN_MODE: ${WORKSPACE_ASSET_MALWARE_SCAN_MODE:-disabled}",
  "NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED: ${NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED:-false}",
]) {
  assert.ok(compose.includes(required), `missing fail-closed compose contract: ${required}`);
}

for (const port of ["CHAT_FIRST_CANARY_PORT:-5050", "CHAT_FIRST_POSTGRES_CANARY_PORT:-5051", "CHAT_FIRST_MYSQL_DISABLED_PORT:-5052"]) {
  assert.ok(canary.includes(port), `missing isolated canary port: ${port}`);
}
assert.equal(
  (compose.match(/NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED: \$\{NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED:-false\}/g) ?? []).length,
  3,
  "both production builds and the runtime environment must receive the explicit false default",
);

for (const required of [
  'profiles: ["chat-first-canary"]',
  "CHAT_FIRST_CANARY_ENABLED:-false",
  "CHAT_FIRST_CANARY_RUNTIME_PROFILE:-sqlite-local",
  "CHAT_FIRST_CANARY_SCAN_MODE:-required",
  "CHAT_FIRST_CANARY_SCANNER:-clamscan",
  "deliberately does not install ClamAV",
]) {
  assert.ok(canary.includes(required), `missing canary safety contract: ${required}`);
}

assert.doesNotMatch(
  canary,
  /CHAT_FIRST_(?:CANARY|POSTGRES)_FRONTEND_ENABLED/,
  "NEXT_PUBLIC flags are build-time image properties, not runtime rollback controls",
);

assert.ok(!/CHAT_FIRST_WORKSPACE_ENABLED:\s*\$\{[^}]*:-true\}/.test(compose));
assert.ok(!/NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED:\s*\$\{[^}]*:-true\}/.test(compose));

for (const required of [
  'profiles: ["chat-first-postgresql-canary"]',
  "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE: postgresql-cluster",
  'profiles: ["chat-first-mysql-disabled"]',
  'CHAT_FIRST_WORKSPACE_ENABLED: "false"',
  'NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED: "false"',
]) {
  assert.ok(canary.includes(required), `missing database profile safety contract: ${required}`);
}

for (const serviceName of [
  "chat-first-canary",
  "chat-first-postgresql-canary",
  "chat-first-mysql-disabled",
]) {
  const start = canary.indexOf(`  ${serviceName}:`);
  assert.notEqual(start, -1, `missing canary service: ${serviceName}`);
  const next = canary.indexOf("\n  chat-first-", start + serviceName.length + 3);
  const block = canary.slice(start, next === -1 ? undefined : next);
  assert.match(block, /\n    image:/, `${serviceName} must select an image`);
  assert.match(
    block,
    /\n      TEMPLATE_V2_DEPLOYMENT_TIER: production/,
    `${serviceName} must exercise production startup safety gates`,
  );
  if (serviceName === "chat-first-mysql-disabled") {
    assert.match(block, /\n    build:/, `${serviceName} must remain buildable with the stock OFF image`);
  } else {
    assert.doesNotMatch(
      block,
      /\n    build:/,
      `${serviceName} must not rebuild the stock scanner-less image`,
    );
  }
}

console.log("chat-first deployment configuration contracts passed");
