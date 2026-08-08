"use client";

import React, { memo, useMemo } from "react";

import {
  makeTemplateV2SelectionId,
  StructuredTemplateSummary,
} from "@/app/hooks/useStructuredTemplates";
import { cn } from "@/lib/utils";

function getSourceLabel(template: StructuredTemplateSummary): string {
  if (typeof template.source === "string" && template.source.trim()) {
    return template.source;
  }
  if (template.source && typeof template.source === "object") {
    const repository = template.source.repository
      .replace(/^https?:\/\//, "")
      .replace(/\/$/, "");
    return `${repository} · ${template.source.path}`;
  }
  return (
    template.provenance?.source_name ??
    template.provenance?.source ??
    template.provenance?.repository ??
    "Template V2 catalog"
  );
}

function getCapabilityLabels(template: StructuredTemplateSummary) {
  const directBadges = template.capability_badges?.filter(Boolean) ?? [];
  if (directBadges.length > 0) return directBadges;

  if (Array.isArray(template.capabilities)) return template.capabilities.filter(Boolean);

  if (template.capabilities) {
    return Object.entries(template.capabilities)
      .filter(([, value]) => Boolean(value))
      .map(([name, value]) => (typeof value === "string" ? value : name));
  }

  return ["Editable PPTX"];
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
  const selectionId = makeTemplateV2SelectionId(template.id, template.revision);
  const isSelected = selectedTemplate === selectionId;
  const sourceLabel = useMemo(() => getSourceLabel(template), [template]);
  const capabilityLabels = useMemo(() => getCapabilityLabels(template), [template]);

  return (
    <button
      type="button"
      aria-pressed={isSelected}
      className={cn(
        "font-syne flex min-h-56 w-full cursor-pointer flex-col justify-between overflow-hidden rounded-[22px] border bg-white text-left transition-all duration-200 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2",
        isSelected
          ? "border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
          : "border-[#E8E9EC]"
      )}
      data-testid={`structured-template-${template.id}`}
      onClick={() => onSelectTemplate(selectionId)}
    >
      <div className="flex flex-1 items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 px-6 py-5 text-center">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-700">
            Template V2
          </div>
          <div className="mt-2 flex flex-wrap justify-center gap-2 text-xs font-medium text-slate-700">
            <span className="rounded-full bg-white/80 px-2 py-1">Revision {template.revision}</span>
            {template.is_default ? (
              <span className="rounded-full bg-blue-700 px-2 py-1 text-white">Default</span>
            ) : null}
          </div>
        </div>
      </div>
      <div className="border-t border-[#EDEEEF] bg-white px-6 py-5">
        <h3 className="text-sm font-bold text-gray-900">{template.name}</h3>
        {template.description ? (
          <p className="mt-1 line-clamp-2 text-xs text-gray-600">{template.description}</p>
        ) : null}
        <p
          className="mt-3 truncate text-xs font-medium text-slate-500"
          data-testid={`structured-template-source-${template.id}`}
          title={sourceLabel}
        >
          Source: {sourceLabel}
        </p>
        <div
          className="mt-2 flex flex-wrap gap-1.5"
          data-testid={`structured-template-capabilities-${template.id}`}
        >
          {capabilityLabels.map((capability) => (
            <span
              className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600"
              key={capability}
            >
              {capability}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
});
