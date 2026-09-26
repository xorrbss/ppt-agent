"use client";
import { useTranslations as useA2bTranslations } from "next-intl";

/**
 * Redesigning from a source deck runs in one click. It pauses here only when
 * the analysis found content this mode cannot carry over, so the user approves
 * the loss before paying for the generation rather than discovering it after.
 */

import React from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

import type { SourceContentLossPrompt } from "./useSourceContentAdmission";

type SourceContentLossDialogProps = {
  prompt: SourceContentLossPrompt | null;
  onDecide: (accepted: boolean) => void;
  inline?: boolean;
};

const SourceContentLossDialog: React.FC<SourceContentLossDialogProps> = ({
  prompt,
  onDecide,
  inline = false,
}) => {
  const tA2b = useA2bTranslations("uploadExtras");
  if (inline) return prompt ? (
    <section aria-labelledby="source-content-choice-title" className="space-y-3 rounded-2xl border border-amber-200 bg-amber-50/40 p-5" data-testid="source-content-loss-dialog">
      <h2 id="source-content-choice-title" className="font-semibold">{tA2b("reconstructionWillOmitSomeContent")}</h2>
      <p className="text-sm">{tA2b("sourceContentPreservationCarriesOverTextAnd")}</p>
      <ul className="list-disc space-y-1 pl-5 text-sm">{prompt.losses.map((loss) => <li key={loss}>{loss}</li>)}</ul>
      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" onClick={() => onDecide(false)} data-testid="source-content-loss-cancel">{tA2b("cancel")}</Button>
        <Button type="button" onClick={() => onDecide(true)} data-testid="source-content-loss-confirm">{tA2b("reconstructAsShown")}</Button>
      </div>
    </section>
  ) : null;
  return (
  <Dialog
    open={Boolean(prompt)}
    onOpenChange={(open) => {
      if (!open) onDecide(false);
    }}
  >
    <DialogContent data-testid="source-content-loss-dialog">
      <DialogHeader>
        <DialogTitle>{tA2b("reconstructionWillOmitSomeContent")}</DialogTitle>
        <DialogDescription>
          {tA2b("sourceContentPreservationCarriesOverTextAnd")}
        </DialogDescription>
      </DialogHeader>
      <ul className="list-disc space-y-1 pl-5 text-sm text-[#33363A]">
        {(prompt?.losses ?? []).map((loss) => (
          <li key={loss}>{loss}</li>
        ))}
      </ul>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={() => onDecide(false)}
          data-testid="source-content-loss-cancel"
        >
          {tA2b("cancel")}
        </Button>
        <Button
          type="button"
          onClick={() => onDecide(true)}
          data-testid="source-content-loss-confirm"
        >
          {tA2b("reconstructAsShown")}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
  );
};

export default SourceContentLossDialog;
