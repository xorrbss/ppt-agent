import React from "react";
import { Loader2, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { ButtonState } from "./types";

interface SaveConfigButtonProps {
  buttonState: ButtonState;
  onSave: () => void;
}

const SaveConfigButton = ({ buttonState, onSave }: SaveConfigButtonProps) => (
  <div className=" mx-auto fixed bottom-20 right-5 ">
    <Button
      onClick={onSave}
      disabled={buttonState.isDisabled}
      variant="toolbar"
      size="toolbar"
      className="h-11 w-full px-5 font-syne disabled:cursor-not-allowed"
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
