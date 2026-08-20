import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = join(root, "servers/fastapi/constants/provider_registry.json");
const pythonPath = join(root, "servers/fastapi/constants/provider_registry_generated.py");
const tsPath = join(root, "servers/nextjs/utils/provider-registry.generated.ts");
const checkOnly = process.argv.includes("--check");
const registry = JSON.parse(readFileSync(sourcePath, "utf8"));
const settings = JSON.parse(readFileSync(join(root, "servers/fastapi/constants/settings_registry.json"), "utf8"));
const settingKeys = new Set(settings.settings.map((setting) => setting.key));
const capabilities = ["webSearch", "vision", "thinking", "structuredOutput", "imageGeneration"];
const kinds = new Set(["llm", "image", "web-search"]);
const states = new Set(["supported", "unsupported", "conditional"]);
const drivers = new Set(["template-v2", "authored"]);
const credentialKinds = new Set(["secret", "oauth", "url", "setting"]);
const widgets = new Set(["oauth-status", "password", "url", "text"]);

function fail(message) { throw new Error(`provider registry: ${message}`); }
if (registry.schemaVersion !== 1 || !Array.isArray(registry.providers)) fail("invalid root");
const ids = new Set();
for (const provider of registry.providers) {
  if (!/^[a-z][a-z0-9._-]*$/.test(provider.id) || ids.has(provider.id)) fail(`invalid or duplicate id ${provider.id}`);
  ids.add(provider.id);
  if (!kinds.has(provider.kind) || typeof provider.displayName !== "string" || !provider.displayName || typeof provider.icon !== "string" || !provider.icon.startsWith("/")) fail(`invalid public fields for ${provider.id}`);
  if (typeof provider.enabled !== "boolean" || !Array.isArray(provider.credentialSchema) || !["none", "remote", "configured", "server-managed"].includes(provider.modelDiscovery)) fail(`invalid runtime fields for ${provider.id}`);
  if (provider.ui !== undefined && (!provider.ui || typeof provider.ui !== "object" || Array.isArray(provider.ui))) fail(`invalid UI metadata for ${provider.id}`);
  for (const credential of provider.credentialSchema) {
    if (!/^[A-Z][A-Z0-9_]*$/.test(credential.settingKey) || !credentialKinds.has(credential.kind) || typeof credential.required !== "boolean") fail(`invalid credential schema for ${provider.id}`);
    if (credential.widget !== undefined && !widgets.has(credential.widget)) fail(`invalid credential widget for ${provider.id}`);
    for (const forbidden of ["value", "default", "secretValue", "token"]) if (forbidden in credential) fail(`credential value in ${provider.id}`);
    if (provider.kind !== "web-search" && !settingKeys.has(credential.settingKey)) fail(`unknown settings registry key ${credential.settingKey} for ${provider.id}`);
  }
  if (!provider.capabilities || Object.keys(provider.capabilities).length !== capabilities.length || capabilities.some((key) => !states.has(provider.capabilities[key]))) fail(`invalid capabilities for ${provider.id}`);
  if (!Array.isArray(provider.presentationDrivers) || provider.presentationDrivers.some((driver) => !drivers.has(driver))) fail(`invalid drivers for ${provider.id}`);
}
for (const settingKey of ["LLM", "IMAGE_PROVIDER"]) {
  const setting = settings.settings.find((entry) => entry.key === settingKey);
  if (!setting || !Array.isArray(setting.choices) || setting.choices.some((id) => !ids.has(id))) fail(`persisted ${settingKey} choices are not represented by providers`);
}

const canonical = `${JSON.stringify(registry, null, 2)}\n`;
const publicProviders = registry.providers.map(({ credentialSchema: _credentials, ...provider }) => provider);
const title = (key) => key.replace(/_(API|ACCESS|SECRET)_KEY$/, "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
const credentialDescriptor = (credential) => ({
  settingKey: credential.settingKey,
  kind: credential.kind,
  required: credential.required,
  widget: credential.widget || (credential.kind === "oauth" ? "oauth-status" : credential.kind === "secret" ? "password" : credential.kind === "url" ? "url" : "text"),
  label: title(credential.settingKey),
  inputType: credential.kind === "secret" || credential.kind === "oauth" ? "password" : credential.kind === "url" ? "url" : "text",
});
const publicCredentialDescriptors = Object.fromEntries(registry.providers.map((provider) => [provider.id, provider.credentialSchema.map(credentialDescriptor)]));
const inferredModelSettingKey = (provider) => {
  if (provider.kind !== "llm") return null;
  if (provider.ui?.modelSettingKey) return provider.ui.modelSettingKey;
  const credential = provider.credentialSchema.find((entry) => entry.kind === "secret" || entry.kind === "url");
  return credential ? credential.settingKey.replace(/_(API_KEY|ACCESS_TOKEN|URL|BASE_URL|ENDPOINT)$/, "_MODEL") : null;
};
const modelBindings = Object.fromEntries(registry.providers.map((provider) => [provider.id, {
  settingKey: inferredModelSettingKey(provider),
  manual: provider.modelDiscovery === "configured" || provider.modelDiscovery === "server-managed",
  discovery: provider.modelDiscovery,
}]));
const defaultEndpoints = {
  openai: "https://api.openai.com/v1", google: "https://api.google.com/v1", openrouter: "https://openrouter.ai/api/v1",
  cerebras: "https://api.cerebras.ai/v1", fireworks: "https://api.fireworks.ai/inference/v1", together: "https://api.together.ai/v1",
  anthropic: "https://api.anthropic.com/v1", lmstudio: "http://localhost:1234/v1",
};

const py = `# Generated by scripts/generate-provider-registry.mjs; do not edit.\nfrom __future__ import annotations\n\nimport json\nfrom types import MappingProxyType\nfrom typing import Any, Mapping\n\ndef _freeze(value: Any) -> Any:\n    if isinstance(value, dict):\n        return MappingProxyType({key: _freeze(item) for key, item in value.items()})\n    if isinstance(value, list):\n        return tuple(_freeze(item) for item in value)\n    return value\n\n_RAW_PROVIDER_REGISTRY = json.loads(r'''${canonical.trim()}''')\nPROVIDER_REGISTRY_SCHEMA_VERSION = 1\nPROVIDER_REGISTRY: tuple[Mapping[str, Any], ...] = tuple(\n    _freeze(item) for item in _RAW_PROVIDER_REGISTRY["providers"]\n)\nPROVIDERS_BY_ID: Mapping[str, Mapping[str, Any]] = MappingProxyType(\n    {item["id"]: item for item in PROVIDER_REGISTRY}\n)\n\ndef public_provider_metadata() -> tuple[Mapping[str, Any], ...]:\n    \"\"\"Return client-safe provider metadata; credential descriptors use no values.\"\"\"\n    return tuple(\n        _freeze({key: value for key, value in item.items() if key != "credentialSchema"})\n        for item in PROVIDER_REGISTRY\n    )\n`;
const ts = `// Generated by scripts/generate-provider-registry.mjs; do not edit.\nexport type ProviderKind = "llm" | "image" | "web-search";\nexport type CapabilityState = "supported" | "unsupported" | "conditional";\nexport type PublicProviderMetadata = Readonly<{ id: string; kind: ProviderKind; displayName: string; icon: string; enabled: boolean; modelDiscovery: "none" | "remote" | "configured" | "server-managed"; capabilities: Readonly<Record<"webSearch" | "vision" | "thinking" | "structuredOutput" | "imageGeneration", CapabilityState>>; presentationDrivers: readonly string[]; ui?: Readonly<{ modelSettingKey?: string; credentialLayout?: string }>; }>;\nexport type PublicCredentialDescriptor = Readonly<{ settingKey: string; kind: "secret" | "oauth" | "url" | "setting"; required: boolean; widget: "oauth-status" | "password" | "url" | "text"; label: string; inputType: "password" | "url" | "text"; }>;\nexport type ProviderModelBinding = Readonly<{ settingKey: string | null; manual: boolean; discovery: PublicProviderMetadata["modelDiscovery"] }>;\nexport type ProviderOption = Readonly<{ value: string; label: string; description: string; icon: string; url?: string; requiresApiKey?: boolean; apiKeyField?: string; apiKeyFieldLabel?: string; apiKeyLabel?: string; urlField?: string; urlLabel?: string; getApiKeyUrl?: string; }>;\nexport const PROVIDER_REGISTRY_SCHEMA_VERSION = 1 as const;\nexport const PUBLIC_PROVIDER_METADATA: readonly PublicProviderMetadata[] = ${JSON.stringify(publicProviders, null, 2)} as const;\nexport const PUBLIC_PROVIDERS_BY_ID: Readonly<Record<string, PublicProviderMetadata>> = Object.freeze(Object.fromEntries(PUBLIC_PROVIDER_METADATA.map((provider) => [provider.id, provider])));\n/** Setting references and widget metadata only; credential values are never emitted. */\nexport const PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS: Readonly<Record<string, readonly PublicCredentialDescriptor[]>> = ${JSON.stringify(publicCredentialDescriptors, null, 2)} as const;\nexport const PROVIDER_MODEL_BINDINGS: Readonly<Record<string, ProviderModelBinding>> = ${JSON.stringify(modelBindings, null, 2)} as const;\nconst DEFAULT_PROVIDER_ENDPOINTS: Readonly<Record<string, string>> = ${JSON.stringify(defaultEndpoints, null, 2)} as const;\nconst providerDescription = (provider: PublicProviderMetadata) => \`${"${provider.displayName}"} provider managed by the canonical provider registry.\`;\nconst providerOptions = (kind: ProviderKind): Readonly<Record<string, ProviderOption>> => Object.freeze(Object.fromEntries(PUBLIC_PROVIDER_METADATA.filter((provider) => provider.kind === kind && provider.enabled).map((provider) => {\n  const credentials = PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS[provider.id] || [];\n  const primarySecret = credentials.find((credential) => credential.kind === "secret" || credential.kind === "oauth");\n  const primaryUrl = credentials.find((credential) => credential.kind === "url");\n  return [provider.id, { value: provider.id, label: provider.displayName, description: providerDescription(provider), icon: provider.icon, ...(DEFAULT_PROVIDER_ENDPOINTS[provider.id] ? { url: DEFAULT_PROVIDER_ENDPOINTS[provider.id] } : {}), ...(kind === "image" ? { requiresApiKey: Boolean(primarySecret), apiKeyField: primarySecret?.settingKey, apiKeyFieldLabel: primarySecret?.label, urlField: primaryUrl?.settingKey, urlLabel: primaryUrl?.label } : {}), ...(kind === "web-search" ? (primaryUrl ? { urlField: primaryUrl.settingKey, urlLabel: primaryUrl.label } : primarySecret ? { apiKeyField: primarySecret.settingKey, apiKeyLabel: primarySecret.label } : {}) : {}) }];\n})));\nexport const LLM_PROVIDER_OPTIONS = providerOptions("llm");\nexport const IMAGE_PROVIDER_OPTIONS = providerOptions("image");\nexport const WEB_SEARCH_PROVIDER_OPTIONS = providerOptions("web-search");\nexport const IMAGE_QUALITY_OPTIONS = Object.freeze({ "dall-e-3": [{ label: "Standard", value: "standard", description: "Faster generation with lower cost" }, { label: "HD", value: "hd", description: "Higher quality with increased cost" }], "gpt-image-1.5": [{ label: "Low", value: "low", description: "Fastest and most cost-effective" }, { label: "Medium", value: "medium", description: "Balanced quality and speed" }, { label: "High", value: "high", description: "Best quality with longer generation time" }], "gpt-image-2": [{ label: "Low", value: "low", description: "Fastest and most cost-effective" }, { label: "Medium", value: "medium", description: "Balanced quality and speed" }, { label: "High", value: "high", description: "Best quality with longer generation time" }] } as const);\n`;
function writeOrCheck(path, content) {
  let current = "";
  try { current = readFileSync(path, "utf8"); } catch { /* generated file missing */ }
  if (checkOnly && current !== content) fail(`generated artifact is stale: ${path}`);
  if (!checkOnly && current !== content) writeFileSync(path, content, "utf8");
}
writeOrCheck(pythonPath, py);
writeOrCheck(tsPath, ts);
