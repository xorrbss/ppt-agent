import BedrockManualFields from "@/components/BedrockManualFields";
import VertexAzureManualFields from "@/components/VertexAzureManualFields";
import { LLMConfig } from "@/types/llm_config";
import { LLM_PROVIDERS } from "@/utils/providerConstants";
import { Eye, EyeOff } from "lucide-react";
import React from "react";
import CodexConfig from "../SettingCodex";

interface ProviderCredentialFieldsProps {
  selectedProvider: keyof typeof LLM_PROVIDERS;
  llmConfig: LLMConfig;
  onInputChange: (value: string | boolean, field: string) => void;
  onApiKeyChange: (llm: keyof typeof LLM_PROVIDERS, value: string) => void;
  useCustomOllamaUrl: boolean;
  currentOllamaUrl: string;
  currentApiKey: string;
  currentCustomUrl: string;
  providerApiKeyLabel: string;
  showApiKey: boolean;
  setShowApiKey: React.Dispatch<React.SetStateAction<boolean>>;
}

const ProviderCredentialFields = ({
  selectedProvider,
  llmConfig,
  onInputChange,
  onApiKeyChange,
  useCustomOllamaUrl,
  currentOllamaUrl,
  currentApiKey,
  currentCustomUrl,
  providerApiKeyLabel,
  showApiKey,
  setShowApiKey,
}: ProviderCredentialFieldsProps) => {
  return (
    <div className="flex flex-col justify-start w-full ">
      {selectedProvider === "ollama" ? (
        <>
          {!useCustomOllamaUrl ? (
            <button
              type="button"
              onClick={() => {
                onInputChange(true, "USE_CUSTOM_URL");
                if (!currentOllamaUrl) {
                  onInputChange(
                    "http://localhost:11434",
                    "OLLAMA_URL"
                  );
                }
              }}
              className="mt-8 py-2.5 bg-[#EDEEEF] px-3.5 w-fit rounded-[48px] text-xs font-semibold text-[#101323] transition-all duration-200 border border-[#EDEEEF] hover:bg-[#E8F0FF]/90 focus:ring-2 focus:ring-blue-500/20"
            >
              Ollama URL 사용
            </button>
          ) : (
            <>
              <label className="block text-sm font-medium capitalize text-gray-700 mb-2">
                Ollama URL
              </label>
              <div className="relative">
                <input
                  type="text"
                  value={currentOllamaUrl}
                  onChange={(e) =>
                    onApiKeyChange(selectedProvider, e.target.value)
                  }
                  className="w-full px-2 py-3 outline-none border  border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                  placeholder="http://localhost:11434"
                />
              </div>
              <button
                type="button"
                onClick={() => {
                  onInputChange(false, "USE_CUSTOM_URL");
                  onInputChange(
                    "http://localhost:11434",
                    "OLLAMA_URL"
                  );
                }}
                className="mt-2 text-xs font-medium text-[#4B5563] underline underline-offset-2"
              >
                기본 Ollama URL 사용
              </button>
            </>
          )}
        </>
      ) : selectedProvider === "codex" ? (
        <div className="w-full mt-0 rounded-[12px]  ">
          <CodexConfig
            codexModel={llmConfig.CODEX_MODEL || ""}
            onInputChange={(value, field) => {
              const normalizedField =
                field === "codex_model" ? "CODEX_MODEL" : field;
              onInputChange(value, normalizedField);
            }}
          />
        </div>
      ) : selectedProvider === "bedrock" ? (
        <BedrockManualFields
          llmConfig={llmConfig}
          onPatch={(patch) => {
            for (const [field, value] of Object.entries(patch)) {
              if (value !== undefined)
                onInputChange(value as string, field);
            }
          }}
        />
      ) : (
        <>
          <label className="block text-sm font-medium capitalize text-gray-700 mb-2">
            {providerApiKeyLabel}
          </label>
          <div className="relative">
            <input
              type={showApiKey ? "text" : "password"}
              value={currentApiKey}
              onChange={(e) =>
                onApiKeyChange(selectedProvider, e.target.value)
              }
              className="w-full px-2 py-3 outline-none border  border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
              placeholder={
                selectedProvider === "litellm"
                  ? "프록시에 인증이 필요 없으면 선택 입력입니다"
                  : `${providerApiKeyLabel} 입력`
              }
            />
            <button
              type="button"
              onClick={() => setShowApiKey((prev) => !prev)}
              className="absolute right-2 top-1/2 -translate-y-1/2 bg-white px-2 py-1 cursor-pointer"
            >
              {showApiKey ? (
                <Eye className="w-4 h-4 text-gray-500" />
              ) : (
                <EyeOff className="w-4 h-4 text-gray-500" />
              )}
            </button>
          </div>
        </>
      )}
      {selectedProvider === "custom" && (
        <input
          type="text"
          value={currentCustomUrl}
          onChange={(e) =>
            onInputChange(e.target.value, "CUSTOM_LLM_URL")
          }
          className="w-full mt-2 px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
          placeholder="OpenAI 호환 URL"
        />
      )}
      {selectedProvider === "litellm" && (
        <>
          <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
            LiteLLM 기본 URL
          </label>
          <input
            type="text"
            value={llmConfig.LITELLM_BASE_URL || ""}
            onChange={(e) =>
              onInputChange(e.target.value, "LITELLM_BASE_URL")
            }
            className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
            placeholder="예: http://host.docker.internal:4000/v1"
          />
          <p className="mt-1.5 text-xs text-gray-500">
            OpenAI 호환 루트 주소입니다(보통 /v1로 끝남). /v1을 생략하면
            자동으로 추가됩니다. 인증이 필요 없는 로컬 프록시의 경우
            위 API 키는 선택 사항입니다.
          </p>
        </>
      )}
      {selectedProvider === "lmstudio" && (
        <>
          <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
            LM Studio 기본 URL
          </label>
          <input
            type="text"
            value={llmConfig.LMSTUDIO_BASE_URL || ""}
            onChange={(e) =>
              onInputChange(e.target.value, "LMSTUDIO_BASE_URL")
            }
            className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
            placeholder="http://localhost:1234/v1"
          />
          <p className="mt-1.5 text-xs text-gray-500">
            기본값은 localhost:1234/v1이며, /v1을 생략하면 자동으로
            추가됩니다.
          </p>
        </>
      )}
      {selectedProvider === "fireworks" && (
        <>
          <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
            Fireworks 기본 URL (선택)
          </label>
          <input
            type="text"
            value={llmConfig.FIREWORKS_BASE_URL || ""}
            onChange={(e) =>
              onInputChange(e.target.value, "FIREWORKS_BASE_URL")
            }
            className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
            placeholder="https://api.fireworks.ai/inference/v1"
          />
        </>
      )}
      {selectedProvider === "together" && (
        <>
          <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
            Together 기본 URL (선택)
          </label>
          <input
            type="text"
            value={llmConfig.TOGETHER_BASE_URL || ""}
            onChange={(e) =>
              onInputChange(e.target.value, "TOGETHER_BASE_URL")
            }
            className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
            placeholder="https://api.together.ai/v1"
          />
        </>
      )}
      {(selectedProvider === "vertex" ||
        selectedProvider === "azure") && (
        <VertexAzureManualFields
          key={selectedProvider}
          provider={selectedProvider}
          llmConfig={llmConfig}
          onPatch={(patch) => {
            for (const [field, value] of Object.entries(patch)) {
              if (value !== undefined)
                onInputChange(value as string, field);
            }
          }}
        />
      )}
    </div>
  );
};

export default ProviderCredentialFields;
