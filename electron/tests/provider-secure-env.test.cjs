const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");

const { buildProviderSecureEnv } = require("../app_dist/utils/provider-secure-env.js");
const {
  buildProviderNonSecretStartupConfig,
} = require("../app_dist/utils/provider-startup-config.js");

test("desktop FastAPI launch requires DPAPI references and an app-local vault", () => {
  const root = path.resolve("C:/test-presenton-data");
  const env = buildProviderSecureEnv(root);

  assert.equal(env.PROVIDER_SECURE_MODE, "true");
  assert.equal(env.PROVIDER_SECURE_CREDENTIALS_REQUIRED, "true");
  assert.equal(env.PROVIDER_CREDENTIAL_BACKEND, "desktop_vault");
  assert.equal(
    env.PROVIDER_CREDENTIAL_SCOPE_USER_ID,
    "00000000-0000-4000-8000-000000000001",
  );
  assert.equal(
    env.PROVIDER_DESKTOP_VAULT_DIRECTORY,
    path.join(root, "provider-credential-vault"),
  );
  assert.equal("OPENAI_API_KEY" in env, false);
});

test("relative vault roots are rejected", () => {
  assert.throws(
    () => buildProviderSecureEnv("relative-app-data"),
    /absolute app-data path/,
  );
});

test("desktop startup copies provider options but never plaintext credentials", () => {
  const config = buildProviderNonSecretStartupConfig({
    LLM: "openai",
    OPENAI_MODEL: "safe-model-name",
    OPENAI_API_KEY: "must-not-cross-process-or-persist",
    GOOGLE_API_KEY: "must-not-persist",
    BEDROCK_AWS_SECRET_ACCESS_KEY: "must-not-persist",
    PEXELS_API_KEY: "must-not-persist",
    DATABASE_URL: "postgresql://user:secret@example.invalid/database",
  });

  assert.equal(config.LLM, "openai");
  assert.equal(config.OPENAI_MODEL, "safe-model-name");
  for (const key of [
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "BEDROCK_AWS_SECRET_ACCESS_KEY",
    "PEXELS_API_KEY",
    "DATABASE_URL",
  ]) {
    assert.equal(key in config, false, key);
  }
});
