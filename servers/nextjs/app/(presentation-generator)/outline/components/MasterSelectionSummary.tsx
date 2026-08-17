"use client";

import { Button } from "@/components/ui/button";
import type { CompanyStandardSelection } from "@/store/slices/presentationGenUpload";
import { Settings2 } from "lucide-react";
import { useTranslations } from "next-intl";

interface MasterSelectionSummaryProps {
  templateName: string;
  selection: CompanyStandardSelection | null;
  onOpen: () => void;
}

export default function MasterSelectionSummary({
  templateName,
  selection,
  onOpen,
}: MasterSelectionSummaryProps) {
  const t = useTranslations("authoredVnext.master.entry");
  return (
    <section
      className="flex min-w-0 flex-col gap-2 rounded-2xl border border-[#EDEEEF] bg-white px-4 py-3 shadow-sm sm:flex-row sm:items-center sm:justify-between"
      aria-label={t("aria")}
      data-testid="outline-master-summary"
    >
      <div className="min-w-0">
        <p className="truncate text-xs text-[#667085]">
          {t("templateLabel")}: {templateName}
        </p>
        <p className="text-xs font-semibold text-[#7E3AF2]">{t("label")}</p>
        {selection ? (
          <>
            <p className="truncate text-sm font-semibold text-[#2D2D2D]">
              {selection.name} · v{selection.revision} / PPT 마스터 기반
            </p>
            <p className="truncate text-xs text-[#667085]">
              {selection.hasLogo ? t("logoOn") : t("logoOff")}
              {selection.primaryFont ? ` · ${selection.primaryFont}` : ""}
              {selection.layoutCount > 0
                ? ` · ${t("layouts", { count: selection.layoutCount })}`
                : ""}
            </p>
          </>
        ) : (
          <p className="text-xs text-[#667085]">
            {t("prompt")}
          </p>
        )}
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="shrink-0 gap-2"
        onClick={onOpen}
        data-testid="open-master-designer"
      >
        <Settings2 className="h-4 w-4" aria-hidden />
        {t("button")}
      </Button>
    </section>
  );
}
