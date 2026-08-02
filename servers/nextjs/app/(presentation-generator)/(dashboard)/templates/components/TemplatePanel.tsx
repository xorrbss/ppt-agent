"use client";

import React, { useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";

import {
  parseTemplateV2SelectionId,
  useStructuredTemplateSummaries,
} from "@/app/hooks/useStructuredTemplates";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { StructuredTemplateCard } from "@/app/(presentation-generator)/outline/components/StructuredTemplateCard";

const LayoutPreview = () => {
  const router = useRouter();
  const { templates, loading, error } = useStructuredTemplateSummaries();

  useEffect(() => {
    trackEvent(MixpanelEvent.Templates_Page_Viewed);
  }, []);

  const handleOpenPreview = useCallback(
    (selectionId: string) => {
      const selection = parseTemplateV2SelectionId(selectionId);
      if (!selection) return;
      trackEvent(MixpanelEvent.Templates_Inbuilt_Opened, {
        template_id: selection.templateId,
        template_generation: "v2",
      });
      router.push(`/template-v2-studio/${encodeURIComponent(selection.templateId)}`);
    },
    [router]
  );

  const defaultTemplates = useMemo(
    () => templates.filter((template) => template.is_default),
    [templates]
  );
  const catalogConfigurationError =
    templates.length > 0 && defaultTemplates.length !== 1
      ? "Template V2 카탈로그에는 정확히 하나의 기본 템플릿이 필요합니다."
      : null;
  const templateCards = useMemo(
    () =>
      templates.map((template) => (
        <StructuredTemplateCard
          key={template.id}
          template={template}
          onSelectTemplate={handleOpenPreview}
          selectedTemplate={null}
        />
      )),
    [handleOpenPreview, templates]
  );

  return (
    <div className="relative min-h-screen font-syne">
      <div className="sticky right-0 top-0 z-50 px-6 py-7 backdrop-blur">
        <div className="flex flex-col items-center justify-between gap-3 xl:flex-row">
          <div>
            <h3 className="font-unbounded text-[28px] font-normal tracking-[-0.84px] text-[#101828]">
              Templates
            </h3>
            <p className="mt-2 text-sm text-slate-600">
              Template V2 카탈로그의 편집 가능 PPTX 템플릿입니다.
            </p>
          </div>
        </div>
      </div>

      <div className="mx-auto px-6 py-8">
        <section className="my-12">
          {loading ? (
            <div data-testid="template-v2-dashboard-loading" role="status">
              Template V2 카탈로그를 불러오는 중입니다.
            </div>
          ) : error ? (
            <div data-testid="template-v2-dashboard-empty" role="alert">
              Template V2 카탈로그를 불러오지 못했습니다. 이전 템플릿으로 대체하지 않습니다.
            </div>
          ) : templates.length === 0 ? (
            <div data-testid="template-v2-dashboard-empty" role="status">
              현재 사용할 수 있는 Template V2 템플릿이 없습니다.
            </div>
          ) : catalogConfigurationError ? (
            <div data-testid="template-v2-dashboard-configuration-error" role="alert">
              {catalogConfigurationError}
            </div>
          ) : (
            <div
              className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
              data-testid="template-v2-dashboard-grid"
            >
              {templateCards}
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default LayoutPreview;
