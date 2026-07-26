import assert from "node:assert/strict";
import test from "node:test";

import {
  checkReleaseInputs,
  databaseDescriptor,
  parseArgs,
  runOperationalGate,
  validateDeployment,
} from "./operational-release-preflight.mjs";

const managed = {
  DATABASE_URL: "postgresql+psycopg://operator:secret@db.example.invalid/presenton",
  TEMPLATE_V2_DEPLOYMENT_TIER: "staging",
  ENABLE_TEMPLATE_V2: "true",
  TEMPLATE_V2_TEMPLATE_ALLOWLIST: "canary-template",
};

test("managed canary returns a redacted, content-free dry-run plan", () => {
  const result = runOperationalGate(parseArgs(["canary", "--dry-run"]), managed);
  assert.equal(result.database.hostKind, "managed");
  assert.equal(result.database.database, "presenton");
  assert.doesNotMatch(JSON.stringify(result), /operator|secret|db\.example/);
  assert.deepEqual(result.command.slice(-2), ["python", "scripts/check_template_v2_canary.py"]);
});

test("managed mode rejects loopback unless it is an explicit rehearsal", () => {
  const local = { ...managed, DATABASE_URL: "postgresql://user:secret@127.0.0.1/local_test" };
  assert.throws(() => databaseDescriptor(local, false), /rejects loopback/);
  const result = runOperationalGate(
    parseArgs(["canary", "--dry-run", "--allow-local-rehearsal"]),
    local
  );
  assert.equal(result.database.hostKind, "local");
});

test("canary fails closed without flag and allowlist", () => {
  assert.throws(
    () => validateDeployment({ ...managed, ENABLE_TEMPLATE_V2: "false" }, {}, "canary"),
    /ENABLE_TEMPLATE_V2=true/
  );
  assert.throws(
    () => validateDeployment({ ...managed, TEMPLATE_V2_TEMPLATE_ALLOWLIST: "" }, {}, "canary"),
    /non-empty/
  );
});

test("OFF verification requires an explicit false flag and uses health gate", () => {
  assert.throws(
    () => runOperationalGate(parseArgs(["verify-off", "--dry-run"]), managed),
    /requires ENABLE_TEMPLATE_V2=false/
  );
  const result = runOperationalGate(parseArgs(["verify-off", "--dry-run"]), {
    ...managed,
    ENABLE_TEMPLATE_V2: "false",
  });
  assert.deepEqual(result.command.slice(-3), ["scripts/check_template_v2_operations.py", "--mode", "health"]);
});

test("release preflight never returns secret values and blocks required groups", async () => {
  const result = await checkReleaseInputs(
    parseArgs(["release", "--require-signing", "--require-r2"]),
    {
      CSC_LINK: "base64-secret",
      CSC_KEY_PASSWORD: "password-secret",
      R2_ACCOUNT_ID: "account-secret",
      R2_ACCESS_KEY: "access-secret",
      R2_SECRET_KEY: "r2-secret",
    }
  );
  assert.equal(result.status, "blocked");
  assert.match(result.signing.blockers.join(" "), /upstream\/default/);
  assert.doesNotMatch(
    JSON.stringify(result),
    /base64-secret|password-secret|account-secret|access-secret|r2-secret/
  );
});
