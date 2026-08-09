"use client";

import { useState, useTransition } from "react";
import { useLocale } from "next-intl";

import { setUiLocale } from "@/app/actions/set-ui-locale";
import { isUiLocale, type UiLocale } from "@/i18n/locale";
import { UiLocaleSelect } from "./UiLocaleSelect";

export function UiLocaleSwitcher({ className }: { className?: string }) {
  const locale = useLocale();
  const [failed, setFailed] = useState(false);
  const [isPending, startTransition] = useTransition();

  const changeLocale = (nextLocale: UiLocale) => {
    if (!isUiLocale(nextLocale) || nextLocale === locale) return;
    setFailed(false);
    startTransition(async () => {
      try {
        await setUiLocale(nextLocale);
      } catch {
        setFailed(true);
      }
    });
  };

  return (
    <UiLocaleSelect
      className={className}
      failed={failed}
      isPending={isPending}
      locale={isUiLocale(locale) ? locale : "ko"}
      onLocaleChange={changeLocale}
    />
  );
}
