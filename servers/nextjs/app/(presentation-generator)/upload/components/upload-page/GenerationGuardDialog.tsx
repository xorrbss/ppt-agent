"use client";

import React from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import type { GuardChoice } from "./generationGuardDecision";
import { pagesLabel } from "./generationGuardDecision";
import type { GenerationGuardPrompt } from "./useGenerationGuard";

type GenerationGuardDialogProps = {
  prompt: GenerationGuardPrompt | null;
  onChoose: (choice: GuardChoice) => void;
  onDismiss: () => void;
};

const GenerationGuardDialog: React.FC<GenerationGuardDialogProps> = ({
  prompt,
  onChoose,
  onDismiss,
}) => {
  const t = useTranslations("generationGuard");
  const busy = prompt?.cancelling ?? false;
  return (
    <Dialog open={Boolean(prompt)} onOpenChange={(open) => { if (!open) onDismiss(); }}>
      <DialogContent data-testid="generation-guard-dialog">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>
        <ul className="space-y-2 text-sm text-[#33363A]" data-testid="generation-guard-items">
          {(prompt?.items ?? []).map((item) => (
            <li
              key={item.operationId}
              className="flex items-center justify-between gap-3 rounded-lg border border-[#EDEEEF] px-3 py-2"
            >
              <span className="min-w-0 truncate">
                {item.presentationTitle ?? item.sourceFileName ?? t("untitled")}
              </span>
              <span className="shrink-0 text-xs text-[#6B6F73]">
                {item.state === "running"
                  ? t("running", { pages: pagesLabel(item.pages) })
                  : item.queuePosition !== null
                    ? t("queuedAt", { position: item.queuePosition })
                    : t("queued")}
              </span>
            </li>
          ))}
        </ul>
        {prompt?.error && (
          <p role="alert" className="text-sm text-red-600" data-testid="generation-guard-error">
            {prompt.error}
          </p>
        )}
        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => onChoose("view")}
            data-testid="generation-guard-view"
          >
            {t("view")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={() => onChoose("cancel_and_start")}
            data-testid="generation-guard-cancel-start"
          >
            {t("cancelAndStart")}
          </Button>
          <Button
            type="button"
            disabled={busy}
            onClick={() => onChoose("queue")}
            data-testid="generation-guard-queue"
          >
            {t("queue")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default GenerationGuardDialog;
