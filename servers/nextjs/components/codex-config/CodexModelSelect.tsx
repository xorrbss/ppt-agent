"use client";
import { useState } from "react";
import { Check, ChevronUp } from "lucide-react";
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
import { CHATGPT_MODELS } from "./constants";

interface CodexModelSelectProps {
  codexModel: string;
  onSelect: (modelId: string) => void;
}

/** ChatGPT models are a fixed client-side list; the provider exposes no discovery API. */
const CodexModelSelect = ({ codexModel, onSelect }: CodexModelSelectProps) => {
  const [open, setOpen] = useState(false);
  const selectedName = CHATGPT_MODELS.find((model) => model.id === codexModel)?.name;

  return (
    <div>
      <label className="mb-2 block text-sm font-medium text-gray-700">
        GPT 모델 선택
      </label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="h-10 w-full justify-between rounded-lg border border-gray-300 px-3 outline-none hover:border-gray-400"
          >
            <span className="text-sm text-gray-900">
              {codexModel ? (selectedName ?? codexModel) : "모델 선택"}
            </span>
            <ChevronUp className="h-4 w-4 text-gray-400" />
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
                {CHATGPT_MODELS.map((model) => (
                  <CommandItem
                    key={model.id}
                    value={model.id}
                    onSelect={(value) => {
                      onSelect(value);
                      setOpen(false);
                    }}
                  >
                    <Check
                      className={cn(
                        "mr-2 h-4 w-4",
                        codexModel === model.id ? "opacity-100" : "opacity-0"
                      )}
                    />
                    <span className="text-sm text-gray-900">{model.name}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
};

export default CodexModelSelect;
