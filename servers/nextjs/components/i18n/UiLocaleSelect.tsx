"use client";

import { useId } from "react";
import { useTranslations } from "next-intl";

import type { UiLocale } from "@/i18n/locale";
import { cn } from "@/lib/utils";

export function UiLocaleSelect({
  className,
  failed,
  isPending,
  locale,
  onLocaleChange,
}: {
  className?: string;
  failed: boolean;
  isPending: boolean;
  locale: UiLocale;
  onLocaleChange: (locale: UiLocale) => void;
}) {
  const t = useTranslations("languageSwitcher");
  const selectId = useId();

  return (
    <div
      className={cn(
        "inline-flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 shadow-sm",
        className,
      )}
    >
      <label htmlFor={selectId} className="font-semibold">
        {t("label")}
      </label>
      <select
        id={selectId}
        aria-busy={isPending}
        className="rounded-md border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        disabled={isPending}
        value={locale}
        onChange={(event) => onLocaleChange(event.target.value as UiLocale)}
      >
        <option value="ko">{t("korean")}</option>
        <option value="en">{t("english")}</option>
      </select>
      {isPending ? (
        <span role="status" aria-live="polite">
          {t("saving")}
        </span>
      ) : null}
      {failed ? (
        <span role="alert" className="text-red-700">
          {t("error")}
        </span>
      ) : null}
    </div>
  );
}
