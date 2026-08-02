"use client";

import React, { memo, useCallback, useEffect, useMemo } from "react";

import { TemplateLayoutsWithSettings } from "@/app/presentation-templates/utils";
import {
  makeTemplateV2SelectionId,
  TEMPLATE_V2_SELECTION_PREFIX,
  useStructuredTemplateSummaries,
} from "@/app/hooks/useStructuredTemplates";

import AuthoredStylePicker from "./AuthoredStylePicker";
import {
  LOCAL_PPT_MASTER_TEMPLATES,
  LocalPptMasterPreviewCard,
} from "./LocalPptMasterPreviewCard";
import { StructuredTemplateCard } from "./StructuredTemplateCard";

// Sentinel id for the authored (high-quality) mode; this is not a layout template.
export const AUTHORED_TEMPLATE_ID = "authored";

interface TemplateSelectionProps {
  selectedTemplate: (TemplateLayoutsWithSettings | string) | null;
  onSelectTemplate: (template: TemplateLayoutsWithSettings | string) => void;
}

const TemplateSelection: React.FC<TemplateSelectionProps> = memo(function TemplateSelection({
  selectedTemplate,
  onSelectTemplate,
}) {
  const {
    templates: structuredTemplates,
    loading: structuredLoading,
    error: structuredError,
  } = useStructuredTemplateSummaries();

  const handleAuthoredActivate = useCallback(
    () => onSelectTemplate(AUTHORED_TEMPLATE_ID),
    [onSelectTemplate]
  );
  const handleStructuredSelect = useCallback(
    (template: string) => onSelectTemplate(template),
    [onSelectTemplate]
  );

  const selectedStructuredId = useMemo(
    () =>
      typeof selectedTemplate === "string" &&
      selectedTemplate.startsWith(TEMPLATE_V2_SELECTION_PREFIX)
        ? selectedTemplate
        : null,
    [selectedTemplate]
  );
  const defaultTemplates = useMemo(
    () => structuredTemplates.filter((template) => template.is_default),
    [structuredTemplates]
  );
  const defaultTemplate = defaultTemplates.length === 1 ? defaultTemplates[0] : null;
  const catalogConfigurationError = useMemo(() => {
    if (structuredLoading || structuredError || structuredTemplates.length === 0) {
      return null;
    }
    if (defaultTemplates.length === 0) {
      return "Template V2 카탈로그에 기본 템플릿이 설정되어 있지 않습니다.";
    }
    if (defaultTemplates.length > 1) {
      return "Template V2 카탈로그에 기본 템플릿이 둘 이상 설정되어 있습니다.";
    }
    return null;
  }, [defaultTemplates.length, structuredError, structuredLoading, structuredTemplates.length]);

  // Fresh flows begin from the single catalog-declared default. Do not overwrite
  // a rehydrated legacy id: existing V1 documents must remain renderable.
  useEffect(() => {
    if (!defaultTemplate || selectedTemplate !== null) return;
    onSelectTemplate(
      makeTemplateV2SelectionId(defaultTemplate.id, defaultTemplate.revision)
    );
  }, [defaultTemplate, onSelectTemplate, selectedTemplate]);

  const structuredTemplateCards = useMemo(
    () =>
      structuredTemplates.map((template) => (
        <StructuredTemplateCard
          key={template.id}
          template={template}
          onSelectTemplate={handleStructuredSelect}
          selectedTemplate={selectedStructuredId}
        />
      )),
    [handleStructuredSelect, selectedStructuredId, structuredTemplates]
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
            className="font-syne text-base font-bold text-gray-900"
          >
            AI 저작 템플릿 (고품질)
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-gray-600">
            AI가 슬라이드별 시각 구성을 직접 저작하는 별도 모드입니다.
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
            className="font-syne text-base font-bold text-gray-900"
          >
            레이아웃 템플릿 (편집 가능 PPTX)
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-gray-600">
            Template V2 카탈로그의 검증된 편집 가능 PPTX 템플릿입니다.
          </p>
        </div>

        {structuredLoading ? (
          <div data-testid="template-v2-catalog-loading" role="status">
            Template V2 카탈로그를 불러오는 중입니다.
          </div>
        ) : structuredError ? (
          <div data-testid="template-v2-catalog-empty" role="alert">
            Template V2 카탈로그를 불러오지 못했습니다. 레이아웃 템플릿을 선택할 수 없습니다.
          </div>
        ) : structuredTemplates.length === 0 ? (
          <div data-testid="template-v2-catalog-empty" role="status">
            현재 사용할 수 있는 Template V2 템플릿이 없습니다.
          </div>
        ) : catalogConfigurationError ? (
          <div data-testid="template-v2-catalog-configuration-error" role="alert">
            {catalogConfigurationError} 관리자에게 카탈로그 구성을 확인해 달라고 요청하세요.
          </div>
        ) : (
          <div
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            data-testid="layout-template-grid"
          >
            {structuredTemplateCards}
          </div>
        )}

        {process.env.NODE_ENV === "development" ? (
          <aside className="mt-6" data-testid="local-ppt-master-catalog">
            <div className="mb-3">
              <h3 className="font-syne text-sm font-bold text-gray-900">
                PPT Master 추가 템플릿
              </h3>
              <p className="mt-1 text-xs leading-5 text-gray-600">
                15개 핵심 레이아웃을 목적별 샘플 데이터로 채웠습니다. 필요한 장수만큼
                슬라이드를 복제하거나 삭제해 사용할 수 있습니다.
              </p>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {LOCAL_PPT_MASTER_TEMPLATES.map((template) => (
                <LocalPptMasterPreviewCard
                  key={template.filename}
                  template={template}
                />
              ))}
            </div>
          </aside>
        ) : null}
      </section>
    </div>
  );
});

export default TemplateSelection;
