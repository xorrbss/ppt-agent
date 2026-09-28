"use client";

import React, { useCallback } from "react";
import { ArrowRight, Check, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useTranslations } from "next-intl";

import type {
  AuthoredStyleCategory,
  AuthoredStyleSummary,
} from "@/app/(presentation-generator)/services/api/authored";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

import StyleSampleSlides from "./StyleSampleSlides";
import { useStyleSampleNavigation } from "./useStyleSampleNavigation";

interface StyleSampleDialogProps {
  styles: AuthoredStyleSummary[];
  initialStyleId: string;
  selectedStyleId?: string;
  categoryLabels: Record<AuthoredStyleCategory, string>;
  onConfirm: (styleId: string) => void;
  onClose: () => void;
}

const styleNavButtonClass =
  "flex min-h-10 flex-1 items-center justify-center gap-1 rounded-lg border border-[#EBEBEC] bg-white px-3 text-xs font-semibold text-[#3D3F48] transition hover:bg-[#F3F4F6] disabled:cursor-not-allowed disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export default function StyleSampleDialog({
  styles,
  initialStyleId,
  selectedStyleId,
  categoryLabels,
  onConfirm,
  onClose,
}: StyleSampleDialogProps) {
  const t = useTranslations("authoredStyleSamples");
  const { styleIndex, slideIndex, style, goToStyle, goToSlide } =
    useStyleSampleNavigation(styles, initialStyleId);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const delta = event.key === "ArrowLeft" ? -1 : 1;
      event.preventDefault();
      if (event.shiftKey) goToStyle(delta);
      else goToSlide(slideIndex + delta);
    },
    [goToSlide, goToStyle, slideIndex],
  );

  if (!style) return null;
  const isSelected = style.id === selectedStyleId;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        hideDefaultClose
        data-testid="style-sample-dialog"
        data-style-id={style.id}
        onKeyDown={handleKeyDown}
        className="max-w-6xl gap-0 p-0 lg:grid-cols-[minmax(0,300px)_minmax(0,1fr)]"
      >
        <aside className="flex min-w-0 flex-col gap-4 border-b border-[#EBEBEC] p-6 lg:border-b-0 lg:border-r">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-[#6B6D76]">
              {categoryLabels[style.category]}
            </p>
            <DialogTitle className="mt-1 break-words text-lg" data-testid="style-sample-title">
              {style.name}
            </DialogTitle>
            <DialogDescription className="mt-2 text-sm leading-6 text-gray-600">
              {style.description}
            </DialogDescription>
          </div>
          {style.tags.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-[#3D3F48]">{t("tags")}</p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {style.tags.map((tag) => (
                  <li
                    key={tag}
                    className="rounded-full border border-[#E3E4E8] bg-[#F8F8FA] px-2 py-1 text-[11px] font-medium text-[#51535B]"
                  >
                    {tag}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {style.use_cases.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-[#3D3F48]">{t("useCases")}</p>
              <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-5 text-[#555761]">
                {style.use_cases.map((useCase) => (
                  <li key={useCase}>{useCase}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="mt-auto flex flex-col gap-2">
            <p className="text-[11px] tabular-nums text-[#676973]" data-testid="style-sample-style-counter">
              {t("styleCounter", { current: styleIndex + 1, total: styles.length })}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                className={styleNavButtonClass}
                onClick={() => goToStyle(-1)}
                disabled={styleIndex <= 0}
                data-testid="style-sample-prev-style"
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                {t("previousStyle")}
              </button>
              <button
                type="button"
                className={styleNavButtonClass}
                onClick={() => goToStyle(1)}
                disabled={styleIndex >= styles.length - 1}
                data-testid="style-sample-next-style"
              >
                {t("nextStyle")}
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
            <p className="text-[11px] text-[#8A8C95]">{t("keyboardHint")}</p>
          </div>
        </aside>

        <div className="relative min-w-0 p-6 pt-14">
          <DialogClose
            className="absolute right-4 top-4 inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
            aria-label={t("close")}
            data-testid="style-sample-close"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </DialogClose>
          <StyleSampleSlides
            style={style}
            slideIndex={slideIndex}
            onSlideChange={goToSlide}
          />
        </div>

        <div className="sticky bottom-0 flex flex-wrap items-center justify-end gap-3 border-t border-[#EBEBEC] bg-white/95 px-6 py-4 backdrop-blur lg:col-span-2">
          {isSelected && (
            <span
              className="flex items-center gap-1 rounded-md bg-[#F5F9FE] px-2 py-1 text-[11px] font-semibold text-[#366FCF]"
              data-testid="style-sample-selected-mark"
            >
              <Check className="h-3 w-3" aria-hidden="true" />
              {t("currentlySelected")}
            </span>
          )}
          <button
            type="button"
            onClick={() => onConfirm(style.id)}
            data-testid="style-sample-confirm"
            className="flex min-h-11 items-center gap-2 rounded-lg bg-brand px-5 text-sm font-semibold text-white transition hover:bg-[#4334C9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
          >
            {t("useThisStyle")}
            <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
