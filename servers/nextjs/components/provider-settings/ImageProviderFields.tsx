import React from "react";
import { Check, ChevronUp, Eye, EyeOff } from "lucide-react";

import { useTranslations } from "next-intl";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { localizeProviderDescription } from "@/utils/provider-description";
import { LLMConfig } from "@/types/llm_config";
import {
  IMAGE_PROVIDER_OPTIONS,
  IMAGE_QUALITY_OPTIONS,
} from "@/utils/provider-registry.generated";

/**
 * Image-provider form fields shared by the onboarding wizard and the settings
 * page. The two screens differ only in styling (`variant`) and in the copy they
 * pass through props, so the provider/quality logic lives here once.
 */
export type ProviderFieldsVariant = "settings" | "onboarding";

const TRIGGER_CLASS: Record<ProviderFieldsVariant, string> = {
  settings:
    "w-full h-11 px-3 py-2 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors hover:border-gray-400 justify-between",
  onboarding:
    "w-full h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between",
};

const PICKER_BUTTON_CLASS: Record<ProviderFieldsVariant, string> = {
  settings:
    "w-full sm:w-[262px] h-11 px-4 py-4 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors hover:border-gray-400 justify-between",
  onboarding:
    "w-full h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between",
};

const INPUT_CLASS: Record<ProviderFieldsVariant, string> = {
  settings:
    "w-full px-4 py-2.5 h-12 outline-none border border-zinc-200 rounded-lg focus:ring-2 focus:ring-zinc-900/10 focus:border-zinc-400 transition-colors",
  onboarding:
    "w-full px-4 py-2.5 h-12 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors",
};

export interface ImageQualitySelectLabels {
  dalle3: string;
  gptImage15: string;
  /** Appended to the provider label for the gpt-image-2 family. */
  qualitySuffix: string;
  placeholder: string;
}

/** Quality dropdown for the image providers that expose one; null otherwise. */
export function ImageQualitySelect({
  llmConfig,
  onChange,
  variant,
  labels,
}: {
  llmConfig: LLMConfig;
  onChange: (field: string, value: string) => void;
  variant: ProviderFieldsVariant;
  labels: ImageQualitySelectLabels;
}) {
  const provider = llmConfig.IMAGE_PROVIDER;
  let field: string;
  let fallback: string;
  let optionsKey: keyof typeof IMAGE_QUALITY_OPTIONS;
  let label: string;

  if (provider === "dall-e-3") {
    field = "DALL_E_3_QUALITY";
    fallback = "standard";
    optionsKey = "dall-e-3";
    label = labels.dalle3;
  } else if (provider === "gpt-image-1.5") {
    field = "GPT_IMAGE_1_5_QUALITY";
    fallback = "low";
    optionsKey = "gpt-image-1.5";
    label = labels.gptImage15;
  } else if (
    provider === "gpt-image-2" ||
    provider === "gpt-image-2.5-flare" ||
    provider === "gpt-image-2.5-sunburst" ||
    provider === "codex_image"
  ) {
    field = provider === "gpt-image-2" ? "GPT_IMAGE_2_QUALITY" : "GPT_IMAGE_2_5_QUALITY";
    fallback = "medium";
    optionsKey = provider === "codex_image" ? "gpt-image-2.5-sunburst" : provider;
    label = `${IMAGE_PROVIDER_OPTIONS[provider].label} ${labels.qualitySuffix}`;
  } else {
    return null;
  }

  const wrapperClass =
    variant === "onboarding"
      ? "w-full"
      : provider === "dall-e-3"
        ? "ml-auto w-full sm:w-[262px]"
        : "w-full sm:w-[262px]";
  const value = (llmConfig as Record<string, string | undefined>)[field] || fallback;

  return (
    <div className={wrapperClass}>
      <label className="block text-sm font-medium text-gray-700 mb-2">{label}</label>
      <Select value={value} onValueChange={(next) => onChange(field, next)}>
        <SelectTrigger className={TRIGGER_CLASS[variant]}>
          <SelectValue placeholder={labels.placeholder} />
        </SelectTrigger>
        <SelectContent>
          {IMAGE_QUALITY_OPTIONS[optionsKey].map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export interface ImageProviderPickerLabels {
  placeholder: string;
  search: string;
  notFound: string;
}

/** Searchable image-provider combobox. Closes itself after a selection. */
export function ImageProviderPicker({
  value,
  onSelect,
  open,
  onOpenChange,
  variant,
  labels,
}: {
  value?: string;
  onSelect: (providerId: string) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  variant: ProviderFieldsVariant;
  labels: ImageProviderPickerLabels;
}) {
  const tProviderSettings = useTranslations("providerSettings");
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className={PICKER_BUTTON_CLASS[variant]}
        >
          <div className="flex gap-3 items-center">
            <span
              className={cn(
                "text-sm font-medium text-gray-900",
                variant === "onboarding" && "capitalize",
              )}
            >
              {value
                ? IMAGE_PROVIDER_OPTIONS[value]?.label || value
                : labels.placeholder}
            </span>
          </div>
          <ChevronUp className="w-4 h-4 text-gray-500" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className={variant === "onboarding" ? "p-0 w-full" : "p-0"}
        align="start"
        style={variant === "settings" ? { width: "300px" } : undefined}
      >
        <Command>
          <CommandInput placeholder={labels.search} />
          <CommandList>
            <CommandEmpty>{labels.notFound}</CommandEmpty>
            <CommandGroup>
              {Object.values(IMAGE_PROVIDER_OPTIONS).map((provider, index) => (
                <CommandItem
                  key={index}
                  value={provider.value}
                  onSelect={(selected) => {
                    onSelect(selected);
                    onOpenChange(false);
                  }}
                >
                  <Check
                    className={cn(
                      "mr-2 h-4 w-4",
                      value === provider.value ? "opacity-100" : "opacity-0",
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
                        {localizeProviderDescription(provider.description, name => tProviderSettings("registryProviderDescription", { provider: name }))}
                      </span>
                    </div>
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/** Masked API-key input with a show/hide toggle. */
export function MaskedApiKeyInput({
  value,
  onChange,
  show,
  onToggleShow,
  placeholder,
  variant,
}: {
  value: string;
  onChange: (value: string) => void;
  show: boolean;
  onToggleShow: () => void;
  placeholder: string;
  variant: ProviderFieldsVariant;
}) {
  return (
    <div className="relative">
      <input
        type={show ? "text" : "password"}
        placeholder={placeholder}
        className={INPUT_CLASS[variant]}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <button
        type="button"
        onClick={onToggleShow}
        className="absolute right-2 top-1/2 -translate-y-1/2 bg-white px-2 py-1 cursor-pointer"
      >
        {show ? (
          <Eye className="w-4 h-4 text-gray-500" />
        ) : (
          <EyeOff className="w-4 h-4 text-gray-500" />
        )}
      </button>
    </div>
  );
}
