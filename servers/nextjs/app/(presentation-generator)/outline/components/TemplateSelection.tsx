"use client";
import React, { useEffect, useMemo, useCallback, memo } from "react";

import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { selectableTemplates } from "@/app/presentation-templates";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { CustomTemplates, useCustomTemplateSummaries } from "@/app/hooks/useCustomTemplates";
import {
  TEMPLATE_V2_SELECTION_PREFIX,
  useStructuredTemplateSummaries,
} from "@/app/hooks/useStructuredTemplates";

import CreateCustomTemplate from "../../(dashboard)/templates/components/CreateCustomTemplate";
import AuthoredStylePicker from "./AuthoredStylePicker";
import { CustomTemplateCard } from "./CustomTemplateCard";
import { StructuredTemplateCard } from "./StructuredTemplateCard";
import {
  TemplatePreviewStage,
  InbuiltTemplatePreview,
} from "../../components/TemplatePreviewComponents";

const BuiltInTemplateCard = memo(function BuiltInTemplateCard({
  template,
  isSelected,
  onSelect,
}: {
  template: TemplateLayoutsWithSettings;
  isSelected: boolean;
  onSelect: (template: TemplateLayoutsWithSettings) => void;
}) {
  const handleClick = useCallback(() => onSelect(template), [onSelect, template]);

  return (
    <Card
      className={cn(
        "cursor-pointer relative hover:shadow-sm transition-all duration-200 group overflow-hidden rounded-[22px] bg-white border",
        isSelected
          ? " border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
          : " border-[#E8E9EC]"
      )}
      onClick={handleClick}
    >
      <TemplatePreviewStage>
        <InbuiltTemplatePreview layouts={template.layouts} templateId={template.id} isOutline={true} />
      </TemplatePreviewStage>
      <div className="flex items-center justify-between px-6 py-5 bg-white border-t border-[#EDEEEF] relative z-40">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-bold text-gray-900 capitalize font-syne">
            {template.name}
          </h3>
          <p className="text-xs text-gray-600 line-clamp-2 font-syne">
            {template.description}
          </p>
        </div>
      </div>
    </Card>
  );
});

// Sentinel id for the authored (high-quality) mode — not a real layout template.
export const AUTHORED_TEMPLATE_ID = "authored";

interface TemplateSelectionProps {
  selectedTemplate: (TemplateLayoutsWithSettings | string) | null;
  onSelectTemplate: (template: TemplateLayoutsWithSettings | string) => void;
}

const TemplateSelection: React.FC<TemplateSelectionProps> = memo(function TemplateSelection({
  selectedTemplate,
  onSelectTemplate,
}) {
  useEffect(() => {
    const existingScript = document.querySelector(
      'script[src*="tailwindcss.com"]'
    );
    if (!existingScript) {
      const script = document.createElement("script");
      script.src = "https://cdn.tailwindcss.com";
      script.async = true;
      document.head.appendChild(script);
    }
  }, []);

  const { templates: customTemplates, loading: customLoading } = useCustomTemplateSummaries();
  const { templates: structuredTemplates, loading: structuredLoading } =
    useStructuredTemplateSummaries();
  const handleAuthoredActivate = useCallback(
    () => onSelectTemplate(AUTHORED_TEMPLATE_ID),
    [onSelectTemplate]
  );

  const handleCustomSelect = useCallback(
    (template: TemplateLayoutsWithSettings | string) => onSelectTemplate(template),
    [onSelectTemplate]
  );

  const handleBuiltInSelect = useCallback(
    (template: TemplateLayoutsWithSettings) => onSelectTemplate(template),
    [onSelectTemplate]
  );

  const selectedCustomId = useMemo(
    () =>
      typeof selectedTemplate === "string" &&
      !selectedTemplate.startsWith(TEMPLATE_V2_SELECTION_PREFIX)
        ? selectedTemplate
        : null,
    [selectedTemplate]
  );

  const selectedStructuredId = useMemo(
    () =>
      typeof selectedTemplate === "string" &&
      selectedTemplate.startsWith(TEMPLATE_V2_SELECTION_PREFIX)
        ? selectedTemplate
        : null,
    [selectedTemplate]
  );

  const selectedBuiltInId = useMemo(
    () => (typeof selectedTemplate !== "string" ? selectedTemplate?.id ?? null : null),
    [selectedTemplate]
  );

  // Custom template cards as a flat list so they can flow inline in the unified
  // grid (after the "create" card, before the built-in templates). Empty while
  // loading — built-in templates and the create card render immediately.
  const customTemplateCards = useMemo(
    () =>
      customLoading
        ? []
        : customTemplates.map((template: CustomTemplates) => (
            <CustomTemplateCard
              key={template.id}
              template={template}
              onSelectTemplate={handleCustomSelect}
              selectedTemplate={selectedCustomId}
            />
          )),
    [customLoading, customTemplates, handleCustomSelect, selectedCustomId]
  );

  const builtInTemplateCards = useMemo(
    () =>
      selectableTemplates.map((template: TemplateLayoutsWithSettings) => (
        <BuiltInTemplateCard
          key={template.id}
          template={template}
          isSelected={selectedBuiltInId === template.id}
          onSelect={handleBuiltInSelect}
        />
      )),
    [selectedBuiltInId, handleBuiltInSelect]
  );

  const structuredTemplateCards = useMemo(
    () =>
      structuredLoading
        ? []
        : structuredTemplates.map((template) => (
            <StructuredTemplateCard
              key={template.id}
              template={template}
              onSelectTemplate={handleCustomSelect}
              selectedTemplate={selectedStructuredId}
            />
          )),
    [
      handleCustomSelect,
      selectedStructuredId,
      structuredLoading,
      structuredTemplates,
    ]
  );

  return (
    <div className="mb-4 space-y-8">
      <section
        aria-labelledby="authored-template-heading"
        data-testid="authored-template-section"
      >
        <div className="mb-4">
          <h2
            id="authored-template-heading"
            className="text-base font-bold text-gray-900 font-syne"
          >
            AI 저작 템플릿(고품질)
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-gray-600">
            AI가 슬라이드별 디자인을 직접 저작하며, 인앱에서는 보기 전용으로 제공됩니다.
          </p>
        </div>
        <AuthoredStylePicker
          isActive={selectedTemplate === AUTHORED_TEMPLATE_ID}
          onActivate={handleAuthoredActivate}
        />
      </section>

      <section
        aria-labelledby="layout-template-heading"
        data-testid="layout-template-section"
      >
        <div className="mb-4">
          <h2
            id="layout-template-heading"
            className="text-base font-bold text-gray-900 font-syne"
          >
            레이아웃 템플릿(편집 가능 PPTX)
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-gray-600">
            기존 레이아웃을 사용해 PowerPoint에서 편집 가능한 슬라이드를 만듭니다.
          </p>
        </div>
        <div
          data-testid="layout-template-grid"
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
        >
          <CreateCustomTemplate />
          {structuredTemplateCards}
          {customTemplateCards}
          {builtInTemplateCards}
        </div>
      </section>
    </div>
  );
});

export default TemplateSelection;
