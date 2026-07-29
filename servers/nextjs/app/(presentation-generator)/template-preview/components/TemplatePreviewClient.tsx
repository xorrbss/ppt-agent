"use client";

import React, { useEffect } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Loader2, Trash2 } from "lucide-react";

import { CustomTemplateLayout, useCustomTemplateDetails } from "@/app/hooks/useCustomTemplates";
import {
  getTemplatesByTemplateName,
  templates as templateGroups,
} from "@/app/presentation-templates";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { notify } from "@/components/ui/sonner";
import { setupImageUrlConverter } from "@/utils/image-url-converter";
import { MixpanelEvent, trackEvent } from "@/utils/mixpanel";
import "../../utils/prism-languages";
import Header from "../../(dashboard)/dashboard/components/Header";
import TemplateService from "../../services/api/template";
import { StructuredTemplateLayoutEditor } from "./StructuredTemplateLayoutEditor";

function LegacyGroupLayoutPreview() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const templateParams = searchParams.get("slug") || "";
  const isCustom = templateParams.startsWith("custom-");
  const customTemplateId = isCustom
    ? templateParams.slice("custom-".length)
    : null;
  const staticTemplates = !isCustom
    ? getTemplatesByTemplateName(templateParams)
    : [];
  const staticGroup = !isCustom
    ? templateGroups.find((group: { id: string }) => group.id === templateParams)
    : null;
  const {
    template: customTemplate,
    loading: customLoading,
    error: customError,
  } = useCustomTemplateDetails({
    id: customTemplateId || "",
    name: "",
    description: "",
  });

  // Keep backend-served assets on the active origin in Docker/nginx preview mode.
  useEffect(() => {
    const observer = setupImageUrlConverter();
    return () => observer?.disconnect();
  }, []);

  const handleDeleteCustomTemplate = async () => {
    if (!customTemplateId) return;
    const confirmed = window.confirm(
      "이 템플릿을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.",
    );
    if (!confirmed) return;

    const result = await TemplateService.deleteCustomTemplate(customTemplateId);
    if (result.success) {
      notify.success(
        "템플릿 삭제 완료",
        "템플릿이 성공적으로 삭제되었습니다.",
      );
      router.push("/templates");
      return;
    }
    notify.error(
      "템플릿을 삭제할 수 없습니다.",
      result.message || "템플릿을 삭제하는 중 문제가 발생했습니다.",
    );
  };

  if (isCustom && customLoading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <div className="flex items-center justify-center py-24">
          <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
          <span className="ml-3 text-gray-600">템플릿을 불러오는 중...</span>
        </div>
      </div>
    );
  }

  if (isCustom && customError) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <div className="flex flex-col items-center justify-center py-24">
          <h2 className="mb-4 text-2xl font-bold text-red-600">
            템플릿을 불러오는 중 오류가 발생했습니다.
          </h2>
          <p className="mb-4 text-gray-600">{customError}</p>
          <Button onClick={() => router.push("/templates")}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            템플릿으로 돌아가기
          </Button>
        </div>
      </div>
    );
  }

  if (
    (!isCustom && (!staticGroup || staticTemplates.length === 0)) ||
    (isCustom && !customTemplate)
  ) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <div className="flex flex-col items-center justify-center py-24">
          <h2 className="mb-4 text-2xl font-bold text-gray-900">
            템플릿을 찾을 수 없습니다.
          </h2>
          <Button onClick={() => router.push("/templates")}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            템플릿으로 돌아가기
          </Button>
        </div>
      </div>
    );
  }

  const templateName = isCustom
    ? customTemplate?.template.name || "사용자 템플릿"
    : staticGroup?.name || "";
  const templateDescription = isCustom
    ? customTemplate?.template.description || ""
    : staticGroup?.description || "";

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <header className="z-30">
        <div className="mx-auto px-6 pb-[30px]">
          <div className="mx-auto mb-4 flex max-w-[1440px] items-center justify-between">
            {isCustom ? (
              <div className="ml-auto mr-0 flex items-center justify-end gap-4">
                <Button
                  className="flex items-center gap-2 border-red-200 text-red-700 hover:bg-red-50"
                  onClick={() => {
                    trackEvent(
                      MixpanelEvent.TemplatePreview_Delete_Templates_Button_Clicked,
                      { pathname },
                    );
                    trackEvent(
                      MixpanelEvent.TemplatePreview_Delete_Templates_API_Call,
                    );
                    void handleDeleteCustomTemplate();
                  }}
                  size="sm"
                  variant="outline"
                >
                  <Trash2 className="h-4 w-4" />
                  템플릿 삭제
                </Button>
              </div>
            ) : null}
          </div>

          <div className="text-center">
            <div className="mb-2 flex items-center justify-center gap-2">
              <h1 className="text-[64px] font-bold text-gray-900">
                {templateName}
              </h1>
              {isCustom ? (
                <span className="rounded bg-purple-100 px-2 py-0.5 text-sm text-purple-700">
                  사용자 지정
                </span>
              ) : null}
            </div>
            <p className="text-xl text-gray-600">{templateDescription}</p>
          </div>
        </div>
      </header>

      <div className="mx-auto mb-4 h-full">
        {!isCustom ? (
          <div
            className="mx-auto w-[1305px] space-y-3 rounded-[20px] border border-[#EDECEC] bg-[#FFFFFF1A] p-2.5"
            style={{
              boxShadow: "0 0 20px 0 rgba(122, 90, 248, 0.16) inset",
            }}
          >
            {staticTemplates.map((template: any, index: number) => {
              const LayoutComponent = template.component;
              return (
                <div
                  className="overflow-hidden rounded-tl-[10px] rounded-tr-[10px] border border-[#EDEEEF]"
                  id={template.layoutId}
                  key={`${templateParams}-${template.layoutId}-${index}`}
                >
                  <div className="border-b border-[#EDEEEF] bg-white px-4 py-6">
                    <div className="flex items-center justify-between">
                      <div>
                        <span className="rounded-full bg-[#7A5AF8] px-3 py-1 font-syne text-sm font-medium text-white">
                          {index + 1 < 10 ? `0${index + 1}` : index + 1}
                        </span>
                        <h3 className="mt-3 text-xl font-semibold text-gray-900">
                          {template.layoutName}
                        </h3>
                        <p className="mt-1 text-sm text-gray-500">
                          {template.layoutDescription}
                        </p>
                      </div>
                    </div>
                  </div>
                  <div className="flex justify-center overflow-x-auto">
                    <div
                      className="flex-shrink-0"
                      style={{ width: "1280px", height: "720px" }}
                    >
                      <LayoutComponent data={template.sampleData} />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        ) : null}

        {isCustom ? (
          <div className="mx-auto flex aspect-video w-full flex-col items-center justify-center gap-10">
            {customTemplate?.layouts.map(
              (layout: CustomTemplateLayout, index: number) => {
                const LayoutComponent = layout.component;
                return (
                  <Card
                    className="overflow-hidden shadow-md"
                    id={layout.layoutId}
                    key={`${templateParams}-${layout.layoutId}-${index}`}
                  >
                    <div className="border-b bg-white px-6 py-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <h3 className="text-xl font-semibold text-gray-900">
                            {layout.rawLayoutName}
                          </h3>
                          <p className="mt-1 max-w-2xl text-sm text-gray-500">
                            {layout.layoutDescription}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-end justify-end">
                        <span className="rounded px-3 py-1 font-mono text-sm text-gray-600">
                          {templateParams}:{layout.layoutId}
                        </span>
                      </div>
                    </div>
                    <div className="flex justify-center overflow-x-auto p-6">
                      <div
                        className="flex-shrink-0"
                        style={{ width: "1280px", height: "720px" }}
                      >
                        <LayoutComponent data={layout.sampleData} />
                      </div>
                    </div>
                  </Card>
                );
              },
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function TemplatePreviewClient() {
  const searchParams = useSearchParams();
  const structuredTemplateId =
    searchParams.get("templateV2Id") || searchParams.get("id");

  return structuredTemplateId ? (
    <StructuredTemplateLayoutEditor templateId={structuredTemplateId} />
  ) : (
    <LegacyGroupLayoutPreview />
  );
}
