"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

export default function NewSlideButton({ disabled = false }: { disabled?: boolean }) {
  const t = useTranslations("slideWorkspace");

  return (
    <Button
      asChild
      variant="toolbar"
      size="toolbar"
      className={disabled ? "pointer-events-none opacity-50" : undefined}
    >
      <Link
        href="/upload"
        aria-disabled={disabled || undefined}
        tabIndex={disabled ? -1 : undefined}
        onClick={(event) => { if (disabled) event.preventDefault(); }}
        data-testid="new-slide-button"
      >
        <Plus aria-hidden="true" />
        {t("newSlide")}
      </Link>
    </Button>
  );
}
