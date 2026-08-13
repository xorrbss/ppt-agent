"use client";

import Image from "next/image";
import React, { memo, useMemo } from "react";
import { useTranslations } from "next-intl";

import {
  makeTemplateV2SelectionId,
  StructuredTemplateSummary,
} from "@/app/hooks/useStructuredTemplates";
import { cn } from "@/lib/utils";
import { resolveBackendAssetUrl } from "@/utils/api";

function getSourceLabel(template: StructuredTemplateSummary): string {
  if (typeof template.source === "string" && template.source.trim()) {
    return template.source;
  }
  if (template.source && typeof template.source === "object") {
    const repository = template.source.repository
      .replace(/^https?:\/\//, "")
      .replace(/\/$/, "");
    if (repository === "github.com/presenton/presenton") return "Presenton";
    return template.source.path
      ? `${repository} · ${template.source.path}`
      : repository;
  }
  return template.provenance?.source === "verified-template-package"
    ? "Presenton"
    : "로컬 카탈로그";
}

function getRawCapabilities(template: StructuredTemplateSummary): string[] {
  const directBadges = template.capability_badges?.filter(Boolean) ?? [];
  if (directBadges.length > 0) return directBadges;
  if (Array.isArray(template.capabilities)) {
    return template.capabilities.filter(Boolean);
  }
  if (!template.capabilities) return [];
  return Object.entries(template.capabilities)
    .filter(([, value]) => Boolean(value))
    .map(([name, value]) => (typeof value === "string" ? value : name));
}

function getCapabilityLabels(template: StructuredTemplateSummary): string[] {
  const raw = getRawCapabilities(template);
  const labels: string[] = [];
  const add = (label: string) => {
    if (!labels.includes(label)) labels.push(label);
  };
  const includes = (...tokens: string[]) =>
    raw.some((item) => tokens.some((token) => item.toLowerCase().includes(token)));

  if (
    raw.length === 0 ||
    includes("native_pptx", "editable pptx", "element.text", "editable_text")
  ) {
    add("편집 가능 PPTX");
  }
  if (includes("chart.")) add("차트");
  if (includes("element.table", "editable_tables", "table_patch")) add("표");
  if (includes("element.image", "image_replacement")) add("이미지");
  if (includes("master retained", "master_layout_theme")) add("마스터 유지");
  if (labels.length === 1 && includes("editable_shapes", "element.container")) {
    add("도형 편집");
  }
  return labels.slice(0, 3);
}

function getLayoutCount(template: StructuredTemplateSummary): number | null {
  const layouts = Array.isArray(template.layouts)
    ? template.layouts
    : template.layouts?.layouts;
  return Array.isArray(layouts) ? layouts.length : null;
}

function getThumbnail(template: StructuredTemplateSummary): string | null {
  const thumbnail = template.assets?.thumbnail;
  return typeof thumbnail === "string" && thumbnail.trim()
    ? resolveBackendAssetUrl(thumbnail.trim())
    : null;
}

function TemplatePreview({ template }: { template: StructuredTemplateSummary }) {
  const t = useTranslations("generation");
  const thumbnail = getThumbnail(template);

  return (
    <div
      className="relative aspect-video w-full overflow-hidden bg-slate-100"
      data-testid={`structured-template-preview-${template.id}`}
    >
      {thumbnail ? (
        <Image
          alt={t("templatePreviewAlt", { name: template.name })}
          className="object-cover transition-transform duration-300 group-hover:scale-[1.02]"
          fill
          sizes="(min-width: 1280px) 25vw, (min-width: 768px) 33vw, 100vw"
          src={thumbnail}
          unoptimized
        />
      ) : (
        <div className="absolute inset-0 overflow-hidden bg-[linear-gradient(135deg,#0F2747_0%,#173B67_62%,#2563EB_100%)]">
          <div className="absolute -right-8 -top-10 h-40 w-40 rounded-full border border-white/20 bg-white/10" />
          <div className="absolute bottom-5 left-5 right-5">
            <div className="mb-3 h-1 w-10 rounded-full bg-sky-400" />
            <p className="line-clamp-2 text-lg font-bold leading-tight text-white">
              {template.name}
            </p>
            <p className="mt-1 text-[11px] font-medium text-blue-100">
              {t("editablePresentation")}
            </p>
          </div>
        </div>
      )}

      <div className="absolute left-3 top-3 rounded-full bg-slate-950/75 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-white backdrop-blur">
        Template V2
      </div>
      {template.is_default ? (
        <div className="absolute right-3 top-3 rounded-full bg-blue-600 px-2.5 py-1 text-[10px] font-bold text-white shadow-sm">
          {t("defaultTemplate")}
        </div>
      ) : null}
    </div>
  );
}

export const StructuredTemplateCard = memo(function StructuredTemplateCard({
  template,
  onSelectTemplate,
  selectedTemplate,
}: {
  template: StructuredTemplateSummary;
  onSelectTemplate: (template: string) => void;
  selectedTemplate: string | null;
}) {
  const t = useTranslations("generation");
  const selectionId = makeTemplateV2SelectionId(template.id, template.revision);
  const isSelected = selectedTemplate === selectionId;
  const sourceLabel = useMemo(() => getSourceLabel(template), [template]);
  const capabilityLabels = useMemo(() => getCapabilityLabels(template), [template]);
  const layoutCount = useMemo(() => getLayoutCount(template), [template]);

  return (
    <button
      type="button"
      aria-pressed={isSelected}
      className={cn(
        "group font-syne flex h-full min-h-0 w-full cursor-pointer flex-col overflow-hidden rounded-[20px] border bg-white text-left shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-all duration-200 hover:-translate-y-0.5 hover:border-slate-300 hover:shadow-[0_12px_28px_rgba(15,23,42,0.10)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2",
        isSelected
          ? "border-blue-500 ring-2 ring-blue-500/20 shadow-[0_12px_28px_rgba(37,99,235,0.13)]"
          : "border-slate-200"
      )}
      data-testid={`structured-template-${template.id}`}
      onClick={() => onSelectTemplate(selectionId)}
    >
      <TemplatePreview template={template} />

      <div className="flex min-h-[168px] flex-1 flex-col px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <h3 className="line-clamp-1 text-base font-bold text-slate-950">
            {template.name}
          </h3>
          <span className="shrink-0 text-[10px] font-semibold text-slate-400">
            {t("templateRevision", { revision: template.revision })}
          </span>
        </div>
        {template.description ? (
          <p className="mt-1.5 line-clamp-2 min-h-10 text-xs leading-5 text-slate-600">
            {template.description}
          </p>
        ) : (
          <p className="mt-1.5 min-h-10 text-xs leading-5 text-slate-500">
            {t("genericEditableTemplate")}
          </p>
        )}

        <div className="mt-auto pt-3">
          <div
            className="flex flex-wrap gap-1.5"
            data-testid={`structured-template-capabilities-${template.id}`}
          >
            {layoutCount ? (
              <span className="rounded-full bg-blue-50 px-2 py-1 text-[10px] font-semibold text-blue-700">
                {t("layoutCount", { count: layoutCount })}
              </span>
            ) : null}
            {capabilityLabels.map((capability) => (
              <span
                className="rounded-full border border-slate-200 bg-slate-50 px-2 py-1 text-[10px] font-medium text-slate-600"
                key={capability}
              >
                {capability}
              </span>
            ))}
          </div>
          <p
            className="mt-3 truncate border-t border-slate-100 pt-3 text-[10px] font-medium text-slate-400"
            data-testid={`structured-template-source-${template.id}`}
            title={sourceLabel}
          >
            {sourceLabel}
          </p>
        </div>
      </div>
    </button>
  );
});
