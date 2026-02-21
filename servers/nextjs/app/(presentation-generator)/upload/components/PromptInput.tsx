import { Textarea } from "@/components/ui/textarea";
import { useState } from "react";


interface PromptInputProps {
  value: string;
  onChange: (value: string) => void;

}

export function PromptInput({
  value,
  onChange,

}: PromptInputProps) {

  return (


    <Textarea
      value={value}
      rows={3}
      name="prompt"
      id="prompt"
      aria-label="Prompt"
      aria-describedby="prompt-description"
      aria-required="true"
      aria-invalid="false"
      aria-autocomplete="list"
      aria-controls="prompt-list"
      aria-expanded="false"
      aria-haspopup="listbox"
      autoFocus={true}
      onChange={(e) => onChange(e.target.value)}
      placeholder="Tell us about your presentation"
      data-testid="prompt-input"
      className={`py-3.5 px-2.5 rounded-[10px] border-none bg-[#F6F6F9] placeholder:text-[#B3B3B3] font-medium font-instrument_sans text-base  max-h-[300px]  focus-visible:ring-offset-0  focus-visible:ring-0 overflow-y-auto  custom_scrollbar  `}
    />


  );
}
