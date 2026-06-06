import { Textarea } from "@/components/ui/textarea";
import { PencilIcon } from "lucide-react";
import React from "react";

interface PromptInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit?: () => void;
}

export function PromptInput({ value, onChange, onSubmit }: PromptInputProps) {


  const handleChange = (val: string) => {

    onChange(val);
  };

  // Enter submits (like Claude); Shift+Enter inserts a newline. Skip while an
  // IME composition is active so confirming Korean input doesn't submit.
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onSubmit?.();
    }
  };

  return (

    <div className="relative font-syne border border-[#DBDBDB99] rounded-[8px] px-[10px] py-3"
      style={{
        boxShadow: "0 4px 14px 0 rgba(0, 0, 0, 0.04)",

      }}
    >
      <div className="flex items-center gap-2 mb-1">
        <PencilIcon className="w-3.5 h-3.5" />
        <p className="text-sm font-normal text-[#333333] font-syne ">프롬프트 입력</p>
      </div>
      <Textarea
        value={value}
        autoFocus={true}
        rows={4}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="아이디어만 입력하세요… 슬라이드는 저희가 만들어 드립니다"
        data-testid="prompt-input"
        className={`px-2 py-0 font-medium shadow-none font-syne indent-4 text-base min-h-[120px] max-h-[250px] focus-visible:ring-offset-0  focus-visible:ring-transparent focus-visible:ring-0 border-none overflow-y-auto  custom_scrollbar  `}
      />
    </div>

  );
}