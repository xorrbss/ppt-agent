/**
 * The "review outline before creating" checkbox paired with the primary
 * generate button, shown at the end of the compose card's settings row.
 * Extracted from UploadPage.tsx (file-size ratchet split); behaviour
 * unchanged, only the module boundary moved.
 */
import { ArrowUp } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

interface GenerateActionRowProps {
  reviewBeforeGeneration: boolean;
  onReviewBeforeGenerationChange: (checked: boolean) => void;
  onGenerate: () => void;
  disabled: boolean;
  generating: boolean;
}

export const GenerateActionRow = ({
  reviewBeforeGeneration,
  onReviewBeforeGenerationChange,
  onGenerate,
  disabled,
  generating,
}: GenerateActionRowProps) => {
  const t = useTranslations("upload");
  return (
    <>
      <label className="flex min-w-0 cursor-pointer items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-slate-700 transition-colors hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800">
        <input
          type="checkbox"
          checked={reviewBeforeGeneration}
          onChange={(event) =>
            onReviewBeforeGenerationChange(event.target.checked)
          }
          data-testid="review-outline-checkbox"
          className="mt-0.5 h-4 w-4 shrink-0 accent-violet-600"
        />
        <span className="min-w-0">
          <span className="block font-medium">
            {t("simplify.reviewBeforeCreate")}
          </span>
          <span className="block text-xs text-slate-500 dark:text-slate-400">
            {t("simplify.reviewBeforeCreateDescription")}
          </span>
        </span>
      </label>
      <Button
        type="button"
        variant="toolbar"
        size="toolbar"
        onClick={onGenerate}
        disabled={disabled}
        data-testid="generate-presentation-button"
        className="h-11 shrink-0 rounded-xl bg-slate-900 px-5 text-white shadow-sm hover:bg-slate-800 focus-visible:ring-violet-400 disabled:cursor-not-allowed"
      >
        <ArrowUp className="h-4 w-4" aria-hidden="true" />
        <span>
          {generating
            ? t("generating")
            : t("simplify.createPresentation")}
        </span>
      </Button>
    </>
  );
};

export default GenerateActionRow;
