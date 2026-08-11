import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const registry = JSON.parse(readFileSync(join(root, "servers/fastapi/constants/provider_registry.json"), "utf8"));
const settings = JSON.parse(readFileSync(join(root, "servers/fastapi/constants/settings_registry.json"), "utf8"));
const generated = readFileSync(join(root, "servers/nextjs/utils/provider-registry.generated.ts"), "utf8");

test("provider registry covers persisted LLM and image enums", () => {
  const ids = new Set(registry.providers.map((provider) => provider.id));
  const byKey = Object.fromEntries(settings.settings.map((setting) => [setting.key, setting]));
  for (const id of [...byKey.LLM.choices, ...byKey.IMAGE_PROVIDER.choices]) {
    assert.ok(ids.has(id), `missing canonical provider ${id}`);
  }
});

test("generated client artifact is current and excludes credential schemas", () => {
  assert.doesNotMatch(generated, /credentialSchema/);
  assert.doesNotMatch(generated, /registry-must-never-render-this-secret/);
  assert.match(generated, /PUBLIC_PROVIDER_METADATA/);
});

test("credential schemas contain references only, never credentials", () => {
  const settingsByKey = new Map(settings.settings.map((setting) => [setting.key, setting]));
  for (const provider of registry.providers) {
    for (const credential of provider.credentialSchema) {
      assert.match(credential.settingKey, /^[A-Z][A-Z0-9_]*$/);
      assert.ok(Object.keys(credential).every((key) => ["kind", "required", "settingKey", "widget"].includes(key)));
      if (credential.widget !== undefined) assert.ok(["oauth-status", "password", "url", "text"].includes(credential.widget));
      assert.ok(
        settingsByKey.has(credential.settingKey),
        `provider credential reference is not in settings registry: ${credential.settingKey}`,
      );
      if (credential.kind === "secret") {
        assert.equal(settingsByKey.get(credential.settingKey).secret, true);
      }
    }
  }
});

test("generated credential descriptors preserve every field reference but no value", () => {
  assert.match(generated, /PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS/);
  for (const provider of registry.providers) {
    for (const credential of provider.credentialSchema) {
      assert.match(generated, new RegExp(`"settingKey": "${credential.settingKey}"`));
    }
  }
  assert.doesNotMatch(generated, /secretValue|registry-must-never-render-this-secret/);
});

test("web-search settings and provider registry use the same closed ids", () => {
  const webProviders = registry.providers
    .filter((provider) => provider.kind === "web-search")
    .map((provider) => provider.id);
  const webSetting = settings.settings.find(
    (setting) => setting.key === "WEB_SEARCH_PROVIDER",
  );
  assert.deepEqual(webSetting.choices, webProviders);
});
