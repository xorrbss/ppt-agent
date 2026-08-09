import { notify } from "@/components/ui/sonner";
import { getApiUrl } from "@/utils/api";
import { LLM_PROVIDERS } from "@/utils/providerConstants";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ModelOption, OpenAIConfigProps } from "./types";
import { MANUAL_MODEL_PROVIDERS, useProviderFields } from "./useProviderFields";

export const useTextProviderForm = ({
  onInputChange,
  llmConfig,
}: OpenAIConfigProps) => {
  const [openProviderSelect, setOpenProviderSelect] = useState(false);
  const [openModelSelect, setOpenModelSelect] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsChecked, setModelsChecked] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const isFirstRender = useRef(true);

  const selectedProvider = (llmConfig.LLM ||
    "openai") as keyof typeof LLM_PROVIDERS;
  const selectedProviderMeta = LLM_PROVIDERS[selectedProvider];
  const isManualModelProvider = MANUAL_MODEL_PROVIDERS.has(selectedProvider);
  const { currentModelField, currentApiKeyField } =
    useProviderFields(selectedProvider);

  const currentModel = currentModelField
    ? ((llmConfig as Record<string, unknown>)[currentModelField] as string) ||
      ""
    : "";
  const currentApiKey = currentApiKeyField
    ? ((llmConfig as Record<string, unknown>)[currentApiKeyField] as string) ||
      ""
    : "";
  const currentCustomUrl = llmConfig.CUSTOM_LLM_URL || "";
  const currentLitellmUrl = (llmConfig.LITELLM_BASE_URL || "").trim();
  const currentLmStudioUrl = (llmConfig.LMSTUDIO_BASE_URL || "").trim();
  const currentFireworksUrl = (llmConfig.FIREWORKS_BASE_URL || "").trim();
  const currentTogetherUrl = (llmConfig.TOGETHER_BASE_URL || "").trim();
  const currentOllamaUrl = llmConfig.OLLAMA_URL || "";
  const useCustomOllamaUrl = !!llmConfig.USE_CUSTOM_URL;
  const modelLabel = selectedProviderMeta?.label || selectedProvider;
  const providerApiKeyLabel =
    selectedProvider === "custom"
      ? "Custom LLM API 키"
      : selectedProvider === "vertex"
      ? "Vertex API 키"
      : selectedProvider === "azure"
      ? "Azure OpenAI API 키"
      : selectedProvider === "bedrock"
      ? "Bedrock API 키 (선택)"
      : selectedProvider === "openrouter"
      ? "OpenRouter API 키"
      : selectedProvider === "fireworks"
      ? "Fireworks API 키"
      : selectedProvider === "together"
      ? "Together API 키"
      : selectedProvider === "cerebras"
      ? "Cerebras API 키"
      : selectedProvider === "litellm"
      ? "LiteLLM API 키 (선택)"
      : selectedProvider === "lmstudio"
      ? "LM Studio API 키 (선택)"
      : `${selectedProvider} API 키`;

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    setAvailableModels((models) => (models.length === 0 ? models : []));
    setModelsChecked((checked) => (checked ? false : checked));
    if (currentModelField) {
      onInputChange("", currentModelField);
    }
  }, [selectedProvider, currentApiKey, currentCustomUrl, currentLitellmUrl, currentLmStudioUrl, currentFireworksUrl, currentTogetherUrl, currentModelField, onInputChange]);

  const onApiKeyChange = (llm: keyof typeof LLM_PROVIDERS, value: string) => {
    if (llm === "ollama") {
      onInputChange(value, "OLLAMA_URL");
      return;
    }

    const keyField =
      llm === "openai"
        ? "OPENAI_API_KEY"
        : llm === "google"
        ? "GOOGLE_API_KEY"
        : llm === "vertex"
        ? "VERTEX_API_KEY"
        : llm === "azure"
        ? "AZURE_OPENAI_API_KEY"
        : llm === "bedrock"
        ? "BEDROCK_API_KEY"
        : llm === "openrouter"
        ? "OPENROUTER_API_KEY"
        : llm === "fireworks"
        ? "FIREWORKS_API_KEY"
        : llm === "together"
        ? "TOGETHER_API_KEY"
        : llm === "cerebras"
        ? "CEREBRAS_API_KEY"
        : llm === "litellm"
        ? "LITELLM_API_KEY"
        : llm === "lmstudio"
        ? "LMSTUDIO_API_KEY"
        : llm === "anthropic"
        ? "ANTHROPIC_API_KEY"
        : llm === "custom"
        ? "CUSTOM_LLM_API_KEY"
        : "";
    if (keyField) {
      onInputChange(value, keyField);
    }
  };

  const fetchAvailableModels = useCallback(async () => {
    if (isManualModelProvider) return;
    if (selectedProvider === "openai" && !currentApiKey) return;
    if (selectedProvider === "google" && !currentApiKey) return;
    if (selectedProvider === "anthropic" && !currentApiKey) return;
    if (selectedProvider === "openrouter" && !currentApiKey) return;
    if (selectedProvider === "fireworks" && !currentApiKey) return;
    if (selectedProvider === "together" && !currentApiKey) return;
    if (selectedProvider === "cerebras" && !currentApiKey) return;
    if (selectedProvider === "custom" && !currentCustomUrl) return;
    if (selectedProvider === "litellm" && !currentLitellmUrl) return;

    setModelsLoading(true);
    try {
      let response: Response;
      if (selectedProvider === "google") {
        response = await fetch(
          getApiUrl("/api/v1/ppt/google/models/available"),
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              api_key: currentApiKey,
            }),
          }
        );
      } else if (selectedProvider === "anthropic") {
        response = await fetch(
          getApiUrl("/api/v1/ppt/anthropic/models/available"),
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              api_key: currentApiKey,
            }),
          }
        );
      } else if (selectedProvider === "ollama") {
        response = await fetch(
          getApiUrl("/api/v1/ppt/ollama/models/supported")
        );
      } else {
        const openAiCompatibleUrl =
          selectedProvider === "custom"
            ? currentCustomUrl
            : selectedProvider === "litellm"
            ? currentLitellmUrl
            : selectedProvider === "lmstudio"
            ? currentLmStudioUrl || selectedProviderMeta?.url || ""
            : selectedProvider === "fireworks"
            ? currentFireworksUrl || selectedProviderMeta?.url || ""
            : selectedProvider === "together"
            ? currentTogetherUrl || selectedProviderMeta?.url || ""
            : selectedProviderMeta?.url || "";
        response = await fetch(
          getApiUrl("/api/v1/ppt/openai/models/available"),
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              url: openAiCompatibleUrl,
              api_key: currentApiKey,
            }),
          }
        );
      }

      if (response.ok) {
        const data = await response.json();
        const normalizedModels: ModelOption[] =
          selectedProvider === "ollama"
            ? Array.isArray(data)
              ? data
                  .map((model) => {
                    if (typeof model === "string") {
                      return {
                        value: model,
                        label: model,
                      };
                    }

                    if (model && typeof model === "object") {
                      const typedModel = model as {
                        value?: string;
                        label?: string;
                        size?: string;
                      };
                      return {
                        value: typedModel.value || typedModel.label || "",
                        label: typedModel.label || typedModel.value || "",
                        size: typedModel.size,
                      };
                    }

                    return {
                      value: "",
                      label: "",
                    };
                  })
                  .filter((model: ModelOption) => Boolean(model.value))
              : []
            : Array.isArray(data)
            ? data
                .filter((model): model is string => typeof model === "string")
                .map((model) => ({
                  value: model,
                  label: model,
                }))
            : [];

        setAvailableModels(normalizedModels);
        setModelsChecked(true);

        if (normalizedModels.length > 0 && currentModelField) {
          const modelValues = normalizedModels.map((model) => model.value);
          if (currentModel && modelValues.includes(currentModel)) {
            onInputChange(currentModel, currentModelField);
            return;
          }

          const preferredDefault =
            selectedProvider === "openai"
              ? "gpt-4.1"
              : selectedProvider === "google"
              ? "models/gemini-2.5-flash"
              : selectedProvider === "anthropic"
              ? "claude-sonnet-4-20250514"
              : selectedProvider === "openrouter"
              ? "openai/gpt-4o"
              : selectedProvider === "fireworks"
              ? "accounts/fireworks/models/llama-v3p1-8b-instruct"
              : selectedProvider === "together"
              ? "openai/gpt-oss-20b"
              : selectedProvider === "cerebras"
              ? "llama-3.3-70b"
              : selectedProvider === "litellm"
              ? "gpt-4.1"
              : selectedProvider === "lmstudio"
              ? "openai/gpt-oss-20b"
              : modelValues[0];

          const nextModel = modelValues.includes(preferredDefault)
            ? preferredDefault
            : modelValues[0];
          onInputChange(nextModel, currentModelField);
        }
      } else {
        console.error("Failed to fetch models");
        setAvailableModels([]);
        setModelsChecked(true);
        notify.error(
          "모델을 불러올 수 없습니다",
          `서버에서 ${modelLabel} 모델 목록을 가져오지 못했습니다. API 키 또는 엔드포인트를 확인한 후 다시 시도하세요.`
        );
      }
    } catch (error) {
      console.error("Error fetching models:", error);
      notify.error(
        "모델을 불러올 수 없습니다",
        "제공자에 연결하는 중 문제가 발생했습니다. 네트워크 상태를 확인한 후 다시 시도하세요."
      );
      setAvailableModels([]);
      setModelsChecked(true);
    } finally {
      setModelsLoading(false);
    }
  }, [
    currentApiKey,
    currentCustomUrl,
    currentFireworksUrl,
    currentLitellmUrl,
    currentLmStudioUrl,
    currentModel,
    currentModelField,
    currentTogetherUrl,
    isManualModelProvider,
    modelLabel,
    onInputChange,
    selectedProvider,
    selectedProviderMeta?.url,
  ]);

  useEffect(() => {
    if (selectedProvider === "ollama" && !modelsChecked && !modelsLoading) {
      fetchAvailableModels();
    }
  }, [selectedProvider, modelsChecked, modelsLoading, fetchAvailableModels]);

  return {
    openProviderSelect,
    setOpenProviderSelect,
    openModelSelect,
    setOpenModelSelect,
    availableModels,
    modelsLoading,
    modelsChecked,
    showApiKey,
    setShowApiKey,
    selectedProvider,
    isManualModelProvider,
    currentModelField,
    currentModel,
    currentApiKey,
    currentCustomUrl,
    currentLitellmUrl,
    currentOllamaUrl,
    useCustomOllamaUrl,
    modelLabel,
    providerApiKeyLabel,
    onApiKeyChange,
    fetchAvailableModels,
  };
};
