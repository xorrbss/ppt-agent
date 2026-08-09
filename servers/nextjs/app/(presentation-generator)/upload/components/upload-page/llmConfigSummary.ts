import { LLMConfig } from "@/types/llm_config";

export const getSelectedTextModel = (config?: LLMConfig): string => {
  if (!config) return "";
  switch (config.LLM) {
    case "openai":
      return config.OPENAI_MODEL || "";
    case "google":
      return config.GOOGLE_MODEL || "";
    case "vertex":
      return config.VERTEX_MODEL || "";
    case "azure":
      return config.AZURE_OPENAI_MODEL || "";
    case "bedrock":
      return config.BEDROCK_MODEL || "";
    case "openrouter":
      return config.OPENROUTER_MODEL || "";
    case "fireworks":
      return config.FIREWORKS_MODEL || "";
    case "together":
      return config.TOGETHER_MODEL || "";
    case "cerebras":
      return config.CEREBRAS_MODEL || "";
    case "litellm":
      return config.LITELLM_MODEL || "";
    case "lmstudio":
      return config.LMSTUDIO_MODEL || "";
    case "anthropic":
      return config.ANTHROPIC_MODEL || "";
    case "ollama":
      return config.OLLAMA_MODEL || "";
    case "custom":
      return config.CUSTOM_MODEL || "";
    case "codex":
      return config.CODEX_MODEL || "";
    default:
      return "";
  }
};

export const getSelectedImageQuality = (config?: LLMConfig): string => {
  if (!config) return "";
  if (config.IMAGE_PROVIDER === "dall-e-3") return config.DALL_E_3_QUALITY || "";
  if (config.IMAGE_PROVIDER === "gpt-image-1.5") return config.GPT_IMAGE_1_5_QUALITY || "";
  if (config.IMAGE_PROVIDER === "gpt-image-2") return config.GPT_IMAGE_2_QUALITY || "";
  return "";
};
