"use client";
import { AlertTriangle } from "lucide-react";
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

/**
 * Asks before exporting a deck that still has pages flagged for review. The
 * caller opens it only when the review count is above zero.
 */
const ExportReviewConfirmDialog = ({
  open,
  count,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  count: number;
  onConfirm: () => void;
  onCancel: () => void;
}) => {
  const t = useTranslations("pageQualityReview");
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onCancel(); }}>
      <DialogContent className="max-w-[400px] gap-0 p-0" data-testid="export-review-confirm">
        <DialogHeader className="items-center px-6 pb-6 pt-8 text-center sm:px-8">
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-50">
            <AlertTriangle className="h-6 w-6 text-amber-600" />
          </div>
          <DialogTitle className="text-xl font-semibold leading-7 tracking-tight text-gray-900">
            {t("exportConfirm.title")}
          </DialogTitle>
          <DialogDescription className="text-sm leading-relaxed text-gray-500">
            {t("exportConfirm.description", { count })}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex-row px-6 pb-6 pt-0 sm:px-8">
          <Button
            type="button"
            variant="ghost"
            onClick={onCancel}
            className="flex-1 inline-flex min-h-10 items-center justify-center rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
          >
            {t("exportConfirm.cancel")}
          </Button>
          <Button
            type="button"
            variant="ghost"
            data-testid="export-review-confirm-proceed"
            onClick={onConfirm}
            className="flex-1 inline-flex min-h-10 items-center justify-center rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700"
          >
            {t("exportConfirm.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default ExportReviewConfirmDialog;
