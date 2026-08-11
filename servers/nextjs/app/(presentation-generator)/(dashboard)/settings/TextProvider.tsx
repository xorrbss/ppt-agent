import { Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import React from "react";
import ModelSelectField from "./text-provider/ModelSelectField";
import ProviderCredentialFields from "./text-provider/ProviderCredentialFields";
import ProviderSelectField from "./text-provider/ProviderSelectField";
import type { OpenAIConfigProps } from "./text-provider/types";
import { useTextProviderForm } from "./text-provider/useTextProviderForm";

const TextProvider = ({ onInputChange, llmConfig }: OpenAIConfigProps) => {
  const t = useTranslations("providerSettings");
  const {
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
    configuredProviderId,
    providerResolutionError,
    isManualModelProvider,
    currentModelField,
    currentModel,
    modelLabel,
    credentialFields,
    fetchAvailableModels,
  } = useTextProviderForm({ onInputChange, llmConfig });

  return (
    <div className="space-y-6 bg-[#F9F8F8] p-7 rounded-[12px] ">
      {/* API Key Input */}
      <div className="mb-4 flex flex-col gap-8 rounded-[12px] bg-white pt-5 pb-10 px-10 lg:flex-row lg:items-end lg:justify-between lg:gap-6">
        <div className="max-w-[290px] shrink-0 ">
          <div
            className="w-[60px] h-[60px] rounded-[4px] flex items-center justify-center"
            style={{ backgroundColor: "#4C55541A" }}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="32"
              height="32"
              viewBox="0 0 32 32"
              fill="none"
            >
              <path
                d="M15.9459 5.31543V26.5767"
                stroke="#4C5554"
                strokeWidth="1.59459"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M5.31531 9.30192V6.64426C5.31531 6.29183 5.45531 5.95384 5.70451 5.70463C5.95372 5.45543 6.29171 5.31543 6.64414 5.31543H25.2477C25.6002 5.31543 25.9382 5.45543 26.1874 5.70463C26.4366 5.95384 26.5766 6.29183 26.5766 6.64426V9.30192"
                stroke="#4C5554"
                strokeWidth="1.59459"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
              <path
                d="M11.9594 26.5762H19.9324"
                stroke="#4C5554"
                strokeWidth="1.59459"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
          <h3 className="text-xl font-normal text-[#191919] py-2.5">
            텍스트 생성 설정
          </h3>
          <p className=" text-sm  text-gray-500">
            텍스트 콘텐츠를 어디서 생성할지 선택하세요
          </p>
        </div>
        <div className="flex min-w-0 flex-1 flex-col items-stretch justify-end gap-4 sm:items-end">
          <div
            className={`flex w-full min-w-0 flex-wrap gap-4 sm:justify-end ${
              selectedProvider === "codex" ? "items-end" : "items-start"
            }`}
          >
            <ProviderSelectField
              selectedProvider={selectedProvider}
              llmConfig={llmConfig}
              onInputChange={onInputChange}
              openProviderSelect={openProviderSelect}
              setOpenProviderSelect={setOpenProviderSelect}
            />
            <div
              className={`relative flex min-w-0 flex-col  justify-end ${
                selectedProvider === "codex"
                  ? "items-end w-[262px]  max-w-full shrink-0"
                  : "items-end w-[282px]  shrink-0 max-w-full"
              }`}
            >
              {providerResolutionError ? (
                <div
                  role="alert"
                  className="w-full rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
                >
                  <p className="font-semibold">{t("unknownProviderTitle")}</p>
                  <p>
                    {t("unknownProviderDescription", {
                      provider: configuredProviderId,
                    })}
                  </p>
                </div>
              ) : (
                <ProviderCredentialFields
                  selectedProvider={selectedProvider!}
                  llmConfig={llmConfig}
                  onInputChange={onInputChange}
                  credentialFields={credentialFields}
                  showApiKey={showApiKey}
                  setShowApiKey={setShowApiKey}
                />
              )}
              {!providerResolutionError &&
                !isManualModelProvider &&
                selectedProvider !== "codex" &&
                (!modelsChecked ||
                  (modelsChecked && availableModels.length === 0)) && (
                  <button
                    onClick={fetchAvailableModels}
                    disabled={
                      modelsLoading ||
                      credentialFields.some(
                        (credential) =>
                          credential.required &&
                          (credential.kind === "secret" || credential.kind === "oauth") &&
                          !(llmConfig as Record<string, unknown>)[credential.settingKey]
                      )
                    }
                    className={`mt-4 py-2.5 bg-[#EDEEEF] px-3.5 w-fit  rounded-[48px] text-xs font-semibold text-[#101323] transition-all duration-200 border ${
                      modelsLoading
                        ? " border-gray-300 cursor-not-allowed text-gray-500"
                        : " border-[#EDEEEF] text-[#101323] hover:bg-[#E8F0FF]/90 focus:ring-2 focus:ring-blue-500/20"
                    }`}
                  >
                    {modelsLoading ? (
                      <span className="flex items-center justify-center gap-2">
                        <Loader2 className="w-4 h-4 animate-spin" />
                        모델 확인 중...
                      </span>
                    ) : (
                      "모델 확인"
                    )}
                  </button>
                )}
            </div>
          </div>
          {/* Model Selection - only show if models are available */}
          {!providerResolutionError &&
          selectedProvider !== null &&
          !isManualModelProvider &&
          selectedProvider !== "codex" &&
          modelsChecked &&
          availableModels.length > 0 ? (
            <ModelSelectField
              selectedProvider={selectedProvider}
              modelLabel={modelLabel}
              availableModels={availableModels}
              currentModel={currentModel}
              currentModelField={currentModelField}
              onInputChange={onInputChange}
              openModelSelect={openModelSelect}
              setOpenModelSelect={setOpenModelSelect}
            />
          ) : null}
        </div>
      </div>
      {/* Show message if no models found */}
      {!providerResolutionError && modelsChecked && availableModels.length === 0 && (
        <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
          <p className="text-sm text-yellow-800">
            모델을 찾을 수 없습니다. 제공자 인증 정보가 올바른지, 선택한
            제공자에 연결할 수 있는지 확인하세요.
          </p>
        </div>
      )}

      {/* <div className="bg-white flex justify-between items-center p-10 rounded-[12px]">
                <div className=' max-w-[290px]'>

                    <h4 className="text-xl font-normal text-[#191919]">Advanced</h4>
                    <p className="mt-2.5 text-sm  text-gray-500">
                        Configure advanced AI features.
                    </p>
                </div>
                <div className="flex items-center gap-4">
                    <div className="w-[222px]">
                        <div className="flex items-center  mb-4 gap-2.5 ">
                            <Switch
                                checked={!!llmConfig.WEB_GROUNDING}
                                onCheckedChange={(checked) => onInputChange(checked, "WEB_GROUNDING")}
                            />
                            <label className="text-sm font-medium text-gray-700">
                                Enable Web Grounding
                            </label>
                        </div>
                    </div>
                </div>
            </div> */}
    </div>
  );
};

export default TextProvider;
