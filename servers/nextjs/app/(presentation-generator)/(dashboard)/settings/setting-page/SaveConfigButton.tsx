import React from "react";
import { Loader2, ChevronRight } from "lucide-react";
import type { ButtonState } from "./types";

interface SaveConfigButtonProps {
  buttonState: ButtonState;
  onSave: () => void;
}

const SaveConfigButton = ({ buttonState, onSave }: SaveConfigButtonProps) => (
  <div className=" mx-auto fixed bottom-20 right-5 ">
    <button
      onClick={onSave}
      disabled={buttonState.isDisabled}
      style={{
        background:
          "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)",
        color: "#101323",
      }}
      className={`w-full font-syne font-semibold flex items-center justify-center gap-2 py-3 px-5 rounded-[58px] transition-all duration-500 ${buttonState.isDisabled
        ? "bg-gray-400 cursor-not-allowed"
        : "bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 focus:ring-4 focus:ring-blue-200"
        } text-white`}
    >
      {buttonState.isLoading ? (
        <div className="flex items-center justify-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" />
          {buttonState.text}
        </div>
      ) : (
        buttonState.text
      )}
      <ChevronRight className="w-4 h-4" />
    </button>
  </div>
);

export default SaveConfigButton;
