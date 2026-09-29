import { useTranslations as useA2bTranslations } from "next-intl";
import ToolTip from '@/components/ToolTip'
import { Switch } from '@/components/ui/switch'
import { LLMConfig } from '@/types/llm_config'
import OpenAICompatibleImageFields from '@/components/OpenAICompatibleImageFields'
import { IMAGE_PROVIDER_OPTIONS, PUBLIC_PROVIDERS_BY_ID } from '@/utils/provider-registry.generated'
import { Eye, EyeOff } from 'lucide-react'
import React, { useEffect, useState } from 'react'
import Image from 'next/image'
import { ImageProviderPicker, ImageQualitySelect, MaskedApiKeyInput } from '@/components/provider-settings/ImageProviderFields'

const ImageProvider = ({ llmConfig, setLlmConfig }: { llmConfig: LLMConfig, setLlmConfig: React.Dispatch<React.SetStateAction<LLMConfig>> }) => {
  const tA2b = useA2bTranslations("dashboardExtras");
  const tOnboarding = useA2bTranslations("a2cOnboarding");
    const [openImageProviderSelect, setOpenImageProviderSelect] = useState(false);
    const [showApiKey, setShowApiKey] = useState(false);
    const [openaiCompatListMeta, setOpenaiCompatListMeta] = useState<{
        modelsChecked: boolean
        modelCount: number
    }>({ modelsChecked: false, modelCount: 0 })

    useEffect(() => {
        if (llmConfig.IMAGE_PROVIDER !== 'openai_compatible') {
            setOpenaiCompatListMeta({ modelsChecked: false, modelCount: 0 })
        }
    }, [llmConfig.IMAGE_PROVIDER])
    const isImageGenerationDisabled = llmConfig.DISABLE_IMAGE_GENERATION ?? false;
    const handleChangeImageGenerationDisabled = (value: boolean) => {
        setLlmConfig((prev) => ({
            ...prev,
            DISABLE_IMAGE_GENERATION: value
        }));
    }
    const input_field_changed = (value: string, field: string) => {
        setLlmConfig((prev) => ({
            ...prev,
            [field]: value
        }));
        if (field === 'IMAGE_PROVIDER') {
            setOpenImageProviderSelect(false);
        }
    }

    const getFieldValue = (field?: string) => {
        if (!field) return "";
        return (llmConfig as Record<string, string | undefined>)[field] || "";
    };

    const updateFieldValue = (field: string | undefined, value: string) => {
        if (!field) return;
        setLlmConfig((prev) => ({
            ...prev,
            [field]: value,
        }));
    };

    const renderQualitySelector = (llmConfig: LLMConfig, input_field_changed: (value: string, field: string) => void) => (
        <ImageQualitySelect
            llmConfig={llmConfig}
            variant="settings"
            onChange={(field, value) => input_field_changed(value, field)}
            labels={{
                dalle3: "DALL·E 3 이미지 품질",
                gptImage15: "GPT Image 1.5 품질",
                qualitySuffix: tA2b("quality"),
                placeholder: tOnboarding("selectQuality"),
            }}
        />
    );

    return (
        <div className="space-y-5 pb-6">
            {/* API Key Input */}
            <div className="rounded-2xl border border-zinc-200/80 bg-white p-5 shadow-sm sm:p-7">
                <ToolTip content="이미지 생성 켜기/끄기" className='flex justify-end items-center'>
                    <div className='flex justify-end items-center'>
                        <Switch
                            checked={!isImageGenerationDisabled}
                            className='data-[state=checked]:bg-[#4791FF] data-[state=unchecked]:bg-gray-400'
                            onCheckedChange={(checked) => handleChangeImageGenerationDisabled(!checked)}
                        />
                    </div>

                </ToolTip>
                <div className='flex items-center justify-between'>


                    <div className="max-w-sm pb-6">
                        <div className='w-[60px] h-[60px] px-[13.5px] py-[14.2px] rounded-[4px] flex items-center justify-center'
                            style={{ backgroundColor: '#F4F3FF' }}
                        >
                            <Image src="/image-markup.svg" width={33} height={32} className='w-full h-full object-cover' alt='image-markup' />
                        </div>
                        <h3 className="py-3 text-lg font-semibold tracking-tight text-zinc-900">이미지 생성 설정</h3>
                        <p className="text-sm leading-relaxed text-zinc-500">
                            이미지를 가져올 위치를 선택하세요
                        </p>
                    </div>
                    <div className=' '>

                        <div className='flex items-center justify-end gap-4'>

                            {!isImageGenerationDisabled && (
                                <>
                                    {/* Image Provider Selection */}
                                    <div className="">
                                        <label className="block text-sm font-medium text-gray-700 mb-2">
                                            이미지 제공자 선택
                                        </label>
                                        <div className="w-full">
                                            <ImageProviderPicker
                                                value={llmConfig.IMAGE_PROVIDER}
                                                open={openImageProviderSelect}
                                                onOpenChange={setOpenImageProviderSelect}
                                                variant="settings"
                                                labels={{
                                                    placeholder: tOnboarding("chooseImageProvider"),
                                                    search: "제공자 검색...",
                                                    notFound: "제공자를 찾을 수 없습니다.",
                                                }}
                                                onSelect={(value) => input_field_changed(value, "IMAGE_PROVIDER")}
                                            />
                                        </div>
                                    </div>



                                    {/* Dynamic API Key Input for Image Provider */}
                                    {llmConfig.IMAGE_PROVIDER &&
                                        IMAGE_PROVIDER_OPTIONS[llmConfig.IMAGE_PROVIDER] &&
                                        (() => {
                                            const provider = IMAGE_PROVIDER_OPTIONS[llmConfig.IMAGE_PROVIDER];
                                            if (provider.value === "codex_image") {
                                                return <p className="text-sm text-gray-600">{tA2b("usesYourExistingCodexLoginAndIts")}</p>;
                                            }

                                            const layout = PUBLIC_PROVIDERS_BY_ID[provider.value]?.ui?.credentialLayout;



                                            if (layout === "openai-compatible") {
                                                return (
                                                    <OpenAICompatibleImageFields
                                                        layout="textProviderSettings"
                                                        baseUrl={llmConfig.OPENAI_COMPAT_IMAGE_BASE_URL || ""}
                                                        apiKey={llmConfig.OPENAI_COMPAT_IMAGE_API_KEY || ""}
                                                        model={llmConfig.OPENAI_COMPAT_IMAGE_MODEL || ""}
                                                        onBaseUrlChange={(v) => {
                                                            setLlmConfig((prev) => ({ ...prev, OPENAI_COMPAT_IMAGE_BASE_URL: v }));
                                                        }}
                                                        onApiKeyChange={(v) => {
                                                            setLlmConfig((prev) => ({ ...prev, OPENAI_COMPAT_IMAGE_API_KEY: v }));
                                                        }}
                                                        onModelChange={(v) => {
                                                            setLlmConfig((prev) => ({ ...prev, OPENAI_COMPAT_IMAGE_MODEL: v }));
                                                        }}
                                                        onModelListMetaChange={setOpenaiCompatListMeta}
                                                    />
                                                );
                                            }

                                            // Show ComfyUI configuration
                                            if (layout === "comfyui-workflow") {
                                                return (
                                                    <div className=" space-y-4">
                                                        <div className='w-[205px]'>
                                                            <label className="block text-sm font-medium text-gray-700 mb-2">
                                                                ComfyUI 서버 URL
                                                            </label>
                                                            <div className="relative">
                                                                <input
                                                                    type="text"
                                                                    placeholder="http://192.168.1.7:8188"
                                                                    className="w-full px-4 py-2.5 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors"
                                                                    value={llmConfig.COMFYUI_URL || ""}
                                                                    onChange={(e) => {
                                                                        input_field_changed(
                                                                            e.target.value,
                                                                            "COMFYUI_URL"
                                                                        );
                                                                    }}
                                                                />
                                                            </div>

                                                        </div>

                                                    </div>
                                                );
                                            }

                                            // Show Open WebUI configuration
                                            if (layout === "open-webui") {
                                                return (
                                                    <div className="space-y-4">
                                                        <div className='w-[205px]'>
                                                            <label className="block text-sm font-medium text-gray-700 mb-2">
                                                                Open WebUI URL
                                                            </label>
                                                            <div className="relative">
                                                                <input
                                                                    type="text"
                                                                    placeholder="http://localhost:3000/api/v1"
                                                                    className="w-full px-4 py-2.5 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors"
                                                                    value={llmConfig.OPEN_WEBUI_IMAGE_URL || ""}
                                                                    onChange={(e) => {
                                                                        input_field_changed(
                                                                            e.target.value,
                                                                            "OPEN_WEBUI_IMAGE_URL"
                                                                        );
                                                                    }}
                                                                />
                                                            </div>
                                                        </div>
                                                    </div>
                                                );
                                            }

                                            // Show API key input for other providers
                                            return (
                                                <div className="w-full sm:w-[262px]">
                                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                                        {provider.apiKeyFieldLabel}
                                                    </label>
                                                    <MaskedApiKeyInput
                                                        variant="settings"
                                                        value={getFieldValue(provider.apiKeyField)}
                                                        placeholder={`${provider.apiKeyFieldLabel} 입력`}
                                                        show={showApiKey}
                                                        onToggleShow={() => setShowApiKey((prev) => !prev)}
                                                        onChange={(value) => updateFieldValue(provider.apiKeyField, value)}
                                                    />

                                                </div>
                                            );
                                        })()}

                                </>
                            )}
                        </div>
                        {!isImageGenerationDisabled && <div className='flex justify-end items-center mt-[18px]'>

                            {renderQualitySelector(llmConfig, input_field_changed)}
                            {PUBLIC_PROVIDERS_BY_ID[llmConfig.IMAGE_PROVIDER || ""]?.ui?.credentialLayout === "open-webui" && (
                                <div className='w-[205px]'>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        API 키 (선택 사항)
                                    </label>
                                    <div className="relative">
                                        <input
                                            type={showApiKey ? 'text' : 'password'}
                                            placeholder="API 키"
                                            className="w-full px-4 py-2.5 h-12 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors"
                                            value={llmConfig.OPEN_WEBUI_IMAGE_API_KEY || ""}
                                            onChange={(e) => {
                                                input_field_changed(e.target.value, "OPEN_WEBUI_IMAGE_API_KEY");
                                            }}
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowApiKey((prev) => !prev)}
                                            className='absolute right-2 top-1/2 -translate-y-1/2 bg-white px-2 py-1 cursor-pointer'
                                        >
                                            {showApiKey ? <Eye className='w-4 h-4 text-gray-500' /> : <EyeOff className='w-4 h-4 text-gray-500' />}
                                        </button>
                                    </div>
                                </div>
                            )}
                            {PUBLIC_PROVIDERS_BY_ID[llmConfig.IMAGE_PROVIDER || ""]?.ui?.credentialLayout === "comfyui-workflow" && <div className='w-full'>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    워크플로 JSON
                                </label>
                                <div className="relative">
                                    <textarea
                                        placeholder='ComfyUI 워크플로 JSON을 여기에 붙여넣으세요 (ComfyUI에서 "Export (API)"로 내보내기)'
                                        className="w-full px-4 py-2.5 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors font-mono text-xs"
                                        rows={3}
                                        value={llmConfig.COMFYUI_WORKFLOW || ""}
                                        onChange={(e) => {
                                            input_field_changed(
                                                e.target.value,
                                                "COMFYUI_WORKFLOW"
                                            );
                                        }}
                                    />
                                </div>

                            </div>}
                        </div>}
                    </div>
                </div>
            </div>

            {!isImageGenerationDisabled &&
                PUBLIC_PROVIDERS_BY_ID[llmConfig.IMAGE_PROVIDER || ""]?.ui?.credentialLayout === "openai-compatible" &&
                openaiCompatListMeta.modelsChecked &&
                openaiCompatListMeta.modelCount === 0 && (
                    <>
                        <div className="mb-4 rounded-lg border border-yellow-200 bg-yellow-50 p-3">
                            <p className="text-sm text-yellow-800">
                                모델을 찾을 수 없습니다. 제공자 인증 정보가 올바른지, 선택한 제공자에 연결할 수 있는지 확인하세요.
                            </p>
                        </div>
                        <div className="flex w-full justify-end">
                            <div className="w-full sm:w-[262px]">
                                <label className="mb-2 block text-sm font-medium text-gray-700">이미지 모델 ID</label>
                                <input
                                    type="text"
                                    placeholder="예: dall-e-3, gpt-image-1"
                                    className="w-full rounded-lg border border-zinc-200 px-4 py-2.5 outline-none transition-colors focus:border-zinc-400 focus:ring-2 focus:ring-zinc-900/10"
                                    value={llmConfig.OPENAI_COMPAT_IMAGE_MODEL || ""}
                                    onChange={(e) => {
                                        setLlmConfig((prev) => ({
                                            ...prev,
                                            OPENAI_COMPAT_IMAGE_MODEL: e.target.value,
                                        }));
                                    }}
                                />
                            </div>
                        </div>
                    </>
                )}


            {/* Web Grounding Toggle - show at the end, below models dropdown */}
            {/* <div className="flex flex-col gap-5 rounded-2xl border border-zinc-200/80 bg-white p-5 sm:p-7 xl:flex-row xl:items-center xl:justify-between">
                <div className=' max-w-[290px]'>

                    <h4 className="text-lg font-semibold tracking-tight text-zinc-900">Advanced</h4>
                    <p className="mt-2.5text-sm leading-relaxed text-zinc-500">
                        Configure advanced AI features.
                    </p>
                </div>
                <div className="flex w-full flex-wrap items-center gap-4 xl:w-auto">

                    <div className="w-full sm:w-[275px]">



                    </div>
                    <div className="w-full sm:w-[295px]"></div>
                </div>

            </div> */}


        </div>
    )
}

export default ImageProvider
