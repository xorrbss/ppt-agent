"use client";
import { Play, Redo2, Undo2 } from "lucide-react";
import { useTranslations } from "next-intl";

import ToolTip from "@/components/ToolTip";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import type { PresentationData } from "@/store/slices/presentationGeneration";

const HeaderToolbar = ({
  presentationData,
  isStreaming,
  historyEnabled,
  canUndo,
  canRedo,
  onUndo,
  onRedo,
  handleStartPresentation,
}: {
  presentationData: PresentationData | null;
  isStreaming: boolean | null;
  historyEnabled: boolean;
  canUndo: boolean;
  canRedo: boolean;
  onUndo: () => void;
  onRedo: () => void;
  handleStartPresentation: () => void;
}) => {
  const t = useTranslations("presentationHeader");

  return (
  <div className="flex items-center gap-2 bg-[#F6F6F9] px-3.5 h-[38px] border border-[#EDECEC] rounded-[80px]">
    {historyEnabled && (
      <>
        <ToolTip content={t("undo")}>
          <button
            type="button"
            disabled={!canUndo}
            data-testid="presentation-undo"
            aria-label={t("undo")}
            className={cn(
              "cursor-pointer group",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
            onClick={onUndo}
          >
            <Undo2 className="w-3.5 h-3.5 text-[#101323] group-hover:text-brand duration-300" />
          </button>
        </ToolTip>
        <Separator orientation="vertical" className="h-4" />
        <ToolTip content={t("redo")}>
          <button
            type="button"
            disabled={!canRedo}
            data-testid="presentation-redo"
            aria-label={t("redo")}
            className={cn(
              "cursor-pointer group",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
            onClick={onRedo}
          >
            <Redo2 className="w-3.5 h-3.5 text-[#101323] group-hover:text-brand duration-300" />
          </button>
        </ToolTip>
        <Separator orientation="vertical" className="h-4" />
      </>
    )}
    <ToolTip content={t("startPresentation")}>
      <button
        type="button"
        onClick={handleStartPresentation}
        disabled={
          isStreaming ||
          !presentationData?.slides ||
          presentationData?.slides.length === 0
        }
        aria-label={t("startPresentation")}
        className="cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed group"
      >
        <Play className="w-3.5 h-3.5 text-[#101323] group-hover:text-brand duration-300" />
      </button>
    </ToolTip>
  </div>
  );
};

export default HeaderToolbar;
