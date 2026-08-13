"use client";

import Image from "next/image";
import { Check } from "lucide-react";
import { useTranslations } from "next-intl";

import type { StructuredTemplateSummary } from "@/app/hooks/useStructuredTemplates";
import { cn } from "@/lib/utils";
import { resolveBackendAssetUrl } from "@/utils/api";

interface StructuredCatalogCardProps {
  template: StructuredTemplateSummary;
  selected: boolean;
  onSelect: () => void;
}

export function StructuredCatalogCard({ template, selected, onSelect }: StructuredCatalogCardProps) {
  const t = useTranslations("upload.catalog");
  const thumbnail = template.assets?.thumbnail;
  const thumbnailUrl = typeof thumbnail === "string" && thumbnail.trim() ? resolveBackendAssetUrl(thumbnail.trim()) : null;
  return <button type="button" aria-pressed={selected} aria-label={t("useTemplate", { name: template.name })} data-testid={`catalog-structured-${template.id}`} onClick={onSelect} className={cn("group relative min-w-0 overflow-hidden rounded-xl border bg-white text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition duration-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5] focus-visible:ring-offset-2", selected ? "border-[#5141E5] ring-2 ring-[#5141E5]/20" : "border-[#E9EAED] hover:-translate-y-0.5 hover:border-[#C9C6E8] hover:shadow-[0_12px_28px_rgba(15,23,42,0.12)]")}>
    <span className="relative block aspect-video overflow-hidden bg-slate-100">
      {thumbnailUrl ? <Image src={thumbnailUrl} alt={t("previewAlt", { name: template.name })} fill unoptimized loading="lazy" sizes="(max-width: 640px) 50vw, (max-width: 1024px) 45vw, 320px" className="object-cover transition-transform duration-300 group-hover:scale-[1.02] group-focus-visible:scale-[1.02] motion-reduce:transition-none" /> : <span className="absolute inset-0 overflow-hidden bg-[linear-gradient(135deg,#18336C_0%,#24469A_65%,#7190D5_100%)]"><span className="absolute -right-[12%] -top-[30%] h-[95%] w-[58%] rounded-full bg-white/10" /><span className="absolute -bottom-[40%] right-[18%] h-[80%] w-[42%] rounded-full bg-white/20" /><span className="absolute bottom-[20%] left-[7%] right-[8%] line-clamp-2 text-[clamp(11px,1.25vw,20px)] font-bold leading-tight text-white">{template.name}</span></span>}
      <span className="absolute inset-x-0 bottom-3 flex translate-y-3 justify-center opacity-0 transition-all duration-300 group-hover:translate-y-0 group-hover:opacity-100 group-focus-visible:translate-y-0 group-focus-visible:opacity-100 motion-reduce:transition-none"><span className="rounded-full bg-[#22252A] px-4 py-2 text-xs font-semibold text-white shadow-[0_4px_12px_rgba(0,0,0,0.15)]">{t("apply")}</span></span>
      {template.is_default ? <span className="absolute left-2 top-2 rounded-full bg-white/90 px-2 py-1 text-[10px] font-bold text-[#344054] shadow-sm backdrop-blur">{t("defaultBadge")}</span> : null}
      {selected ? <span className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-[#5141E5] text-white shadow-sm"><Check className="h-4 w-4" aria-hidden="true" /></span> : null}
    </span>
    <span className="flex min-h-11 items-center justify-between gap-2 border-t border-[#F0F1F3] px-3 py-2.5"><span className="truncate text-xs font-semibold text-[#303238] sm:text-sm">{template.name}</span><span className="shrink-0 text-[10px] font-medium text-[#90949B]">{t("revision", { revision: template.revision })}</span></span>
  </button>;
}
