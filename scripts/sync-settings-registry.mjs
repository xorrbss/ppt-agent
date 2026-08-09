import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const registryPath = join(
  repoRoot,
  "servers/fastapi/constants/settings_registry.json"
);
const composePath = join(repoRoot, "docker-compose.yml");
const docsPath = join(repoRoot, "docs/configuration.md");
const readmePath = join(repoRoot, "README.md");
const startPath = join(repoRoot, "start.js");
const checkOnly = process.argv.includes("--check-only");

const knownTypes = new Set([
  "string",
  "boolean",
  "integer",
  "number",
  "json",
  "enum",
]);
const knownTargets = new Set([
  "fastapi.runtime",
  "start.process",
  "start.user_config",
  "compose.shared",
  "docs.public",
  "user_config.managed",
  "user_config.ui",
]);
const allowedFields = new Set([
  "key",
  "type",
  "default",
  "secret",
  "targets",
  "group",
  "description",
  "choices",
]);

const fail = (message) => {
  throw new Error(message);
};

const registry = JSON.parse(readFileSync(registryPath, "utf8"));
if (
  registry.schemaVersion !== 1 ||
  !Array.isArray(registry.settings) ||
  Object.keys(registry).sort().join(",") !== "schemaVersion,settings"
) {
  fail("settings registry root is invalid");
}

const seen = new Set();
for (const setting of registry.settings) {
  const fields = Object.keys(setting);
  if (fields.some((field) => !allowedFields.has(field))) {
    fail("settings registry entry contains an unknown field");
  }
  if (!/^[A-Z][A-Z0-9_]*$/.test(setting.key) || seen.has(setting.key)) {
    fail("settings registry contains an invalid or duplicate key");
  }
  seen.add(setting.key);
  if (!knownTypes.has(setting.type)) {
    fail(`settings registry type is invalid for ${setting.key}`);
  }
  if (typeof setting.secret !== "boolean") {
    fail(`settings registry secret flag is invalid for ${setting.key}`);
  }
  if (setting.secret && setting.default !== null) {
    fail(`secret setting must not define a default: ${setting.key}`);
  }
  if (
    !Array.isArray(setting.targets) ||
    setting.targets.length === 0 ||
    setting.targets.some((target) => !knownTargets.has(target)) ||
    new Set(setting.targets).size !== setting.targets.length
  ) {
    fail(`settings registry targets are invalid for ${setting.key}`);
  }
  if (
    setting.type === "enum" &&
    (!Array.isArray(setting.choices) || setting.choices.length === 0)
  ) {
    fail(`enum choices are required for ${setting.key}`);
  }
  if (setting.type !== "enum" && "choices" in setting) {
    fail(`choices are only allowed for enum setting ${setting.key}`);
  }
}

const composeStart = "  # BEGIN GENERATED SETTINGS: compose.shared";
const composeEnd = "  # END GENERATED SETTINGS: compose.shared";

const composeValue = (setting) => {
  if (setting.default === null) return `\${${setting.key}:-}`;
  if (typeof setting.default === "boolean") {
    return `\${${setting.key}:-${setting.default ? "true" : "false"}}`;
  }
  return `\${${setting.key}:-${String(setting.default)}}`;
};

const composeBlock = [
  composeStart,
  ...registry.settings
    .filter((setting) => setting.targets.includes("compose.shared"))
    .map((setting) => `  ${setting.key}: ${composeValue(setting)}`),
  composeEnd,
].join("\n");

const replaceMarkedBlock = (source, start, end, replacement) => {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end);
  if (startIndex < 0 || endIndex < startIndex) {
    fail("generated settings markers are missing from docker-compose.yml");
  }
  return (
    source.slice(0, startIndex) +
    replacement +
    source.slice(endIndex + end.length)
  );
};

const originalCompose = readFileSync(composePath, "utf8");
const renderedCompose = replaceMarkedBlock(
  originalCompose,
  composeStart,
  composeEnd,
  composeBlock
);
const composeOutsideGeneratedBlock = replaceMarkedBlock(
  originalCompose,
  composeStart,
  composeEnd,
  ""
);
for (const setting of registry.settings.filter((entry) =>
  entry.targets.includes("compose.shared")
)) {
  const duplicate = new RegExp(`^\\s+${setting.key}:`, "m");
  if (duplicate.test(composeOutsideGeneratedBlock)) {
    fail(`compose setting is defined outside the generated block: ${setting.key}`);
  }
}

const escapeTable = (value) => String(value).replaceAll("|", "\\|");
const displayDefault = (setting) => {
  if (setting.default === null) return "—";
  if (typeof setting.default === "string") return `\`${setting.default}\``;
  return `\`${String(setting.default)}\``;
};
const docsSettings = registry.settings.filter((setting) =>
  setting.targets.includes("docs.public")
);
const renderedDocs = `${[
  "# Runtime provider settings",
  "",
  "This file is generated from `servers/fastapi/constants/settings_registry.json`.",
  "Do not edit the table by hand; run `npm run generate:settings-registry`.",
  "Secret values are never read or rendered by the generator.",
  "",
  "| Key | Type | Default | Secret | Description |",
  "| --- | --- | --- | --- | --- |",
  ...docsSettings.map(
    (setting) =>
      `| \`${setting.key}\` | ${setting.type} | ${displayDefault(setting)} | ${
        setting.secret ? "yes" : "no"
      } | ${escapeTable(setting.description)} |`
  ),
  "",
].join("\n")}\n`;

const startSource = readFileSync(startPath, "utf8");
for (const requiredSnippet of [
  "constants/settings_registry.json",
  'setting.targets.includes("start.user_config")',
  "for (const setting of userConfigSettings)",
]) {
  if (!startSource.includes(requiredSnippet)) {
    fail("start.js does not consume the settings registry generically");
  }
}

const readme = readFileSync(readmePath, "utf8");
if ((readme.match(/\(docs\/configuration\.md\)/g) || []).length !== 1) {
  fail("README must link to docs/configuration.md exactly once");
}

const docsCurrent = (() => {
  try {
    return readFileSync(docsPath, "utf8");
  } catch {
    return null;
  }
})();
const drift = [];
if (renderedCompose !== originalCompose) drift.push("docker-compose.yml");
if (docsCurrent !== renderedDocs) drift.push("docs/configuration.md");

if (checkOnly) {
  if (drift.length) {
    fail(`settings registry drift: ${drift.join(", ")}`);
  }
  console.log(`settings registry is synchronized (${registry.settings.length} keys)`);
} else {
  if (renderedCompose !== originalCompose) {
    writeFileSync(composePath, renderedCompose, "utf8");
  }
  if (docsCurrent !== renderedDocs) {
    writeFileSync(docsPath, renderedDocs, "utf8");
  }
  console.log(`synchronized settings registry (${registry.settings.length} keys)`);
}
