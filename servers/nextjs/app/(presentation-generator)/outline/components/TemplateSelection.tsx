"use client";
import React, { useEffect, useMemo, useCallback, memo } from "react";

import { useDispatch, useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { setPptGenUploadState } from "@/store/slices/presentationGenUpload";
import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import { selectableTemplates } from "@/app/presentation-templates";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { CustomTemplates, useCustomTemplateSummaries } from "@/app/hooks/useCustomTemplates";

import CreateCustomTemplate from "../../(dashboard)/templates/components/CreateCustomTemplate";
import { CustomTemplateCard } from "./CustomTemplateCard";
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

const AuthoredModeCard = memo(function AuthoredModeCard({
  isSelected,
  onSelect,
  visionQa,
  onToggleVisionQa,
}: {
  isSelected: boolean;
  onSelect: (id: string) => void;
  visionQa: boolean;
  onToggleVisionQa: (next: boolean) => void;
}) {
  return (
    <Card
      className={cn(
        "cursor-pointer relative hover:shadow-sm transition-all duration-200 overflow-hidden rounded-[22px] border",
        isSelected
          ? " border-blue-500 ring-2 ring-blue-500/25 shadow-sm"
          : " border-[#E8E9EC]"
      )}
      onClick={() => onSelect(AUTHORED_TEMPLATE_ID)}
    >
      <div className="flex h-[150px] items-center justify-center bg-gradient-to-br from-[#1e3a8a] via-[#2563EB] to-[#3b82f6] text-white">
        <div className="text-center px-4">
          <div className="text-[11px] tracking-[0.15em] font-semibold opacity-90">
            AI AUTHORED
          </div>
          <div className="text-2xl font-extrabold font-syne mt-1">고품질 AI 저작</div>
        </div>
      </div>
      <div className="px-6 py-5 bg-white border-t border-[#EDEEEF]">
        <h3 className="text-sm font-bold text-gray-900 font-syne">AI 저작 (고품질)</h3>
        <p className="text-xs text-gray-600 line-clamp-2 font-syne">
          모델이 슬라이드별 디자인을 직접 저작 · 이미지 PPTX로 내보내기 · 인앱은 보기 전용(편집은 PowerPoint)
        </p>
        {isSelected && (
          <label
            className="mt-3 flex items-center gap-2 text-xs text-gray-700 cursor-pointer select-none"
            onClick={(e) => e.stopPropagation()}
          >
            <input
              type="checkbox"
              checked={visionQa}
              onChange={(e) => onToggleVisionQa(e.target.checked)}
              className="h-3.5 w-3.5 accent-blue-600"
            />
            고품질 검수 (vision-QA · 더 느림)
          </label>
        )}
      </div>
    </Card>
  );
});

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
  const dispatch = useDispatch();
  const authoredVisionQa = useSelector(
    (s: RootState) => s.pptGenUpload.authoredVisionQa
  );

  // Stable identity so memo(AuthoredModeCard) isn't defeated on every parent render.
  const handleToggleVisionQa = useCallback(
    (next: boolean) => dispatch(setPptGenUploadState({ authoredVisionQa: next })),
    [dispatch]
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
    () => (typeof selectedTemplate === "string" ? selectedTemplate : null),
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

  return (
    <div className="mb-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {/* Authored (high-quality) mode first, then create-custom, custom, built-ins */}
        <AuthoredModeCard
          isSelected={selectedTemplate === AUTHORED_TEMPLATE_ID}
          onSelect={handleCustomSelect}
          visionQa={authoredVisionQa}
          onToggleVisionQa={handleToggleVisionQa}
        />
        <CreateCustomTemplate />
        {customTemplateCards}
        {builtInTemplateCards}
      </div>
    </div>
  );
});

export default TemplateSelection;
