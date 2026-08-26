import { Textarea } from "@/components/ui/textarea";
import { useTranslations } from "next-intl";
import React, { useEffect, useRef } from "react";

interface PromptInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
  label?: string;
  placeholder?: string;
}

export function PromptInput({ value, onChange, onSubmit, label, placeholder }: PromptInputProps) {
  const t = useTranslations("upload");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, 76), 184)}px`;
  }, [value]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      onSubmit?.();
    }
  };

  return (
    <div>
      {label ? <label className="mb-1 block px-1 text-sm font-semibold text-[#242629]">{label}</label> : null}
      <Textarea
      ref={textareaRef}
      value={value}
      autoFocus
      rows={2}
      aria-label={label ?? t("promptLabel")}
      onChange={(event) => onChange(event.target.value)}
      onKeyDown={handleKeyDown}
      placeholder={placeholder ?? t("promptPlaceholder")}
      data-testid="prompt-input"
      className="custom_scrollbar min-h-[76px] max-h-[184px] resize-none overflow-y-auto border-0 bg-transparent px-1 py-1 font-syne text-base font-medium leading-7 text-[#242629] shadow-none placeholder:font-normal placeholder:text-[#909499] focus-visible:ring-0 focus-visible:ring-offset-0 md:text-[17px]"
      />
    </div>
  );
}
