import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { Button } from '../ui/button';
import { ArrowUpRight, Check, CheckCircle, ChevronLeft, ChevronUp, Download, Eye, EyeOff, Info, Loader2 } from 'lucide-react';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '../ui/command';
import { IMAGE_PROVIDER_OPTIONS, LLM_PROVIDER_OPTIONS, PROVIDER_MODEL_BINDINGS, PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS } from '@/utils/provider-registry.generated';
import { cn } from '@/lib/utils';
import { LLMConfig } from '@/types/llm_config';
import { RootState } from '@/store/store';
import { useSelector } from 'react-redux';
import { notify } from '@/components/ui/sonner';
import ToolTip from '../ToolTip';
import { Switch } from '../ui/switch';
import { getLLMConfigValidationError, handleSaveLLMConfig } from '@/utils/storeHelpers';
import { checkIfSelectedOllamaModelIsPulled, pullOllamaModel } from '@/utils/providerUtils';
import { getApiUrl } from '@/utils/api';
import { isCodexAuthenticated } from '@/app/(presentation-generator)/services/api/codex-auth';
import { openaiAvailableModels } from '@/app/(presentation-generator)/services/api/openai-models';
import { ImageProviderPicker, ImageQualitySelect, MaskedApiKeyInput } from '@/components/provider-settings/ImageProviderFields';
import CodexConfig, { CODEX_GENERATION_PRESETS } from '@/components/codex-config';
import VertexAzureManualFields from '@/components/VertexAzureManualFields';
import BedrockManualFields from '@/components/BedrockManualFields';
import OpenAICompatibleImageFields from '@/components/OpenAICompatibleImageFields';
import Image from 'next/image';

const PresentonMode = ({ currentStep, setStep }: { currentStep: number, setStep: (step: number) => void }) => {
    const t = useTranslations("a2cOnboarding");
    const [openProviderSelect, setOpenProviderSelect] = useState(false);
    const [openImageProviderSelect, setOpenImageProviderSelect] = useState(false);
    const userConfigState = useSelector((state: RootState) => state.userConfig);

    const [showApiKey, setShowApiKey] = useState(false);
    const [availableModels, setAvailableModels] = useState<string[]>([]);
    const [openModelSelect, setOpenModelSelect] = useState(false);
    const [modelsLoading, setModelsLoading] = useState(false);
    const [modelsChecked, setModelsChecked] = useState(false);
    const [showDownloadModal, setShowDownloadModal] = useState(false);
    const [savingConfig, setSavingConfig] = useState(false);
    const [llmConfig, setLlmConfig] = useState<LLMConfig>(
        userConfigState.llm_config
    );
    const [downloadingModel, setDownloadingModel] = useState<{
        name: string;
        size: number | null;
        downloaded: number | null;
        status: string;
        done: boolean;
    } | null>(null);
    const providerBinding = PROVIDER_MODEL_BINDINGS[llmConfig.LLM || ""];
    const isManualModelProvider = providerBinding?.manual ?? true;

    const handleProviderChange = (provider: string) => {
        setLlmConfig(prev => ({
            ...prev,
            LLM: provider
        }));
        setOpenProviderSelect(false);
        setAvailableModels([]);
        setModelsChecked(false);
        if (currentModelField) {
            setLlmConfig(prev => ({
                ...prev,
                [currentModelField]: ''
            }));
        }
    };

    const currentModelField = providerBinding?.settingKey || '';
    const currentApiKeyField = (PUBLIC_PROVIDER_CREDENTIAL_DESCRIPTORS[llmConfig.LLM || ""] || []).find(
        (credential) => credential.kind === "secret" || credential.kind === "oauth"
    )?.settingKey || '';



    const getFieldValue = (field?: string) => {
        if (!field) return "";
        return (llmConfig as Record<string, string | undefined>)[field] || "";
    };

    const currentApiKey = currentApiKeyField ? ((llmConfig as Record<string, unknown>)[currentApiKeyField] as string || '') : '';
    const currentModel = currentModelField ? ((llmConfig as Record<string, unknown>)[currentModelField] as string || '') : '';
    const currentLitellmUrl = (llmConfig.LITELLM_BASE_URL || '').trim();
    const currentLmStudioUrl = (llmConfig.LMSTUDIO_BASE_URL || '').trim();
    const currentFireworksUrl = (llmConfig.FIREWORKS_BASE_URL || '').trim();
    const currentTogetherUrl = (llmConfig.TOGETHER_BASE_URL || '').trim();
    const currentOllamaUrl = llmConfig.OLLAMA_URL || '';
    const useCustomOllamaUrl = !!llmConfig.USE_CUSTOM_URL;
    const providerApiKeyLabel =
        llmConfig.LLM === 'custom'
            ? 'Custom LLM API 키'
            : llmConfig.LLM === 'vertex'
                ? 'Vertex API 키'
                : llmConfig.LLM === 'azure'
                    ? 'Azure OpenAI API 키'
                    : llmConfig.LLM === 'bedrock'
                        ? 'Bedrock API 키 (선택 사항)'
                    : llmConfig.LLM === 'openrouter'
                        ? 'OpenRouter API 키'
                        : llmConfig.LLM === 'fireworks'
                            ? 'Fireworks API 키'
                            : llmConfig.LLM === 'together'
                                ? 'Together API 키'
                        : llmConfig.LLM === 'cerebras'
                            ? 'Cerebras API 키'
                            : llmConfig.LLM === 'litellm'
                                ? 'LiteLLM API 키 (선택 사항)'
                                : llmConfig.LLM === 'lmstudio'
                                    ? 'LM Studio API 키 (선택 사항)'
                                : `${llmConfig.LLM} API 키`;

    const fetchAvailableModels = useCallback(async () => {
        if (isManualModelProvider) return;
        if (llmConfig.LLM === 'openai' && !currentApiKey) return;
        if (llmConfig.LLM === 'google' && !currentApiKey) return;
        if (llmConfig.LLM === 'anthropic' && !currentApiKey) return;
        if (llmConfig.LLM === 'openrouter' && !currentApiKey) return;
        if (llmConfig.LLM === 'fireworks' && !currentApiKey) return;
        if (llmConfig.LLM === 'together' && !currentApiKey) return;
        if (llmConfig.LLM === 'cerebras' && !currentApiKey) return;
        if (llmConfig.LLM === 'custom' && !llmConfig.CUSTOM_LLM_URL) return;
        if (llmConfig.LLM === 'litellm' && !currentLitellmUrl) return;
        setModelsLoading(true);
        try {
            let response: Response;
            if (llmConfig.LLM === 'google') {
                response = await fetch(getApiUrl('/api/v1/ppt/google/models/available'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        api_key: currentApiKey
                    }),
                });
            } else if (llmConfig.LLM === 'anthropic') {
                response = await fetch(getApiUrl('/api/v1/ppt/anthropic/models/available'), {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        api_key: currentApiKey
                    }),
                });
            } else if (llmConfig.LLM === 'ollama') {
                response = await fetch(getApiUrl('/api/v1/ppt/ollama/models/supported'));
            } else {
                const openAiCompatibleUrl =
                    llmConfig.LLM === 'custom'
                        ? llmConfig.CUSTOM_LLM_URL
                        : llmConfig.LLM === 'litellm'
                            ? currentLitellmUrl
                            : llmConfig.LLM === 'lmstudio'
                                ? currentLmStudioUrl || LLM_PROVIDER_OPTIONS[llmConfig.LLM!]?.url || ''
                            : llmConfig.LLM === 'fireworks'
                                ? currentFireworksUrl || LLM_PROVIDER_OPTIONS[llmConfig.LLM!]?.url || ''
                                : llmConfig.LLM === 'together'
                                    ? currentTogetherUrl || LLM_PROVIDER_OPTIONS[llmConfig.LLM!]?.url || ''
                            : LLM_PROVIDER_OPTIONS[llmConfig.LLM!]?.url || '';
                response = await openaiAvailableModels({
                    url: openAiCompatibleUrl,
                    apiKey: currentApiKey,
                });
            }

            if (response.ok) {
                const data = await response.json();
                const normalizedModels: string[] = llmConfig.LLM === 'ollama'
                    ? Array.isArray(data)
                        ? data.map((model: { value?: string; label?: string }) => model.value || model.label || '').filter(Boolean)
                        : []
                    : Array.isArray(data)
                        ? data
                        : [];

                setAvailableModels(normalizedModels);
                setModelsChecked(true);

                if (normalizedModels.length > 0 && currentModelField) {
                    const configValues = llmConfig as Record<string, string | undefined>;
                    if (configValues[currentModelField] && normalizedModels.includes(configValues[currentModelField]!)) {
                        setLlmConfig(prev => ({
                            ...prev,
                            [currentModelField]: configValues[currentModelField]
                        }));
                        return;
                    }

                    const nextModel = normalizedModels[0];
                    setLlmConfig(prev => ({
                        ...prev,
                        [currentModelField]: nextModel
                    }));
                }
            } else {
                console.error('Failed to fetch models');
                setAvailableModels([]);
                setModelsChecked(true);
                notify.error(t("modelLoadFailed"), t("providerModelListFailed", { provider: LLM_PROVIDER_OPTIONS[llmConfig.LLM!]?.label ?? "" }));
            }
        } catch (error) {
            console.error('Error fetching models:', error);
            notify.error(t("modelLoadFailed"), t("modelListFailed"));
            setAvailableModels([]);
            setModelsChecked(true);
        } finally {
            setModelsLoading(false);
        }
    }, [
        currentApiKey,
        currentFireworksUrl,
        currentLitellmUrl,
        currentLmStudioUrl,
        currentModelField,
        currentTogetherUrl,
        isManualModelProvider,
        llmConfig,
    ]);

    const renderQualitySelector = (llmConfig: LLMConfig) => (
        <ImageQualitySelect
            llmConfig={llmConfig}
            variant="onboarding"
            onChange={(field, value) => setLlmConfig((prev) => ({ ...prev, [field]: value }))}
            labels={{
                dalle3: t("dalleQuality"),
                gptImage15: t("gptImageQuality"),
                qualitySuffix: t("quality"),
                placeholder: t("selectQuality"),
            }}
        />
    );
    const handleModelDownload = async () => {
        try {
            await pullOllamaModel(llmConfig.OLLAMA_MODEL!, setDownloadingModel);
        }
        finally {
            setDownloadingModel(null);
            setShowDownloadModal(false);
        }
    };
    const handleSaveConfig = async () => {
        try {
            if (llmConfig.LLM === 'codex') {
                const isAuthenticated = await isCodexAuthenticated();
                if (!isAuthenticated) {
                    notify.error(t("signInRequired"), t("signInToContinue"));
                    return;
                }
            }
            const validationError = getLLMConfigValidationError(llmConfig);
            if (validationError) {
                notify.warning(t("cannotSaveYet"), validationError);
                return;
            }
            setSavingConfig(true);

            await handleSaveLLMConfig(llmConfig);

            if (llmConfig.LLM === "ollama" && llmConfig.OLLAMA_MODEL) {
                const isPulled = await checkIfSelectedOllamaModelIsPulled(llmConfig.OLLAMA_MODEL);
                if (!isPulled) {
                    setShowDownloadModal(true);
                    await handleModelDownload();
                }
            }

            notify.success(t("settingsSaved"), t("settingsSavedDescription"));
            setStep(3)
            // router.push("/upload");
        } catch (error) {
            notify.error(t("settingsSaveFailed"), error instanceof Error ? error.message : "설정 저장에 실패했습니다");

        }
        finally {
            setSavingConfig(false);
        }
    };

    const downloadProgress = useMemo(() => {
        if (downloadingModel && downloadingModel.downloaded !== null && downloadingModel.size !== null) {
            return Math.round((downloadingModel.downloaded / downloadingModel.size) * 100);
        }
        return 0;
    }, [downloadingModel]);

    useEffect(() => {
        if (llmConfig.LLM === 'ollama' && !modelsChecked && !modelsLoading) {
            void fetchAvailableModels();
        }
    }, [fetchAvailableModels, llmConfig.LLM, modelsChecked, modelsLoading]);

    return (
        <div className='w-full min-w-0 max-w-[660px] pb-10 font-syne'>
            <p className='px-2.5 py-0.5 w-fit text-brand-accent rounded-[50px]  border border-surface text-[10px] font-medium mb-5 font-syne'>PRESENTON</p>
            <div className=''>

                <h2 className='mb-4 text-black text-[26px] font-normal font-unbounded '>{t("chooseContentProvider")}</h2>
                <p className='text-[#000000CC] text-xl font-normal font-syne'>{t("chooseAiEngine")}</p>
            </div>
            <div className='flex items-center gap-2 bg-[#F0F3F9B2] rounded-[8px]  px-6 py-2.5 my-[54px]'>
                <Info className='w-4 h-4 fill-[#003399] stroke-white' />
                <p className='text-sm text-[#5F6062] font-medium'>{t("localStorageInfo")}</p>
            </div>

            {/* Text Provider */}
            <div className='p-3 border border-surface rounded-[11px] bg-white '>
                <div className="flex items-center gap-[24.3px]  mb-[42px]">
                    <div className='w-[74px] h-[74px] rounded-[4px] pt-[16.8px] pr-[17.15px] pb-[17.2px] pl-[16.85px] flex items-center justify-center'
                        style={{ backgroundColor: '#4C55541A' }}
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40" fill="none">
                            <path d="M20 6.6665V33.3332" stroke="#4C5554" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                            <path d="M6.66666 11.6665V8.33317C6.66666 7.89114 6.84225 7.46722 7.15481 7.15466C7.46737 6.8421 7.8913 6.6665 8.33332 6.6665H31.6667C32.1087 6.6665 32.5326 6.8421 32.8452 7.15466C33.1577 7.46722 33.3333 7.89114 33.3333 8.33317V11.6665" stroke="#4C5554" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                            <path d="M15 33.3335H25" stroke="#4C5554" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </div>
                    <div className='w-full'>

                        <h3 className="text-xl font-normal text-[#191919] pb-1.5">{t("textGenerationSettings")}</h3>
                        <p className=" text-sm  text-gray-500">
                            {t("chooseTextLocation")}
                        </p>
                    </div>
                </div>
                <CodexConfig
                    codexModel={llmConfig.CODEX_MODEL || ''}
                    codexReasoningEffort={llmConfig.CODEX_REASONING_EFFORT || ''}
                    onInputChange={(value, field) => {
                        const normalizedField = field === 'codex_model'
                            ? 'CODEX_MODEL'
                            : field === 'codex_reasoning_effort'
                                ? 'CODEX_REASONING_EFFORT'
                                : field;
                        setLlmConfig(prev => ({
                            ...prev,
                            [normalizedField]: value
                        }));
                    }}
                />
                <div className='flex items-center gap-2.5 my-[30px]'>
                    <div className='w-full h-[1px] bg-[#E1E1E5]' />
                    <p className='text-xs font-normal text-[#999999]'>{t("or")}</p>
                    <div className='w-full h-[1px] bg-[#E1E1E5]' />
                </div>
                <div className="flex w-full max-w-[222px] flex-col items-start gap-4">
                    <div className="flex w-full flex-col justify-start">

                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            {t("chooseTextProvider")}
                        </label>
                        <Popover
                            open={openProviderSelect}
                            onOpenChange={setOpenProviderSelect}
                        >
                            <PopoverTrigger asChild>
                                <Button
                                    variant="outline"
                                    role="combobox"
                                    aria-expanded={openProviderSelect}
                                    className="flex h-12 w-full px-4 py-4 outline-none border border-[#E8E8E9] rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between"
                                >
                                    <div className="flex gap-3 items-center">
                                        <span className="text-sm font-medium text-gray-900">
                                            {llmConfig.LLM
                                                ? LLM_PROVIDER_OPTIONS[llmConfig.LLM]
                                                    ?.label || llmConfig.LLM
                                                : t("chooseTextProvider")}
                                        </span>
                                    </div>
                                    <ChevronUp className="w-4 h-4 text-gray-500" />
                                </Button>
                            </PopoverTrigger>
                            <PopoverContent
                                className="p-0 w-full "
                                align="end"

                            >
                                <Command>
                                    <CommandInput placeholder={t("searchProvider")} />
                                    <CommandList className='hide-scrollbar'>
                                        <CommandEmpty>{t("providerNotFound")}</CommandEmpty>
                                        <CommandGroup >
                                            {Object.values(LLM_PROVIDER_OPTIONS).map(
                                                (provider, index) => (
                                                    <CommandItem
                                                        key={index}
                                                        value={provider.value}
                                                        onSelect={() => handleProviderChange(provider.value)}
                                                    >
                                                        <Check
                                                            className={cn(
                                                                "mr-2 h-4 w-4",
                                                                llmConfig.LLM === provider.value
                                                                    ? "opacity-100"
                                                                    : "opacity-0"
                                                            )}
                                                        />
                                                        <div className="flex gap-3 items-center">
                                                            <div className="flex flex-col space-y-1 flex-1">
                                                                <div className="flex items-center justify-between gap-2">
                                                                    <span className="text-sm font-medium text-gray-900 capitalize">
                                                                        {provider.label}
                                                                    </span>
                                                                </div>
                                                                <span className="text-xs text-gray-600 leading-relaxed">
                                                                    {provider.description}
                                                                </span>
                                                            </div>
                                                        </div>
                                                    </CommandItem>
                                                )
                                            )}
                                        </CommandGroup>
                                    </CommandList>
                                </Command>
                            </PopoverContent>
                        </Popover>
                    </div>
                    <div className="relative flex w-full flex-col justify-end items-start">
                        <div className="flex flex-col justify-start w-full ">
                            {llmConfig.LLM === 'ollama' ? (
                                <>
                                    {!useCustomOllamaUrl ? (
                                        <button
                                            type="button"
                                            onClick={() => setLlmConfig(prev => ({
                                                ...prev,
                                                USE_CUSTOM_URL: true,
                                                OLLAMA_URL: prev.OLLAMA_URL || 'http://localhost:11434'
                                            }))}
                                            className="py-2.5 bg-surface px-3.5 w-fit rounded-[48px] text-xs font-semibold text-[#101323] transition-all duration-200 border border-surface hover:bg-[#E8F0FF]/90 focus:ring-2 focus:ring-blue-500/20"
                                        >
                                            {t("useOllamaUrl")}
                                        </button>
                                    ) : (
                                        <>
                                            <label className="block text-sm font-medium capitalize text-gray-700 mb-2">
                                                {t("ollamaUrl")}
                                            </label>
                                            <div className="relative">
                                                <input
                                                    type="text"
                                                    value={currentOllamaUrl}
                                                    onChange={(e) => setLlmConfig(prev => ({
                                                        ...prev,
                                                        OLLAMA_URL: e.target.value
                                                    }))}
                                                    className="w-full px-2 py-3 outline-none border  border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                                    placeholder="http://localhost:11434"
                                                />
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => setLlmConfig(prev => ({
                                                    ...prev,
                                                    USE_CUSTOM_URL: false,
                                                    OLLAMA_URL: 'http://localhost:11434'
                                                }))}
                                                className="mt-2 text-xs font-medium text-[#4B5563] underline underline-offset-2"
                                            >
                                                {t("useDefaultOllamaUrl")}
                                            </button>
                                        </>
                                    )}
                                </>
                            ) : llmConfig.LLM === 'chatgpt' || llmConfig.LLM === 'codex' ? (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        {t("modelAndReasoning")}
                                    </label>
                                    <Popover open={openModelSelect} onOpenChange={setOpenModelSelect}>
                                        <PopoverTrigger asChild>
                                            <Button
                                                variant="outline"
                                                role="combobox"
                                                aria-expanded={openModelSelect}
                                                className="w-full h-12 px-3 outline-none border border-gray-300 rounded-lg hover:border-gray-400 justify-between"
                                            >
                                                <span className="text-sm text-gray-900">
                                                    {llmConfig.CODEX_MODEL
                                                        ? (CODEX_GENERATION_PRESETS.find((preset) =>
                                                            preset.modelId === llmConfig.CODEX_MODEL
                                                            && preset.effort === llmConfig.CODEX_REASONING_EFFORT
                                                        )?.name ?? `${llmConfig.CODEX_MODEL}${llmConfig.CODEX_REASONING_EFFORT ? ` · ${llmConfig.CODEX_REASONING_EFFORT}` : ''}`)
                                                        : t("selectModel")}
                                                </span>
                                                <ChevronUp className="w-4 h-4 text-gray-400" />
                                            </Button>
                                        </PopoverTrigger>
                                        <PopoverContent
                                            className="p-0"
                                            align="start"
                                            style={{ width: "var(--radix-popover-trigger-width)" }}
                                        >
                                            <Command>
                                                <CommandInput placeholder={t("searchModel")} />
                                                <CommandList>
                                                    <CommandEmpty>{t("modelNotFound")}</CommandEmpty>
                                                    <CommandGroup>
                                                        {CODEX_GENERATION_PRESETS.map((preset) => (
                                                            <CommandItem
                                                                key={`${preset.modelId}:${preset.effort}`}
                                                                value={`${preset.modelId} ${preset.effort}`}
                                                                onSelect={() => {
                                                                    setLlmConfig(prev => ({
                                                                        ...prev,
                                                                        CODEX_MODEL: preset.modelId,
                                                                        CODEX_REASONING_EFFORT: preset.effort
                                                                    }));
                                                                    setOpenModelSelect(false);
                                                                }}
                                                            >
                                                                <Check
                                                                    className={cn(
                                                                        "mr-2 h-4 w-4",
                                                                        llmConfig.CODEX_MODEL === preset.modelId
                                                                            && llmConfig.CODEX_REASONING_EFFORT === preset.effort
                                                                            ? "opacity-100"
                                                                            : "opacity-0"
                                                                    )}
                                                                />
                                                                <span className="text-sm text-gray-900">
                                                                    {preset.name}
                                                                </span>
                                                            </CommandItem>
                                                        ))}
                                                    </CommandGroup>
                                                </CommandList>
                                            </Command>
                                        </PopoverContent>
                                    </Popover>
                                </div>
                            ) : llmConfig.LLM === 'bedrock' ? (
                                <BedrockManualFields
                                    llmConfig={llmConfig}
                                    onPatch={(patch) => {
                                        setLlmConfig((prev) => ({ ...prev, ...patch }));
                                    }}
                                />
                            ) : (
                                <>
                                    <div className='flex items-center justify-between mb-2'>

                                        <label className="block text-sm font-medium capitalize text-gray-700 ">
                                            {providerApiKeyLabel}
                                        </label>
                                    </div>

                                    <div className="relative">
                                        <input
                                            type={showApiKey ? 'text' : 'password'}
                                            value={currentApiKey}
                                            onChange={(e) => setLlmConfig(prev => ({
                                                ...prev,
                                                [currentApiKeyField]: e.target.value
                                            }))}
                                            className="w-full px-2 py-3 outline-none border  border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            placeholder={t("enterField", { field: providerApiKeyLabel })}
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowApiKey((prev) => !prev)}
                                            className='absolute right-2 top-1/2 -translate-y-1/2 bg-white px-2 py-1 cursor-pointer'
                                        >
                                            {showApiKey ? <Eye className='w-4 h-4 text-gray-500' /> : <EyeOff className='w-4 h-4 text-gray-500' />}
                                        </button>
                                    </div>
                                </>
                            )}
                            {llmConfig.LLM === 'custom' && (
                                <input
                                    type="text"
                                    value={llmConfig.CUSTOM_LLM_URL}
                                    onChange={(e) => setLlmConfig(prev => ({
                                        ...prev,
                                        CUSTOM_LLM_URL: e.target.value
                                    }))}
                                    className="w-full mt-2 px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                    placeholder={t("openAiCompatibleUrl")}
                                />
                            )}
                            {llmConfig.LLM === 'litellm' && (
                                <>
                                    <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
                                        {t("litellmDefaultUrl")}
                                    </label>
                                    <input
                                        type="text"
                                        value={llmConfig.LITELLM_BASE_URL || ''}
                                        onChange={(e) => setLlmConfig(prev => ({
                                            ...prev,
                                            LITELLM_BASE_URL: e.target.value
                                        }))}
                                        className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                        placeholder={t("urlExample")}
                                    />
                                    <p className="mt-1.5 text-xs text-gray-500">
                                        {t("openAiCompatibleHint")}
                                    </p>
                                </>
                            )}
                            {llmConfig.LLM === 'lmstudio' && (
                                <>
                                    <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
                                        {t("lmStudioDefaultUrl")}
                                    </label>
                                    <input
                                        type="text"
                                        value={llmConfig.LMSTUDIO_BASE_URL || ''}
                                        onChange={(e) => setLlmConfig(prev => ({
                                            ...prev,
                                            LMSTUDIO_BASE_URL: e.target.value
                                        }))}
                                        className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                        placeholder="http://localhost:1234/v1"
                                    />
                                    <p className="mt-1.5 text-xs text-gray-500">
                                        {t("lmStudioHint")}
                                    </p>
                                </>
                            )}
                            {llmConfig.LLM === 'fireworks' && (
                                <>
                                    <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
                                        {t("fireworksOptionalUrl")}
                                    </label>
                                    <input
                                        type="text"
                                        value={llmConfig.FIREWORKS_BASE_URL || ''}
                                        onChange={(e) => setLlmConfig(prev => ({
                                            ...prev,
                                            FIREWORKS_BASE_URL: e.target.value
                                        }))}
                                        className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                        placeholder="https://api.fireworks.ai/inference/v1"
                                    />
                                </>
                            )}
                            {llmConfig.LLM === 'together' && (
                                <>
                                    <label className="mt-3 block text-sm font-medium text-gray-700 mb-2">
                                        {t("togetherOptionalUrl")}
                                    </label>
                                    <input
                                        type="text"
                                        value={llmConfig.TOGETHER_BASE_URL || ''}
                                        onChange={(e) => setLlmConfig(prev => ({
                                            ...prev,
                                            TOGETHER_BASE_URL: e.target.value
                                        }))}
                                        className="w-full px-2 py-3 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                        placeholder="https://api.together.ai/v1"
                                    />
                                </>
                            )}
                            {(llmConfig.LLM === 'vertex' || llmConfig.LLM === 'azure') && (
                                <VertexAzureManualFields
                                    key={llmConfig.LLM}
                                    provider={llmConfig.LLM === 'vertex' ? 'vertex' : 'azure'}
                                    llmConfig={llmConfig}
                                    onPatch={(patch) => {
                                        setLlmConfig((prev) => ({ ...prev, ...patch }));
                                    }}
                                />
                            )}
                        </div>


                        {!isManualModelProvider && llmConfig.LLM !== 'ollama' && llmConfig.LLM !== 'chatgpt' && llmConfig.LLM !== 'codex' && (!modelsChecked || (modelsChecked && availableModels.length === 0)) && (

                            <button
                                onClick={fetchAvailableModels}
                                disabled={
                                    modelsLoading ||
                                    (llmConfig.LLM === 'openai' && !currentApiKey) ||
                                    (llmConfig.LLM === 'google' && !currentApiKey) ||
                                    (llmConfig.LLM === 'anthropic' && !currentApiKey) ||
                                    (llmConfig.LLM === 'openrouter' && !currentApiKey) ||
                                    (llmConfig.LLM === 'fireworks' && !currentApiKey) ||
                                    (llmConfig.LLM === 'together' && !currentApiKey) ||
                                    (llmConfig.LLM === 'cerebras' && !currentApiKey) ||
                                    (llmConfig.LLM === 'custom' && !llmConfig.CUSTOM_LLM_URL) ||
                                    (llmConfig.LLM === 'litellm' && !currentLitellmUrl)
                                }
                                className={`mt-4 py-2.5 bg-surface disabled:opacity-50 disabled:cursor-not-allowed px-3.5 w-full  rounded-[48px] text-xs font-semibold text-[#101323] transition-all duration-200 border ${modelsLoading
                                    ? " border-gray-300 cursor-not-allowed text-gray-500"
                                    : " border-surface text-[#101323] hover:bg-surface/90 focus:ring-2 focus:ring-blue-500/20"
                                    }`}
                            >
                                {modelsLoading ? (
                                    <span className="flex items-center justify-center gap-2">
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                        {t("checkingModels")}
                                    </span>
                                ) : (
                                    t("loadModelsAfterValidation")
                                )}
                            </button>
                        )}
                    </div>

                </div>
                <div className="mt-4 flex w-full max-w-[222px] items-start gap-4">


                    {/* Model Selection - only show if models are available */}
                    {!isManualModelProvider && llmConfig.LLM !== 'chatgpt' && llmConfig.LLM !== 'codex' && modelsChecked && availableModels.length > 0 && (
                        <div className="w-full">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    {llmConfig.LLM === 'ollama' ? t("selectSupportedModel") : t("selectProviderModel", { provider: LLM_PROVIDER_OPTIONS[llmConfig.LLM!]?.label ?? "" })}
                                </label>
                                <div className="w-full">
                                    <Popover
                                        open={openModelSelect}
                                        onOpenChange={setOpenModelSelect}
                                    >
                                        <PopoverTrigger asChild>
                                            <Button
                                                variant="outline"
                                                role="combobox"
                                                aria-expanded={openModelSelect}
                                                className="w-full h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between"
                                            >
                                                <span className="text-sm truncate font-medium text-gray-900">
                                                    {
                                                        currentModel
                                                            ? availableModels.find(model => model === currentModel) || currentModel
                                                            :
                                                            t("selectModel")
                                                    }
                                                </span>

                                                <ChevronUp className="w-4 h-4 text-gray-500" />
                                            </Button>
                                        </PopoverTrigger>
                                        <PopoverContent
                                            className="p-0"
                                            align="start"
                                            style={{ width: "var(--radix-popover-trigger-width)" }}
                                        >
                                            <Command>
                                                <CommandInput placeholder={t("searchModel")} />
                                                <CommandList>
                                                    <CommandEmpty>{t("modelNotFound")}</CommandEmpty>
                                                    <CommandGroup>
                                                        {availableModels.map((model, index) => (
                                                            <CommandItem
                                                                key={index}
                                                                value={model}
                                                                onSelect={(value) => {
                                                                    if (currentModelField) {
                                                                        setLlmConfig(prev => ({
                                                                            ...prev,
                                                                            [currentModelField]: value
                                                                        }));
                                                                    }
                                                                    setOpenModelSelect(false);
                                                                }}
                                                            >
                                                                <Check
                                                                    className={cn(
                                                                        "mr-2 h-4 w-4",
                                                                        currentModel === model
                                                                            ? "opacity-100"
                                                                            : "opacity-0"
                                                                    )}
                                                                />
                                                                <div className="flex gap-3 items-center">
                                                                    <div className="flex flex-col space-y-1 flex-1">
                                                                        <div className="flex items-center justify-between gap-2">
                                                                            <span className="text-sm font-medium text-gray-900">
                                                                                {model}
                                                                            </span>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                            </CommandItem>
                                                        ))}
                                                    </CommandGroup>
                                                </CommandList>
                                            </Command>
                                        </PopoverContent>
                                    </Popover>
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
            {/* Image Provider */}
            <div className={`p-3 border border-surface rounded-[11px] relative mt-5 bg-white ${llmConfig.DISABLE_IMAGE_GENERATION ? "bg-[#F9FAFB]" : ""}`}>
                <ToolTip content="이미지 생성 사용/해제" className='flex justify-end items-center absolute top-3 right-3'>
                    <div className='flex justify-end items-center'>
                        <Switch
                            checked={!llmConfig.DISABLE_IMAGE_GENERATION}
                            className='data-[state=checked]:bg-[#4791FF] h-[22px] w-[36px] data-[state=unchecked]:bg-[#E2E0E1]'
                            onCheckedChange={(checked) => setLlmConfig(prev => ({
                                ...prev,
                                DISABLE_IMAGE_GENERATION: !checked
                            }))}
                        />
                    </div>

                </ToolTip>
                <div className={` flex items-center gap-6 ${llmConfig.DISABLE_IMAGE_GENERATION ? "" : "mb-[42px]"}`}>
                    <div className='w-[74px] h-[74px] px-[13.5px] py-[14.2px] rounded-[4px] flex items-center justify-center'
                        style={{ backgroundColor: '#F4F3FF' }}
                    >
                        <Image
                            src="/image-markup.svg"
                            className='h-full w-full object-cover'
                            alt={t("imageMarkupAlt")}
                            width={46}
                            height={46}
                        />
                    </div>
                    <div>

                        <h3 className="text-xl font-normal text-[#191919] ">{t("imageGenerationSettings")}</h3>
                        <p className=" text-sm  text-gray-500">
                            {t("chooseImageLocation")}
                        </p>
                    </div>
                </div>
                {!llmConfig.DISABLE_IMAGE_GENERATION && (
                    <div className='flex flex-col gap-4'>
                        {/* Image Provider Selection */}
                        <div className="w-full">
                            <label className="block text-sm font-medium text-gray-700 mb-2">
                                {t("chooseImageProvider")}
                            </label>
                            <div className="w-full">
                                <ImageProviderPicker
                                    value={llmConfig.IMAGE_PROVIDER}
                                    open={openImageProviderSelect}
                                    onOpenChange={setOpenImageProviderSelect}
                                    variant="onboarding"
                                    labels={{
                                        placeholder: t("chooseImageProvider"),
                                        search: t("searchProvider"),
                                        notFound: t("providerNotFound"),
                                    }}
                                    onSelect={(value) => setLlmConfig((prev) => ({ ...prev, IMAGE_PROVIDER: value }))}
                                />
                            </div>
                        </div>



                        {/* Dynamic API Key Input for Image Provider */}
                        {llmConfig.IMAGE_PROVIDER &&
                            IMAGE_PROVIDER_OPTIONS[llmConfig.IMAGE_PROVIDER] &&
                            (() => {
                                const provider = IMAGE_PROVIDER_OPTIONS[llmConfig.IMAGE_PROVIDER];
                                            if (provider.value === "codex_image") {
                                                return <p className="text-sm text-gray-600">{t("codexImageInfo")}</p>;
                                            }


                                if (provider.value === "openai_compatible") {
                                    return (
                                        <OpenAICompatibleImageFields
                                            layout="stacked"
                                            baseUrl={llmConfig.OPENAI_COMPAT_IMAGE_BASE_URL || ""}
                                            apiKey={llmConfig.OPENAI_COMPAT_IMAGE_API_KEY || ""}
                                            model={llmConfig.OPENAI_COMPAT_IMAGE_MODEL || ""}
                                            onBaseUrlChange={(v) =>
                                                setLlmConfig((prev) => ({
                                                    ...prev,
                                                    OPENAI_COMPAT_IMAGE_BASE_URL: v,
                                                }))
                                            }
                                            onApiKeyChange={(v) =>
                                                setLlmConfig((prev) => ({
                                                    ...prev,
                                                    OPENAI_COMPAT_IMAGE_API_KEY: v,
                                                }))
                                            }
                                            onModelChange={(v) =>
                                                setLlmConfig((prev) => ({
                                                    ...prev,
                                                    OPENAI_COMPAT_IMAGE_MODEL: v,
                                                }))
                                            }
                                        />
                                    );
                                }

                                // Show ComfyUI configuration
                                if (provider.value === "comfyui") {
                                    return (
                                        <div className=" space-y-4 w-full">
                                            <div className=''>
                                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                                    {t("comfyServerUrl")}
                                                </label>
                                                <div className="relative">
                                                    <input
                                                        type="text"
                                                        placeholder="http://192.168.1.7:8188"
                                                        className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                                        value={llmConfig.COMFYUI_URL || ""}
                                                        onChange={(e) => {
                                                            setLlmConfig(prev => ({
                                                                ...prev,
                                                                COMFYUI_URL: e.target.value
                                                            }));
                                                        }}
                                                    />
                                                </div>

                                            </div>

                                        </div>
                                    );
                                }

                                // Show API key input for other providers
                                return (
                                    <div className="w-full ">
                                        <div className='flex items-center justify-between mb-2'>

                                            <label className="block text-sm font-medium text-gray-700">
                                                {provider.apiKeyFieldLabel}
                                            </label>
                                            {provider.getApiKeyUrl && <a href={provider.getApiKeyUrl || ""} target='_blank' className='text-[#666666] text-xs font-normal flex items-center gap-1'>{t("getApiKey")} <ArrowUpRight className='w-3.5 h-3.5' /></a>}
                                        </div>
                                        <MaskedApiKeyInput
                                            variant="onboarding"
                                            value={getFieldValue(provider.apiKeyField)}
                                            placeholder={t("enterField", { field: provider.apiKeyFieldLabel ?? "" })}
                                            show={showApiKey}
                                            onToggleShow={() => setShowApiKey((prev) => !prev)}
                                            onChange={(value) =>
                                                setLlmConfig((prev) => ({
                                                    ...prev,
                                                    [provider.apiKeyField as keyof LLMConfig]: value,
                                                }))
                                            }
                                        />

                                    </div>
                                );
                            })()}

                    </div>
                )}
                {!llmConfig.DISABLE_IMAGE_GENERATION && <div className='flex flex-col justify-end items-center mt-[18px]'>
                    <div className='w-full flex items-center gap-4'>

                        {renderQualitySelector(llmConfig)}
                    </div>
                    {llmConfig.IMAGE_PROVIDER === "comfyui" && <div className='w-full'>
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            {t("workflowJson")}
                        </label>
                        <div className="relative">
                            <textarea
                                placeholder={t("workflowPlaceholder")}
                                className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors font-mono text-xs"
                                rows={3}
                                value={llmConfig.COMFYUI_WORKFLOW || ""}
                                onChange={(e) => {
                                    setLlmConfig((prev) => ({
                                        ...prev,
                                        COMFYUI_WORKFLOW: e.target.value
                                    }))
                                }}
                            />
                        </div>

                    </div>}
                </div>}
            </div>

            <div className='fixed bottom-4 left-4 right-4 z-30 flex items-center justify-end gap-2.5 md:bottom-16 md:left-auto md:right-16'>
                <button
                    disabled={currentStep === 1}
                    onClick={() => {
                        setStep(currentStep - 1);
                    }}
                    className='border border-surface rounded-[53px] px-4 py-1 h-[36px]'>
                    <ChevronLeft className='w-4 h-4 text-gray-500' />
                </button>
                <button

                    disabled={savingConfig}
                    onClick={handleSaveConfig}
                    className='border font-syne border-surface bg-[#7C51F8]  rounded-[58px] px-5 py-2.5 text-white text-xs  font-semibold'>
                    {t("continueToFinish")}
                </button>
            </div>
            {/* Download Progress Modal */}
            {showDownloadModal && downloadingModel && (
                <div className="fixed inset-0 bg-slate-950/35 backdrop-blur-[2px] z-50 flex items-center justify-center p-4">
                    <div className="rounded-2xl border border-gray-200/80 bg-white shadow-[0_12px_40px_-16px_rgba(15,23,42,0.18)] max-w-[400px] max-h-[calc(100dvh-2rem)] overflow-y-auto w-full px-6 py-8 sm:px-8 relative">
                        {/* Modal Content */}
                        <div className="text-center">
                            {/* Icon */}
                            <div className="mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-2xl bg-violet-50">
                                {downloadingModel.done ? (
                                    <CheckCircle className="w-6 h-6 text-green-600 mx-auto" />
                                ) : (
                                    <Download className="w-6 h-6 text-brand mx-auto" />
                                )}
                            </div>

                            {/* Title */}
                            <h3 className="break-keep text-xl font-semibold leading-7 tracking-tight text-gray-900 mb-2">
                                {downloadingModel.done ? t("downloadComplete") : t("downloadingModel")}
                            </h3>

                            {/* Model Name */}
                            <p className="break-words text-sm leading-6 text-gray-500 mb-6">
                                {llmConfig.OLLAMA_MODEL}
                            </p>

                            {/* Progress Bar */}
                            {downloadProgress > 0 && (
                                <div className="mb-4">
                                    <div className="w-full bg-gray-100 rounded-full h-1 overflow-hidden">
                                        <div
                                            className="bg-brand/80 h-1 rounded-full transition-[width] duration-300 ease-out motion-reduce:transition-none"
                                            style={{ width: `${downloadProgress}%` }}
                                        />
                                    </div>
                                    <p className="text-xs tabular-nums text-gray-500 mt-2">
                                        {t("downloadPercent", { percent: downloadProgress })}
                                    </p>
                                </div>
                            )}

                            {/* Status */}
                            {downloadingModel.status && (
                                <div className="flex items-center justify-center gap-2 mb-4">
                                    <CheckCircle className="w-4 h-4 text-green-600" />
                                    <span className="text-sm font-medium text-green-700 capitalize">
                                        {downloadingModel.status}
                                    </span>
                                </div>
                            )}

                            {/* Status Message */}
                            {downloadingModel.status && downloadingModel.status !== "pulled" && (
                                <div className="text-xs text-gray-500">
                                    {downloadingModel.status === "downloading" && t("downloadingFile")}
                                    {downloadingModel.status === "verifying" && t("verifyingModel")}
                                    {downloadingModel.status === "pulling" && t("pullingModel")}
                                </div>
                            )}

                            {/* Download Info */}
                            {downloadingModel.downloaded && downloadingModel.size && (
                                <div className="mt-4 p-3 bg-gray-50 rounded-lg">
                                    <div className="flex justify-between text-xs text-gray-600">
                                        <span>{t("downloaded")} {(downloadingModel.downloaded / 1024 / 1024).toFixed(1)} MB</span>
                                        <span>{t("total")} {(downloadingModel.size / 1024 / 1024).toFixed(1)} MB</span>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

export default PresentonMode
