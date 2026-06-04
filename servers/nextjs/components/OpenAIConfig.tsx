"use client";
import { useEffect, useState } from "react";
import { Check, ChevronsUpDown, Loader2 } from "lucide-react";
import { Button } from "./ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "./ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { cn } from "@/lib/utils";
import { notify } from "@/components/ui/sonner";
import { Switch } from "./ui/switch";
import { LLMConfig } from "@/types/llm_config";
import { getApiUrl } from "@/utils/api";

interface OpenAIConfigProps {
  openaiApiKey: string;
  openaiModel: string;
  webGrounding?: boolean;
  onInputChange: (value: string | boolean, field: string) => void;
  llmConfig: LLMConfig;
}

export default function OpenAIConfig({
  openaiApiKey,
  openaiModel,
  webGrounding,
  onInputChange,
  llmConfig
}: OpenAIConfigProps) {
  const [openModelSelect, setOpenModelSelect] = useState(false);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
const [modelsLoading, setModelsLoading] = useState(false);
const [modelsChecked, setModelsChecked] = useState(false);
const [apiKey, setApiKey] = useState(openaiApiKey);
const isImageGenerationDisabled = llmConfig?.DISABLE_IMAGE_GENERATION ?? false;

  const openaiUrl = "https://api.openai.com/v1";

  useEffect(() => {
    setAvailableModels([]);
    setModelsChecked(false);
    onInputChange("", "openai_model");
  }, [apiKey]);

  const onApiKeyChange = (value: string) => {
    setApiKey(value);
    onInputChange(value, "openai_api_key");
  };

  const fetchAvailableModels = async () => {
    if (!openaiApiKey) return;

    setModelsLoading(true);
    try {
      const response = await fetch(getApiUrl("/api/v1/ppt/openai/models/available"), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          url: openaiUrl,
          api_key: openaiApiKey
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setAvailableModels(data);
        setModelsChecked(true);
        onInputChange("gpt-4.1", "openai_model");
      } else {
        console.error('Failed to fetch models');
        setAvailableModels([]);
        setModelsChecked(true);
      }
    } catch (error) {
      console.error('Error fetching models:', error);
      notify.error("모델을 불러오지 못했습니다", "서버에서 모델 목록을 가져오지 못했습니다. API 키 또는 엔드포인트를 확인한 후 다시 시도하세요.");
      setAvailableModels([]);
      setModelsChecked(true);
    } finally {
      setModelsLoading(false);
    }
  };

  return (
    <div className="space-y-6 ">
      {/* API Key Input */}
      <div className="mb-4 flex items-center justify-between bg-white p-10">
        <div className="">

          <h3 className="text-xl font-normal text-[#191919]">OpenAI API 키</h3>
          <p className="mt-2 text-sm max-w-[205px] text-gray-500">
            API 키는 로컬에 저장되며 외부에 공유되지 않습니다
          </p>
        </div>
        <div className="flex items-center gap-4">


          <div className="relative  w-[275px] ">
            <div className="flex flex-col justify-start gap-2">

              <label className="block text-sm font-medium text-gray-700 mb-2">
                OpenAI API 키
              </label>
              <input
                type="text"
                value={openaiApiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                className="w-full px-2 py-3 outline-none border  border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                placeholder="API 키를 입력하세요"
              />
            </div>

            {/* Check for available models button - show when no models checked or no models found */}

            {(!modelsChecked || (modelsChecked && availableModels.length === 0)) && (

              <button
                onClick={fetchAvailableModels}
                disabled={modelsLoading || !openaiApiKey}
                className={` mt-7 py-2.5 bg-[#F7F6F9] px-3.5 rounded-[48px] text-xs font-semibold text-[#101323] transition-all duration-200 border ${modelsLoading || !openaiApiKey
                  ? " border-gray-300 cursor-not-allowed text-gray-500"
                  : " border-[#EDEEEF] text-blue-600 hover:bg-[#E8F0FF]/90 focus:ring-2 focus:ring-blue-500/20"
                  }`}
              >
                {modelsLoading ? (
                  <span className="flex items-center justify-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    모델 확인 중…
                  </span>
                ) : (
                  "사용 가능한 모델 확인"
                )}
              </button>

            )}
          </div>
          <div className="w-[295px]">
            {/* Show message if no models found */}
            {modelsChecked && availableModels.length === 0 && (
              <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                <p className="text-sm text-yellow-800">
                  모델을 찾을 수 없습니다. API 키가 유효하며 OpenAI 모델에 접근할 수 있는지 확인하세요.
                </p>
              </div>
            )}

            {/* Model Selection - only show if models are available */}
            {modelsChecked && availableModels.length > 0 ? (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-3">
                  OpenAI 모델 선택
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
                        <div className="flex gap-3 items-center">
                          <span className="text-sm font-medium text-gray-900">
                            {openaiModel
                              ? availableModels.find(model => model === openaiModel) || openaiModel
                              : "모델 선택"}
                          </span>
                        </div>
                        <ChevronsUpDown className="w-4 h-4 text-gray-500" />
                      </Button>
                    </PopoverTrigger>
                    <PopoverContent
                      className="p-0"
                      align="start"
                      style={{ width: "var(--radix-popover-trigger-width)" }}
                    >
                      <Command>
                        <CommandInput placeholder="모델 검색…" />
                        <CommandList>
                          <CommandEmpty>모델을 찾을 수 없습니다.</CommandEmpty>
                          <CommandGroup>
                            {availableModels.map((model, index) => (
                              <CommandItem
                                key={index}
                                value={model}
                                onSelect={(value) => {
                                  onInputChange(value, "openai_model");
                                  setOpenModelSelect(false);
                                }}
                              >
                                <Check
                                  className={cn(
                                    "mr-2 h-4 w-4",
                                    openaiModel === model
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
            ) : null}
          </div>
        </div>
      </div>






      {/* Web Grounding Toggle - show at the end, below models dropdown */}
      <div className="bg-white flex justify-between items-center p-10 rounded-[12px]">
        <div>
          <h4 className="text-xl font-normal text-[#191919]">모델 제어</h4>
          <p className="mt-2 text-sm max-w-[205px] text-gray-500">

            웹 접근, 이미지 생성, 고급 AI 기능을 설정합니다.
          </p>
        </div>
        <div className="flex items-center gap-4">

          <div className="w-[275px]">
            <div className="flex items-center  mb-4 gap-2.5 ">
              <Switch
                checked={!!webGrounding}
                onCheckedChange={(checked) => onInputChange(checked, "web_grounding")}
              />
              <label className="text-sm font-medium text-gray-700">
                웹 그라운딩 사용
              </label>
            </div>
            <div className="flex items-center  mb-4 gap-2.5 ">
              <Switch
                checked={!!isImageGenerationDisabled}
                onCheckedChange={(checked) => onInputChange(checked, "disable_image_generation")}
              />
              <label className="text-sm font-medium text-gray-700">
                이미지 생성 비활성화
              </label>
            </div>

          </div>
          <div className="w-[295px]"></div>
        </div>

      </div>


    </div>
  );
}