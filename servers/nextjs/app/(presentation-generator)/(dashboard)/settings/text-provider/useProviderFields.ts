import { LLM_PROVIDERS } from "@/utils/providerConstants";
import { useMemo } from "react";

export const MANUAL_MODEL_PROVIDERS = new Set(["vertex", "azure", "bedrock"]);

export const useProviderFields = (
  selectedProvider: keyof typeof LLM_PROVIDERS
) => {
  const currentModelField = useMemo(() => {
    switch (selectedProvider) {
      case "openai":
        return "OPENAI_MODEL";
      case "google":
        return "GOOGLE_MODEL";
      case "vertex":
        return "VERTEX_MODEL";
      case "azure":
        return "AZURE_OPENAI_MODEL";
      case "bedrock":
        return "BEDROCK_MODEL";
      case "openrouter":
        return "OPENROUTER_MODEL";
      case "fireworks":
        return "FIREWORKS_MODEL";
      case "together":
        return "TOGETHER_MODEL";
      case "cerebras":
        return "CEREBRAS_MODEL";
      case "litellm":
        return "LITELLM_MODEL";
      case "lmstudio":
        return "LMSTUDIO_MODEL";
      case "anthropic":
        return "ANTHROPIC_MODEL";
      case "ollama":
        return "OLLAMA_MODEL";
      case "custom":
        return "CUSTOM_MODEL";
      case "codex":
        return "CODEX_MODEL";
      default:
        return "";
    }
  }, [selectedProvider]);

  const currentApiKeyField = useMemo(() => {
    switch (selectedProvider) {
      case "openai":
        return "OPENAI_API_KEY";
      case "google":
        return "GOOGLE_API_KEY";
      case "vertex":
        return "VERTEX_API_KEY";
      case "azure":
        return "AZURE_OPENAI_API_KEY";
      case "bedrock":
        return "BEDROCK_API_KEY";
      case "openrouter":
        return "OPENROUTER_API_KEY";
      case "fireworks":
        return "FIREWORKS_API_KEY";
      case "together":
        return "TOGETHER_API_KEY";
      case "cerebras":
        return "CEREBRAS_API_KEY";
      case "litellm":
        return "LITELLM_API_KEY";
      case "lmstudio":
        return "LMSTUDIO_API_KEY";
      case "anthropic":
        return "ANTHROPIC_API_KEY";
      case "custom":
        return "CUSTOM_LLM_API_KEY";
      default:
        return "";
    }
  }, [selectedProvider]);

  return { currentModelField, currentApiKeyField };
};
