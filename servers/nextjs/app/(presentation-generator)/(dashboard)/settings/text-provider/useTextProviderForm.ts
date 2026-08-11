import { notify } from "@/components/ui/sonner";
import { getApiUrl } from "@/utils/api";
import { LLM_PROVIDER_OPTIONS } from "@/utils/provider-registry.generated";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ModelOption, OpenAIConfigProps } from "./types";
import { useProviderFields } from "./useProviderFields";

export const useTextProviderForm = ({ onInputChange, llmConfig }: OpenAIConfigProps) => {
  const t = useTranslations("providerSettings");
  const [openProviderSelect, setOpenProviderSelect] = useState(false);
  const [openModelSelect, setOpenModelSelect] = useState(false);
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsChecked, setModelsChecked] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const isFirstRender = useRef(true);
  const credentialSignatureRef = useRef("");
  const configuredProviderId = typeof llmConfig.LLM === "string" ? llmConfig.LLM.trim() : "";
  const configuredProvider = configuredProviderId as keyof typeof LLM_PROVIDER_OPTIONS;
  const providerResolutionError = Boolean(
    configuredProviderId && !LLM_PROVIDER_OPTIONS[configuredProvider]
  );
  // An absent legacy setting retains the documented OpenAI default. An explicit
  // unknown value is never coerced to a different provider.
  const selectedProvider = providerResolutionError
    ? null
    : configuredProviderId
      ? configuredProvider
      : "openai";
  const selectedProviderMeta = selectedProvider
    ? LLM_PROVIDER_OPTIONS[selectedProvider]
    : undefined;
  const { currentModelField, currentApiKeyField, credentialFields, isManualModelProvider } = useProviderFields(selectedProvider);
  const getValue = useCallback(
    (field: string) => ((llmConfig as Record<string, unknown>)[field] as string) || "",
    [llmConfig]
  );
  const currentModel = currentModelField ? getValue(currentModelField) : "";
  const currentApiKey = currentApiKeyField ? getValue(currentApiKeyField) : "";
  const currentCustomUrl = llmConfig.CUSTOM_LLM_URL || "";
  const currentLitellmUrl = (llmConfig.LITELLM_BASE_URL || "").trim();
  const currentOllamaUrl = llmConfig.OLLAMA_URL || "";
  const useCustomOllamaUrl = !!llmConfig.USE_CUSTOM_URL;
  const credentialSignature = JSON.stringify(
    credentialFields.map((credential) => [
      credential.settingKey,
      getValue(credential.settingKey),
    ])
  );
  const modelLabel = selectedProviderMeta?.label || configuredProviderId;
  const providerApiKeyLabel = credentialFields.find((credential) => credential.settingKey === currentApiKeyField)?.label || `${modelLabel} API key`;

  useEffect(() => {
    credentialSignatureRef.current = credentialSignature;
    if (isFirstRender.current) { isFirstRender.current = false; return; }
    setAvailableModels((models) => (models.length ? [] : models));
    setModelsLoading(false);
    setModelsChecked((checked) => (checked ? false : checked));
    if (currentModelField) onInputChange("", currentModelField);
  }, [credentialSignature, currentModelField, onInputChange, selectedProvider]);

  const onApiKeyChange = (_provider: keyof typeof LLM_PROVIDER_OPTIONS, value: string) => {
    if (currentApiKeyField) onInputChange(value, currentApiKeyField);
  };
  const fetchAvailableModels = useCallback(async () => {
    if (isManualModelProvider || selectedProvider === null) return;
    const hasRequiredSecret = credentialFields.some((credential) => credential.required && (credential.kind === "secret" || credential.kind === "oauth"));
    if (hasRequiredSecret && !currentApiKey) return;
    const discoveryCredentialSignature = credentialSignature;
    setModelsLoading(true);
    try {
      let response: Response;
      if (selectedProvider === "google" || selectedProvider === "anthropic") {
        response = await fetch(getApiUrl(`/api/v1/ppt/${selectedProvider}/models/available`), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ api_key: currentApiKey }) });
      } else if (selectedProvider === "ollama") {
        response = await fetch(getApiUrl("/api/v1/ppt/ollama/models/supported"));
      } else {
        const urlCredential = credentialFields.find((credential) => credential.kind === "url");
        const configuredUrl = urlCredential ? getValue(urlCredential.settingKey).trim() : "";
        response = await fetch(getApiUrl("/api/v1/ppt/openai/models/available"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: configuredUrl || selectedProviderMeta?.url || "", api_key: currentApiKey }) });
      }
      if (!response.ok) throw new Error("model discovery failed");
      const data = await response.json();
      if (credentialSignatureRef.current !== discoveryCredentialSignature) return;
      const normalized = Array.isArray(data) ? data.map((model): ModelOption => typeof model === "string" ? { value: model, label: model } : { value: model?.value || model?.label || "", label: model?.label || model?.value || "", size: model?.size }).filter((model) => Boolean(model.value)) : [];
      setAvailableModels(normalized);
      setModelsChecked(true);
      if (normalized.length && currentModelField) onInputChange(normalized.some((model) => model.value === currentModel) ? currentModel : normalized[0].value, currentModelField);
    } catch (error) {
      if (credentialSignatureRef.current !== discoveryCredentialSignature) return;
      console.error("Error fetching models:", error);
      setAvailableModels([]); setModelsChecked(true);
      notify.error(t("modelLoadErrorTitle"), t("modelLoadErrorDescription", { provider: modelLabel }));
    } finally {
      if (credentialSignatureRef.current === discoveryCredentialSignature) {
        setModelsLoading(false);
      }
    }
  }, [credentialFields, credentialSignature, currentApiKey, currentModel, currentModelField, getValue, isManualModelProvider, modelLabel, onInputChange, selectedProvider, selectedProviderMeta?.url, t]);
  useEffect(() => { if (selectedProvider === "ollama" && !modelsChecked && !modelsLoading) void fetchAvailableModels(); }, [selectedProvider, modelsChecked, modelsLoading, fetchAvailableModels]);
  return { openProviderSelect, setOpenProviderSelect, openModelSelect, setOpenModelSelect, availableModels, modelsLoading, modelsChecked, showApiKey, setShowApiKey, selectedProvider, configuredProviderId, providerResolutionError, isManualModelProvider, currentModelField, currentModel, currentApiKey, currentCustomUrl, currentLitellmUrl, currentOllamaUrl, useCustomOllamaUrl, modelLabel, providerApiKeyLabel, credentialFields, onApiKeyChange, fetchAvailableModels };
};
