"use client";

import { useTranslations } from "next-intl";

import { Toaster } from "@/components/ui/sonner";

export function LocalizedToaster() {
  const t = useTranslations("common");

  return (
    <Toaster
      closeButtonAriaLabel={t("closeNotification")}
      closeLabel={t("gotIt")}
      position="top-center"
    />
  );
}
