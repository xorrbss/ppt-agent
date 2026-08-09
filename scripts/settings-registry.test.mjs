import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "..");
const registry = JSON.parse(
  readFileSync(
    join(root, "servers/fastapi/constants/settings_registry.json"),
    "utf8"
  )
);

test("registry provider choices cover the backend enums", () => {
  const byKey = Object.fromEntries(
    registry.settings.map((setting) => [setting.key, setting])
  );
  const llmEnum = readFileSync(
    join(root, "servers/fastapi/enums/llm_provider.py"),
    "utf8"
  );
  const imageEnum = readFileSync(
    join(root, "servers/fastapi/enums/image_provider.py"),
    "utf8"
  );
  for (const choice of byKey.LLM.choices) {
    assert.match(llmEnum, new RegExp(`= ["']${choice.replaceAll("-", "\\-")}["']`));
  }
  for (const choice of byKey.IMAGE_PROVIDER.choices) {
    assert.match(imageEnum, new RegExp(`= ["']${choice.replaceAll("-", "\\-")}["']`));
  }
});

test("generated artifacts contain names but never environment secret values", () => {
  const sentinel = "registry-must-never-render-this-secret";
  process.env.OPENAI_API_KEY = sentinel;
  const generated = [
    readFileSync(join(root, "docker-compose.yml"), "utf8"),
    readFileSync(join(root, "docs/configuration.md"), "utf8"),
  ].join("\n");
  assert.match(generated, /OPENAI_API_KEY/);
  assert.doesNotMatch(generated, new RegExp(sentinel));
});
