"use client";

import { Ellipsis, RotateCcw, ScanSearch } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import AuthoredPageRemake from "../AuthoredPageRemake";
import VersionHistoryPopover from "../VersionHistoryPopover";
import type {
  WorkspaceCommandDispatcher,
  WorkspacePageRemakeRequester,
  WorkspaceQualityReviewRequester,
} from "../../workspace";

const menuButtonClass =
  "h-9 w-full justify-start rounded-lg px-3 text-sm font-medium shadow-none";

interface HeaderMoreMenuProps {
  presentationId: string;
  isSvgDeck?: boolean;
  canRemakePage?: boolean;
  currentSlide?: number;
  isStreaming: boolean | null;
  isPresentationSaving: boolean;
  mutationsEnabled: boolean;
  historyEnabled: boolean;
  workspaceCommandsEnabled: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  onWorkspaceQualityReviewRequest?: WorkspaceQualityReviewRequester;
  onWorkspacePageRemakeRequest?: WorkspacePageRemakeRequester;
  onQualityReview: () => void;
  onRegenerate: () => void;
}

const HeaderMoreMenu = ({
  presentationId,
  isSvgDeck = false,
  canRemakePage = false,
  currentSlide,
  isStreaming,
  isPresentationSaving,
  mutationsEnabled,
  historyEnabled,
  workspaceCommandsEnabled,
  onWorkspaceCommand,
  onWorkspaceQualityReviewRequest,
  onWorkspacePageRemakeRequest,
  onQualityReview,
  onRegenerate,
}: HeaderMoreMenuProps) => {
  const t = useTranslations("presentationHeader");
  const [open, setOpen] = useState(false);
  const closeThen = (action: () => void) => {
    setOpen(false);
    action();
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="toolbar"
          size="icon"
          className="h-[38px] w-[38px] shrink-0 rounded-full"
          data-testid="presentation-more-trigger"
          aria-label={t("moreAria")}
          title={t("more")}
        >
          <Ellipsis className="h-4 w-4" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="max-h-[min(640px,calc(100vh-5rem))] w-[min(26rem,calc(100vw-2rem))] overflow-y-auto rounded-[18px] p-3"
      >
        <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
          {t("actions")}
        </p>
        <div className="space-y-1">
          {canRemakePage ? (
            <div
              className="[&>button]:h-9 [&>button]:w-full [&>button]:justify-start [&>button]:rounded-lg [&>button]:border-0 [&>button]:px-3 [&>button]:text-sm [&>button]:font-medium [&>button]:shadow-none"
            >
              <AuthoredPageRemake
                currentSlide={currentSlide}
                disabled={
                  !workspaceCommandsEnabled ||
                  !onWorkspaceCommand ||
                  isStreaming === true ||
                  isPresentationSaving
                }
                workspaceCommandsEnabled={workspaceCommandsEnabled}
                onWorkspacePageRemakeRequest={onWorkspacePageRemakeRequest}
              />
            </div>
          ) : null}
          {isSvgDeck ? (
            <Button
              type="button"
              variant="ghost"
              className={menuButtonClass}
              disabled={
                !workspaceCommandsEnabled ||
                !onWorkspaceQualityReviewRequest ||
                isStreaming === true
              }
              onClick={() => closeThen(onQualityReview)}
              data-testid="workspace-quality-review"
              aria-label={t("qualityReviewAria")}
            >
              <ScanSearch className="h-4 w-4" aria-hidden="true" />
              {t("qualityReview")}
            </Button>
          ) : null}
          {mutationsEnabled ? (
            <Button
              type="button"
              variant="ghost"
              className={menuButtonClass}
              onClick={() => closeThen(onRegenerate)}
              data-testid="presentation-regenerate"
              aria-label={t("regenerate")}
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              {t("regenerate")}
            </Button>
          ) : null}
          <VersionHistoryPopover
            presentationId={presentationId}
            disabled={isStreaming === true}
            allowRestore={historyEnabled}
            workspaceCommandsEnabled={workspaceCommandsEnabled}
            onWorkspaceCommand={onWorkspaceCommand}
            triggerLabel={t("versionHistory")}
          />
        </div>
      </PopoverContent>
    </Popover>
  );
};

export default HeaderMoreMenu;
