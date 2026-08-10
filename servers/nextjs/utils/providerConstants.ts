/**
 * Compatibility facade for the pre-Registry settings surface.
 *
 * Provider identity, display metadata and capability policy are generated from
 * `provider_registry.json`.  This file retains only UI shape adapters while
 * settings components are migrated to the capability-aware view model. Remove
 * this facade when that view model consumes server-issued provider metadata.
 */
import {
  LEGACY_PROVIDER_FIELD_BINDINGS,
  PUBLIC_PROVIDER_METADATA,
  type PublicProviderMetadata,
} from "./provider-registry.generated";

export interface ModelOption {
  value: string;
  label: string;
  description?: string;
  icon?: string;
  size: string;
}

export interface ImageProviderOption {
  value: string;
  label: string;
  description?: string;
  icon?: string;
  requiresApiKey?: boolean;
  apiKeyField?: string;
  apiKeyFieldLabel?: string;
  getApiKeyUrl?: string;
}

export interface LLMProviderOption {
  value: string;
  label: string;
  description?: string;
  model_value?: string;
  model_label?: string;
  url?: string;
  icon?: string;
  getApiKeyUrl?: string;
}

export interface WebSearchProviderOption {
  value: string;
  label: string;
  description: string;
  icon?: string;
  apiKeyField?: string;
  apiKeyLabel?: string;
  urlField?: string;
  urlLabel?: string;
}

const defaults: Readonly<Record<string, string>> = {
  openai: "https://api.openai.com/v1",
  google: "https://api.google.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  cerebras: "https://api.cerebras.ai/v1",
  fireworks: "https://api.fireworks.ai/inference/v1",
  together: "https://api.together.ai/v1",
  anthropic: "https://api.anthropic.com/v1",
  lmstudio: "http://localhost:1234/v1",
};

const enabled = (kind: PublicProviderMetadata["kind"]) =>
  PUBLIC_PROVIDER_METADATA.filter((provider) => provider.kind === kind && provider.enabled);

const descriptionFor = (provider: PublicProviderMetadata) =>
  `${provider.displayName} provider managed by the canonical provider registry.`;

const bindingFor = (provider: PublicProviderMetadata) =>
  LEGACY_PROVIDER_FIELD_BINDINGS[provider.id] ?? undefined;

export const LLM_PROVIDERS: Record<string, LLMProviderOption> = Object.freeze(
  Object.fromEntries(
    enabled("llm").map((provider) => [
      provider.id,
      {
        value: provider.id,
        label: provider.displayName,
        description: descriptionFor(provider),
        icon: provider.icon,
        url: defaults[provider.id],
      },
    ])
  )
);

export const IMAGE_PROVIDERS: Record<string, ImageProviderOption> = Object.freeze(
  Object.fromEntries(
    enabled("image").map((provider) => {
      const binding = bindingFor(provider);
      return [
        provider.id,
        {
          value: provider.id,
          label: provider.displayName,
          description: descriptionFor(provider),
          icon: provider.icon,
          requiresApiKey: binding?.requiresSecret,
          apiKeyField: binding?.settingKey,
          apiKeyFieldLabel: `${provider.displayName} API key`,
        },
      ];
    })
  )
);

export const WEB_SEARCH_PROVIDERS: Record<string, WebSearchProviderOption> = Object.freeze(
  Object.fromEntries(
    enabled("web-search").map((provider) => {
      const binding = bindingFor(provider);
      const isUrl = binding?.settingKey.endsWith("_URL");
      return [
        provider.id,
        {
          value: provider.id,
          label: provider.displayName,
          description: descriptionFor(provider),
          icon: provider.icon,
          ...(isUrl
            ? { urlField: binding?.settingKey, urlLabel: `${provider.displayName} base URL` }
            : { apiKeyField: binding?.settingKey, apiKeyLabel: `${provider.displayName} API key` }),
        },
      ];
    })
  )
);

export const DALLE_3_QUALITY_OPTIONS = [
  { label: "Standard", value: "standard", description: "Faster generation with lower cost" },
  { label: "HD", value: "hd", description: "Higher quality with increased cost" },
] as const;

export const GPT_IMAGE_1_5_QUALITY_OPTIONS = [
  { label: "Low", value: "low", description: "Fastest and most cost-effective" },
  { label: "Medium", value: "medium", description: "Balanced quality and speed" },
  { label: "High", value: "high", description: "Best quality with longer generation time" },
] as const;

export const GPT_IMAGE_2_QUALITY_OPTIONS = GPT_IMAGE_1_5_QUALITY_OPTIONS;
