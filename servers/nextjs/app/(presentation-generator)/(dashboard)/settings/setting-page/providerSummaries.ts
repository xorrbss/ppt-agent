import { LLMConfig } from "@/types/llm_config";
import { IMAGE_PROVIDERS, LLM_PROVIDERS } from "@/utils/providerConstants";

export const getTextSummary = (llmConfig: LLMConfig) => {
  const textProviderKey = llmConfig.LLM || "openai";
  const textProviderLabel =
    LLM_PROVIDERS[textProviderKey]?.label || textProviderKey;
  const selectedTextModel =
    textProviderKey === "openai"
      ? llmConfig.OPENAI_MODEL
      : textProviderKey === "google"
        ? llmConfig.GOOGLE_MODEL
        : textProviderKey === "vertex"
          ? llmConfig.VERTEX_MODEL
          : textProviderKey === "azure"
            ? llmConfig.AZURE_OPENAI_MODEL
          : textProviderKey === "bedrock"
            ? llmConfig.BEDROCK_MODEL
            : textProviderKey === "openrouter"
              ? llmConfig.OPENROUTER_MODEL
              : textProviderKey === "fireworks"
                ? llmConfig.FIREWORKS_MODEL
                : textProviderKey === "together"
                  ? llmConfig.TOGETHER_MODEL
              : textProviderKey === "cerebras"
                ? llmConfig.CEREBRAS_MODEL
                : textProviderKey === "litellm"
                    ? llmConfig.LITELLM_MODEL
                    : textProviderKey === "lmstudio"
                      ? llmConfig.LMSTUDIO_MODEL
                    : textProviderKey === "anthropic"
                      ? llmConfig.ANTHROPIC_MODEL
                      : textProviderKey === "ollama"
                        ? llmConfig.OLLAMA_MODEL
                        : textProviderKey === "custom"
                          ? llmConfig.CUSTOM_MODEL
                          : textProviderKey === "codex"
                            ? llmConfig.CODEX_MODEL
                            : "";
  return selectedTextModel
    ? `${textProviderLabel} (${selectedTextModel})`
    : textProviderLabel;
};

export const getImageSummary = (llmConfig: LLMConfig) =>
  llmConfig.DISABLE_IMAGE_GENERATION
    ? "이미지 생성 비활성화됨"
    : llmConfig.IMAGE_PROVIDER
      ? IMAGE_PROVIDERS[llmConfig.IMAGE_PROVIDER]?.label ||
      llmConfig.IMAGE_PROVIDER
      : "이미지 제공자 없음";
