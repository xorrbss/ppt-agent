import { Textarea } from "@/components/ui/textarea";
import { useState } from "react";

interface PromptInputProps {
  value: string;
  onChange: (value: string) => void;
}

export function PromptInput({ value, onChange }: PromptInputProps) {


  const handleChange = (val: string) => {

    onChange(val);
  };

  return (

    <div className="relative font-syne">
      <Textarea
        value={value}
        rows={5}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="Tell us about your presentation"
        data-testid="prompt-input"
        className={`py-3 px-2.5 border-2 font-medium font-instrument_sans text-base min-h-[150px] max-h-[300px] border-[#DBDBDB99] focus-visible:ring-offset-0  focus-visible:ring-[#5146E5] overflow-y-auto  custom_scrollbar  `}
      />
    </div>

  );
}