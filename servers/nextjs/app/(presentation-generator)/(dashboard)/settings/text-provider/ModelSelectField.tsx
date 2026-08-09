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
import { LLM_PROVIDERS } from "@/utils/providerConstants";
import { Check, ChevronUp } from "lucide-react";
import React from "react";
import type { ModelOption } from "./types";

interface ModelSelectFieldProps {
  selectedProvider: keyof typeof LLM_PROVIDERS;
  modelLabel: string;
  availableModels: ModelOption[];
  currentModel: string;
  currentModelField: string;
  onInputChange: (value: string | boolean, field: string) => void;
  openModelSelect: boolean;
  setOpenModelSelect: (open: boolean) => void;
}

const ModelSelectField = ({
  selectedProvider,
  modelLabel,
  availableModels,
  currentModel,
  currentModelField,
  onInputChange,
  openModelSelect,
  setOpenModelSelect,
}: ModelSelectFieldProps) => {
  return (
    <div className="w-[262px]">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-3">
          {selectedProvider === "ollama"
            ? "지원되는 모델 선택"
            : `${modelLabel} 모델 선택`}
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
                  {(() => {
                    if (!currentModel) return "모델 선택";
                    const selectedModel = availableModels.find(
                      (model) => model.value === currentModel
                    );
                    if (!selectedModel) return currentModel;
                    if (
                      selectedProvider === "ollama" &&
                      selectedModel.size
                    ) {
                      return `${selectedModel.label} (${selectedModel.size})`;
                    }
                    return selectedModel.label;
                  })()}
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
                <CommandInput placeholder="모델 검색..." />
                <CommandList>
                  <CommandEmpty>모델을 찾을 수 없습니다.</CommandEmpty>
                  <CommandGroup>
                    {availableModels.map((model) => (
                      <CommandItem
                        key={model.value}
                        value={model.value}
                        onSelect={() => {
                          if (currentModelField) {
                            onInputChange(
                              model.value,
                              currentModelField
                            );
                          }
                          setOpenModelSelect(false);
                        }}
                      >
                        <Check
                          className={cn(
                            "mr-2 h-4 w-4",
                            currentModel === model.value
                              ? "opacity-100"
                              : "opacity-0"
                          )}
                        />
                        <div className="flex gap-3 items-center">
                          <div className="flex flex-col space-y-1 flex-1">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-sm font-medium text-gray-900">
                                {model.label}
                              </span>
                              {selectedProvider === "ollama" &&
                              model.size ? (
                                <span className="text-xs font-medium text-gray-500">
                                  {model.size}
                                </span>
                              ) : null}
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
  );
};

export default ModelSelectField;
