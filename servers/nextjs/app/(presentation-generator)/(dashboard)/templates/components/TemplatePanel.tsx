"use client";

import React, { useCallback, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";

import {
  parseTemplateV2SelectionId,
  useStructuredTemplateSummaries,
} from "@/app/hooks/useStructuredTemplates";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { StructuredTemplateCard } from "@/app/(presentation-generator)/outline/components/StructuredTemplateCard";
import { useTranslations } from "next-intl";

const LayoutPreview = () => {
  const router = useRouter();
  const t = useTranslations("templates");
  const { templates, loading, error, refetch } = useStructuredTemplateSummaries();

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
      ? t("configurationError")
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
    <div className="relative min-h-screen bg-[#FAFAFA] font-syne">
      <div className="border-b border-zinc-200/70 bg-white px-4 py-6 sm:px-8 sm:py-8">
        <div className="flex flex-col items-start justify-between gap-4 xl:flex-row xl:items-center">
          <div>
            <h3 className="text-2xl font-semibold tracking-tight text-zinc-900 sm:text-3xl">
              {t("title")}
            </h3>
            <p className="mt-2 text-sm text-slate-600">
              {t("description")}
            </p>
          </div>
        </div>
      </div>

      <div className="mx-auto px-4 py-6 sm:px-8 sm:py-8">
        <section className="mt-7">
          {loading ? (
            <div data-testid="template-v2-dashboard-loading" role="status">
              {t("loading")}
            </div>
          ) : error ? (
            <div className="rounded-2xl border border-red-200 bg-red-50 p-8 text-center" data-testid="template-v2-dashboard-empty" role="alert">
              <p>{t("loadError")}</p>
              <button
                type="button"
                onClick={() => void refetch()}
                className="mt-4 rounded-xl bg-zinc-900 px-5 py-3 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 focus-visible:ring-offset-2"
              >
                {t("retry")}
              </button>
            </div>
          ) : templates.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-zinc-300 bg-white px-6 py-16 text-center" data-testid="template-v2-dashboard-empty" role="status">
              <h4 className="text-lg font-semibold text-[#101828]">{t("emptyTitle")}</h4>
              <p className="mt-2 text-sm text-[#667085]">{t("emptyDescription")}</p>
            </div>
          ) : catalogConfigurationError ? (
            <div data-testid="template-v2-dashboard-configuration-error" role="alert">
              {catalogConfigurationError}
            </div>
          ) : (
            <div
              className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
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
