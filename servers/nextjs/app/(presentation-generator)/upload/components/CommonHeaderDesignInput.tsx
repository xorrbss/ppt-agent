"use client";
import type { PresentationConfig } from "../type";
import { useTranslations } from "next-intl";

export default function CommonHeaderDesignInput({ config, onConfigChange }: {
  config: PresentationConfig;
  onConfigChange: (key: keyof PresentationConfig, value: unknown) => void;
}) {
  const t = useTranslations("uploadExtras");
  return <div className="basis-full space-y-1 text-sm">
    <label className="block text-slate-700">
      {t("commonHeaderInstructionsLabel")}
      <textarea
        aria-label={t("commonHeaderInstructionsLabel")}
        value={config.commonHeaderDesign?.instructions ?? ""}
        onChange={(event) => onConfigChange("commonHeaderDesign", { ...config.commonHeaderDesign, instructions: event.target.value })}
        placeholder={t("commonHeaderInstructionsPlaceholder")}
        rows={2}
        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2"
      />
    </label>
    <label className="block text-slate-700">
      {t("commonHeaderDocumentLabel")}
      <input
        aria-label={t("commonHeaderDocumentLabel")}
        value={config.commonHeaderDesign?.document_values?.document_label ?? ""}
        onChange={(event) => onConfigChange("commonHeaderDesign", { ...config.commonHeaderDesign, document_values: { ...config.commonHeaderDesign?.document_values, document_label: event.target.value } })}
        placeholder={t("commonHeaderDocumentPlaceholder")}
        className="mt-1 w-full rounded-md border border-slate-200 bg-white px-3 py-2"
      />
    </label>
    <p className="text-xs text-slate-600">{t("commonHeaderComparisonNote")}</p>
  </div>;
}
