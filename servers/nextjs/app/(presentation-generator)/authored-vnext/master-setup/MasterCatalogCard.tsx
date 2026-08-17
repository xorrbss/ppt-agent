"use client";

import { useTranslations } from "next-intl";

import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type { MasterCatalogSummary } from "@/lib/authored-vnext/master/contracts";
import { companyStandardDisplayName } from "@/lib/authored-vnext/master/selection-storage";

const masterClient = new AuthoredVnextMasterClient();

interface MasterCatalogCardProps {
  entry: MasterCatalogSummary;
  pending: boolean;
  onEdit: () => void;
  onSelect: () => void;
}

/** Presentation-safe card: transport identifiers and digests never leave this boundary. */
export default function MasterCatalogCard({
  entry,
  pending,
  onEdit,
  onSelect,
}: MasterCatalogCardProps) {
  const t = useTranslations("authoredVnext.master.catalog");
  const selectable = entry.publishedRevision !== null;

  return (
    <article
      className={`rounded-xl border bg-white p-4 shadow-sm ${pending ? "border-violet-500 ring-2 ring-violet-100" : "border-slate-200"}`}
    >
      <button
        aria-disabled={!selectable}
        aria-pressed={pending}
        className="w-full text-left disabled:cursor-default"
        disabled={!selectable}
        onClick={onSelect}
        type="button"
      >
        <div className="relative mb-4 aspect-video overflow-hidden rounded-lg border border-slate-200 bg-white">
          <div className="absolute inset-x-0 top-0 h-[5%] bg-blue-600" />
          <div className="absolute left-[6%] top-[12%] h-[5%] w-[24%] rounded-sm bg-blue-100" />
          <div className="absolute left-[25%] top-[24%] h-[12%] w-[50%] rounded-sm bg-slate-800" />
          <div className="absolute left-[7%] top-[46%] h-[6%] w-[70%] rounded-sm bg-slate-100" />
          <div className="absolute left-[7%] top-[58%] h-[6%] w-[58%] rounded-sm bg-slate-100" />
          <div className="absolute inset-x-[6%] bottom-[11%] h-px bg-slate-300" />
          <div className="absolute bottom-[3%] left-1/2 h-[6%] w-[7%] -translate-x-1/2 rounded-sm bg-slate-200" />
          {entry.logo ? (
            <img
              alt={entry.logo.altText}
              className="absolute bottom-[2.5%] right-[6%] h-[9%] w-auto max-w-[22%] object-contain"
              src={masterClient.assetUrl(entry.logo)}
            />
          ) : null}
        </div>
        <div className="flex items-start justify-between gap-3">
          <h3 className="font-bold text-slate-950">
            {companyStandardDisplayName(entry.name, entry.masterId)} · v{entry.publishedRevision}
          </h3>
          <span className={`shrink-0 rounded-full px-2 py-1 text-xs font-semibold ${selectable ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900"}`}>
            {selectable ? t("published") : t("draft")}
          </span>
        </div>
        <p className="mt-2 text-sm text-slate-600">
          PPT 마스터 기반 · {t("cardDetails", { version: entry.semanticVersion, layouts: entry.layoutCount })}
        </p>
        <p className="mt-1 text-xs text-slate-500">
          {t("cardStyle", { font: entry.primaryFont, logo: entry.logo ? t("hasLogo") : t("noLogo") })}
        </p>
      </button>
      <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-100 pt-3">
        <span className="text-xs text-slate-500">
          {selectable ? t("readyToApply") : t("continueDraft")}
        </span>
        <button
          className="rounded border border-slate-300 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50"
          onClick={onEdit}
          type="button"
        >
          {selectable ? t("edit") : t("continueEditing")}
        </button>
      </div>
    </article>
  );
}
