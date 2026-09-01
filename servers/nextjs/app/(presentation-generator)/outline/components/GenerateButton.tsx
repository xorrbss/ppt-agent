import React from "react";
import { Button } from "@/components/ui/button";
import { LoadingState } from "../types/index";
import { Sparkles } from "lucide-react";

interface GenerateButtonProps {
  loadingState: LoadingState;
  streamState: { isStreaming: boolean; isLoading: boolean };
  onSubmit: () => void;
}

const GenerateButton: React.FC<GenerateButtonProps> = ({
  loadingState,
  streamState,
  onSubmit,
}) => {
  const isDisabled =
    loadingState.isLoading || streamState.isLoading || streamState.isStreaming;

  const getButtonText = () => {
    if (loadingState.isLoading) return loadingState.message;
    if (streamState.isLoading || streamState.isStreaming) return "불러오는 중…";
    return "발표자료 생성";
  };

  return (
    <Button
      disabled={isDisabled}
      onClick={() => {
        onSubmit();
      }}
      variant="toolbar"
      size="toolbar"
      className="h-[46px] w-fit px-6 font-syne disabled:cursor-not-allowed"
    >
      <Sparkles
        aria-hidden="true"
        strokeWidth={1.7}
        className="h-[18px] w-[18px]"
      />
      {getButtonText()}
    </Button>
  );
};

export default GenerateButton;
