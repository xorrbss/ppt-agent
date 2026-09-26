"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
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
import { CHATGPT_MODELS, CODEX_GENERATION_PRESETS, type CodexReasoningEffort } from "./constants";

interface CodexModelSelectProps {
  codexModel: string;
  codexReasoningEffort: string;
  onSelect: (modelId: string, effort: CodexReasoningEffort) => void;
}

/** ChatGPT models are a fixed client-side list; the provider exposes no discovery API. */
const CodexModelSelect = ({ codexModel, codexReasoningEffort, onSelect }: CodexModelSelectProps) => {
  const t = useTranslations("a2cCodexConfig");
  const [open, setOpen] = useState(false);
  const selectedPreset = CODEX_GENERATION_PRESETS.find(
    (preset) => preset.modelId === codexModel && preset.effort === codexReasoningEffort,
  );
  const selectedModelName = CHATGPT_MODELS.find((model) => model.id === codexModel)?.name;
  const selectedName = selectedPreset?.name ?? (
    codexModel
      ? `${selectedModelName ?? codexModel}${codexReasoningEffort ? ` · ${codexReasoningEffort}` : ""}`
      : ""
  );

  return (
    <div>
      <label className="mb-2 block text-sm font-medium text-gray-700">
        {t("modelAndReasoning")}
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
              {codexModel ? (selectedName ?? codexModel) : t("selectModel")}
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
            <CommandInput placeholder={t("searchModel")} />
            <CommandList>
              <CommandEmpty>{t("modelNotFound")}</CommandEmpty>
              <CommandGroup>
                {CODEX_GENERATION_PRESETS.map((preset) => (
                  <CommandItem
                    key={`${preset.modelId}:${preset.effort}`}
                    value={`${preset.modelId} ${preset.effort}`}
                    onSelect={() => {
                      onSelect(preset.modelId, preset.effort);
                      setOpen(false);
                    }}
                  >
                    <Check
                      className={cn(
                        "mr-2 h-4 w-4",
                        codexModel === preset.modelId && codexReasoningEffort === preset.effort
                          ? "opacity-100"
                          : "opacity-0"
                      )}
                    />
                    <span className="text-sm text-gray-900">{preset.name}</span>
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
