import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { LLMConfig } from "@/types/llm_config";
import { LLM_PROVIDER_OPTIONS } from "@/utils/provider-registry.generated";
import { Check, ChevronUp } from "lucide-react";
import React from "react";

interface ProviderSelectFieldProps {
  selectedProvider: keyof typeof LLM_PROVIDER_OPTIONS | null;
  llmConfig: LLMConfig;
  onInputChange: (value: string | boolean, field: string) => void;
  openProviderSelect: boolean;
  setOpenProviderSelect: (open: boolean) => void;
}

const ProviderSelectField = ({
  selectedProvider,
  llmConfig,
  onInputChange,
  openProviderSelect,
  setOpenProviderSelect,
}: ProviderSelectFieldProps) => {
  return (
    <div
      className={`relative shrink-0 ${
        selectedProvider === "codex" ? "w-[240px]" : "w-[262px]"
      }`}
    >
      <div className="flex flex-col justify-start ">
        <label className="block text-sm font-medium text-gray-700 mb-2">
          텍스트 제공자 선택
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
              className="w-[222px] h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between"
            >
              <div className="flex gap-3 items-center">
                <span className="text-sm font-medium text-gray-900">
                  {llmConfig.LLM
                    ? LLM_PROVIDER_OPTIONS[llmConfig.LLM]?.label ||
                      llmConfig.LLM
                    : "텍스트 제공자 선택"}
                </span>
              </div>
              <ChevronUp className="w-4 h-4 text-gray-500" />
            </Button>
          </PopoverTrigger>
          <PopoverContent
            className="p-0"
            align="start"
            style={{ width: "300px" }}
          >
            <Command>
              <CommandInput placeholder="제공자 검색..." />
              <CommandList>
                <CommandEmpty>제공자를 찾을 수 없습니다.</CommandEmpty>
                <CommandGroup>
                  {Object.values(LLM_PROVIDER_OPTIONS).map(
                    (provider, index) => (
                      <CommandItem
                        key={index}
                        value={provider.value}
                        onSelect={(value) => {
                          onInputChange(value, "LLM");
                          setOpenProviderSelect(false);
                        }}
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
    </div>
  );
};

export default ProviderSelectField;
