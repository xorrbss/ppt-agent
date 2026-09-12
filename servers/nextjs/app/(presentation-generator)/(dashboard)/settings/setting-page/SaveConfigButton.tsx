import React from "react";
import { Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ButtonState } from "./types";

interface SaveConfigButtonProps {
  buttonState: ButtonState;
  onSave: () => void;
}

const SaveConfigButton = ({ buttonState, onSave }: SaveConfigButtonProps) => (
  <div className="sticky bottom-[calc(5rem+env(safe-area-inset-bottom))] z-20 ml-auto mt-6 w-full rounded-2xl border border-zinc-200/80 bg-white/95 p-2 shadow-lg backdrop-blur sm:bottom-4 sm:w-fit">
    <Button
      onClick={onSave}
      disabled={buttonState.isDisabled}
      variant="toolbar"
      size="toolbar"
      className="h-11 w-full rounded-xl bg-zinc-900 px-6 font-syne text-white hover:bg-zinc-800 disabled:cursor-not-allowed"
    >
      {buttonState.isLoading ? (
        <div className="flex items-center justify-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
          {buttonState.text}
        </div>
      ) : (
        <><Save className="h-4 w-4" aria-hidden="true" />{buttonState.text}</>
      )}
    </Button>
  </div>
);

export default SaveConfigButton;
