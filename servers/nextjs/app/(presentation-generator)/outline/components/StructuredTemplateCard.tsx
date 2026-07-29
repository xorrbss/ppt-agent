"use client";

import React, { memo } from "react";

import {
  makeTemplateV2SelectionId,
  StructuredTemplateSummary,
} from "@/app/hooks/useStructuredTemplates";
import { cn } from "@/lib/utils";

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

  return (
    <button
      type="button"
      aria-pressed={isSelected}
      className={cn(
        "font-syne flex min-h-48 w-full cursor-pointer flex-col justify-between overflow-hidden rounded-[22px] border bg-white text-left transition-all duration-200 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2",
        isSelected
          ? "border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
          : "border-[#E8E9EC]"
      )}
      data-testid={`structured-template-${template.id}`}
      onClick={() => onSelectTemplate(selectionId)}
    >
      <div className="flex flex-1 items-center justify-center bg-gradient-to-br from-blue-50 to-slate-100 px-6 text-center">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-blue-700">
            Structured Template
          </div>
          <div className="mt-2 text-sm text-slate-600">
            Revision {template.revision}
          </div>
        </div>
      </div>
      <div className="border-t border-[#EDEEEF] bg-white px-6 py-5">
        <h3 className="text-sm font-bold text-gray-900">{template.name}</h3>
        {template.description ? (
          <p className="mt-1 line-clamp-2 text-xs text-gray-600">
            {template.description}
          </p>
        ) : null}
      </div>
    </button>
  );
});
