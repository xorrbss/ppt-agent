"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronUp, Eye, EyeOff, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { getApiUrl } from "@/utils/api";
import { notify } from "@/components/ui/sonner";

export interface OpenAICompatibleImageFieldsProps {
  baseUrl: string;
  apiKey: string;
  model: string;
  onBaseUrlChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onModelChange: (value: string) => void;
  /** Settings page: report model list state so the parent can render the yellow banner below the card (like TextProvider). */
  onModelListMetaChange?: (meta: { modelsChecked: boolean; modelCount: number }) => void;
  /**
   * `textProviderSettings` — same column widths, field order, inputs, pill button, and model row
   * as {@link TextProvider} when LLM is Custom (settings page only).
   * `stacked` — full-width onboarding / LLM selection layout (CustomConfig-style blocks).
   */
  layout?: "stacked" | "textProviderSettings";
}

/**
 * Image provider "Custom" (OpenAI-compatible). Styling matches Text settings Custom or onboarding stacked layout.
 */
export default function OpenAICompatibleImageFields({
  baseUrl,
  apiKey,
  model,
  onBaseUrlChange,
  onApiKeyChange,
  onModelChange,
  onModelListMetaChange,
  layout = "stacked",
}: OpenAICompatibleImageFieldsProps) {
  const [models, setModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsChecked, setModelsChecked] = useState(false);
  const [openModelSelect, setOpenModelSelect] = useState(false);
  const [showApiKey, setShowApiKey] = useState(false);
  const skipUrlKeyResetOnce = useRef(true);

  const urlKey = `${baseUrl}|${apiKey}`;
  useEffect(() => {
    if (skipUrlKeyResetOnce.current) {
      skipUrlKeyResetOnce.current = false;
      return;
    }
    setModels([]);
    setModelsChecked(false);
    onModelChange("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlKey]);

  const fetchModels = async () => {
    if (!baseUrl.trim()) return;

    setModelsLoading(true);
    try {
      const response = await fetch(getApiUrl("/api/v1/ppt/openai/models/available"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          url: baseUrl.trim(),
          api_key: apiKey,
        }),
      });

      if (response.ok) {
        const data = await response.json();
        setModels(Array.isArray(data) ? data : []);
        setModelsChecked(true);
      } else {
        console.error("Failed to fetch models");
        setModels([]);
        setModelsChecked(true);
        notify.error(
          "모델을 불러올 수 없습니다",
          "서버에서 모델 목록을 가져오지 못했습니다. API 키 또는 엔드포인트를 확인한 후 다시 시도하세요."
        );
      }
    } catch (error) {
      console.error("Error fetching models:", error);
      notify.error(
        "모델을 불러올 수 없습니다",
        "제공자에 연결하는 중 문제가 발생했습니다. 네트워크를 확인한 후 다시 시도하세요."
      );
      setModels([]);
      setModelsChecked(true);
    } finally {
      setModelsLoading(false);
    }
  };

  useEffect(() => {
    if (layout !== "textProviderSettings" || !onModelListMetaChange) return;
    onModelListMetaChange({ modelsChecked, modelCount: models.length });
  }, [layout, modelsChecked, models.length, onModelListMetaChange]);

  if (layout === "textProviderSettings") {
    return (
      <div className="flex shrink-0 flex-col items-end gap-4">
        <div className="relative flex w-[222px] min-w-0 max-w-full shrink-0 flex-col items-end justify-end">
          <div className="flex w-full flex-col justify-start">
            <label className="mb-2 block text-sm font-medium text-gray-700">이미지 API 키</label>
            <div className="relative">
              <input
                type={showApiKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => onApiKeyChange(e.target.value)}
                className="w-full rounded-lg border border-gray-300 px-2 py-3 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
                placeholder="이미지 엔드포인트용 키"
              />
              <button
                type="button"
                onClick={() => setShowApiKey((p) => !p)}
                className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer bg-white px-2 py-1"
              >
                {showApiKey ? <Eye className="h-4 w-4 text-gray-500" /> : <EyeOff className="h-4 w-4 text-gray-500" />}
              </button>
            </div>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => onBaseUrlChange(e.target.value)}
              className="mt-2 w-full rounded-lg border border-gray-300 px-2 py-3 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
              placeholder="기본 URL (/v1 포함)"
            />
          </div>
          {(!modelsChecked || (modelsChecked && models.length === 0)) && (
            <button
              type="button"
              onClick={() => void fetchModels()}
              disabled={modelsLoading || !baseUrl.trim()}
              className={`mt-4 w-fit rounded-[48px] border px-3.5 py-2.5 text-xs font-semibold transition-all duration-200 ${
                modelsLoading || !baseUrl.trim()
                  ? "cursor-not-allowed border-gray-300 bg-[#EDEEEF] text-gray-500"
                  : "border-[#EDEEEF] bg-[#EDEEEF] text-[#101323] hover:bg-[#E8F0FF]/90 focus:ring-2 focus:ring-blue-500/20"
              }`}
            >
              {modelsLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  모델 확인 중…
                </span>
              ) : (
                "모델 확인"
              )}
            </button>
          )}
        </div>

        {modelsChecked && models.length > 0 ? (
          <div className="w-[222px]">
            <div>
              <label className="mb-3 block text-sm font-medium text-gray-700">이미지 모델 선택</label>
              <div className="w-full">
                <Popover open={openModelSelect} onOpenChange={setOpenModelSelect}>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      role="combobox"
                      aria-expanded={openModelSelect}
                      className="flex h-12 w-full justify-between rounded-lg border border-gray-300 px-4 py-4 outline-none transition-colors hover:border-gray-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
                    >
                      <span className="truncate text-sm font-medium text-gray-900">
                        {model || "모델 선택"}
                      </span>
                      <ChevronUp className="h-4 w-4 text-gray-500" />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="p-0" align="start" style={{ width: "var(--radix-popover-trigger-width)" }}>
                    <Command>
                      <CommandInput placeholder="모델 검색…" />
                      <CommandList>
                        <CommandEmpty>모델을 찾을 수 없습니다.</CommandEmpty>
                        <CommandGroup>
                          {models.map((m) => (
                            <CommandItem
                              key={m}
                              value={m}
                              onSelect={() => {
                                onModelChange(m);
                                setOpenModelSelect(false);
                              }}
                            >
                              <Check className={cn("mr-2 h-4 w-4", model === m ? "opacity-100" : "opacity-0")} />
                              <div className="flex flex-1 flex-col space-y-1">
                                <div className="flex items-center justify-between gap-2">
                                  <span className="text-sm font-medium text-gray-900">{m}</span>
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
        ) : null}
      </div>
    );
  }

  /* ----- stacked (onboarding / ImageSelectionConfig) ----- */
  return (
    <div className="w-full space-y-6">
      <p className="-mt-2 mb-2 flex items-center gap-2 text-sm text-gray-500">
        <span className="block h-1 w-1 rounded-full bg-gray-400" />
        OpenAI 방식의{" "}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">/v1/images/generations</code>을(를) 지원하는 엔드포인트를 사용하세요. URL에{" "}
        <code className="rounded bg-gray-100 px-1 py-0.5 text-xs">/v1</code>을(를) 포함하세요.
      </p>

      <div className="mb-4">
        <label className="mb-2 block text-sm font-medium text-gray-700">OpenAI 호환 URL</label>
        <div className="relative">
          <input
            type="text"
            required
            placeholder="URL을 입력하세요"
            className="w-full rounded-lg border border-gray-300 px-4 py-2.5 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
            value={baseUrl}
            onChange={(e) => onBaseUrlChange(e.target.value)}
          />
        </div>
      </div>

      <div className="mb-4">
        <label className="mb-2 block text-sm font-medium text-gray-700">OpenAI 호환 API 키</label>
        <div className="relative">
          <input
            type="text"
            required
            placeholder="API 키를 입력하세요"
            className="w-full rounded-lg border border-gray-300 px-4 py-2.5 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
          />
        </div>
      </div>

      {(!modelsChecked || (modelsChecked && models.length === 0)) && (
        <div className="mb-4">
          <button
            type="button"
            onClick={() => void fetchModels()}
            disabled={modelsLoading || !baseUrl.trim()}
            className={`w-full rounded-lg border-2 px-4 py-2.5 text-sm font-medium transition-all duration-200 ${
              modelsLoading || !baseUrl.trim()
                ? "cursor-not-allowed border-gray-300 bg-gray-100 text-gray-500"
                : "border-blue-600 bg-white text-blue-600 hover:bg-blue-50 focus:ring-2 focus:ring-blue-500/20"
            }`}
          >
            {modelsLoading ? (
              <div className="flex items-center justify-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                모델 확인 중…
              </div>
            ) : (
              "사용 가능한 모델 확인"
            )}
          </button>
        </div>
      )}

      {modelsChecked && models.length === 0 && (
        <div className="mb-4 rounded-lg border border-yellow-200 bg-yellow-50 p-3">
          <p className="text-sm text-yellow-800">
            모델을 찾을 수 없습니다. API 키가 유효하며 모델에 접근 권한이 있는지 확인하세요.
          </p>
        </div>
      )}

      {modelsChecked && models.length === 0 && (
        <div className="mb-4">
          <label className="mb-2 block text-sm font-medium text-gray-700">이미지 모델 ID</label>
          <div className="relative">
            <input
              type="text"
              required
              placeholder="예: dall-e-3, gpt-image-1"
              className="w-full rounded-lg border border-gray-300 px-4 py-2.5 outline-none transition-colors focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
            />
          </div>
        </div>
      )}

      {modelsChecked && models.length > 0 && (
        <div className="mb-4">
          <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
            <p className="text-sm text-amber-800">
              <strong>중요:</strong> 서버가 이미지 생성용으로 제공하는 모델을 선택하세요.
            </p>
          </div>
          <label className="mb-2 block text-sm font-medium text-gray-700">이미지 모델 선택</label>
          <div className="w-full">
            <Popover open={openModelSelect} onOpenChange={setOpenModelSelect}>
              <PopoverTrigger asChild>
                <Button
                  variant="outline"
                  role="combobox"
                  aria-expanded={openModelSelect}
                  className="flex h-12 w-full justify-between rounded-lg border border-gray-300 px-4 py-4 font-normal outline-none transition-colors hover:border-gray-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
                >
                  <span className="text-sm font-medium text-gray-900">{model || "모델 선택"}</span>
                  <ChevronUp className="h-4 w-4 text-gray-500" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="p-0" align="start" style={{ width: "var(--radix-popover-trigger-width)" }}>
                <Command>
                  <CommandInput placeholder="모델 검색…" />
                  <CommandList>
                    <CommandEmpty>모델을 찾을 수 없습니다.</CommandEmpty>
                    <CommandGroup>
                      {models.map((m, index) => (
                        <CommandItem
                          key={index}
                          value={m}
                          onSelect={(value) => {
                            onModelChange(value);
                            setOpenModelSelect(false);
                          }}
                        >
                          <Check className={cn("mr-2 h-4 w-4", model === m ? "opacity-100" : "opacity-0")} />
                          <span className="text-sm font-medium text-gray-900">{m}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  </CommandList>
                </Command>
              </PopoverContent>
            </Popover>
          </div>
        </div>
      )}
    </div>
  );
}
