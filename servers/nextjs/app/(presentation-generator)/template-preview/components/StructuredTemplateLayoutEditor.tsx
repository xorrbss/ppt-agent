"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Loader2, Save, Sparkles } from "lucide-react";

import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
  type TemplateV2LayoutsCompatibilityDocument,
} from "@/lib/template-v2-upstream-compat";
import type { JsonRecord } from "@/lib/template-v2-studio";
import { normalizeBackendAssetUrls } from "@/utils/api";
import { TemplateV2PromptOverlay } from "../../_shared/TemplateV2PromptOverlay";
import { TemplateV2HtmlSlidePreview } from "../../components/TemplateV2HtmlSlidePreview";
import TemplateService, {
  type StructuredTemplateDetailsResponse,
} from "../../services/api/template";
import Header from "../../(dashboard)/dashboard/components/Header";
import { LayoutsPanel } from "./editor/LayoutsPanel";
import {
  cloneLayout,
  readLayoutId,
  updateLayoutMetadata,
} from "./editor/templatePreviewUtils";

type StructuredTemplateLayoutEditorProps = {
  templateId: string;
};

type LoadedTemplate = {
  details: StructuredTemplateDetailsResponse;
  document: TemplateV2LayoutsCompatibilityDocument;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function layoutsFromDocument(
  document: TemplateV2LayoutsCompatibilityDocument,
): TemplateV2Layout[] {
  const layouts = document.studioLayouts.layouts;
  if (!Array.isArray(layouts)) {
    throw new Error("Structured template layouts are invalid.");
  }
  return layouts.map((layout) => cloneLayout(layout as TemplateV2Layout));
}

function createPreviewSlide(layout: TemplateV2Layout, index: number) {
  return {
    id: readLayoutId(layout, index),
    content: {},
    ui: layout,
    layout: readLayoutId(layout, index),
    layout_group: "template-v2",
  };
}

function nextGeneratedLayoutId(layouts: TemplateV2Layout[]) {
  const ids = new Set(layouts.map((layout, index) => readLayoutId(layout, index)));
  let suffix = layouts.length + 1;
  let candidate = `ai_layout_${suffix}`;
  while (ids.has(candidate)) {
    suffix += 1;
    candidate = `ai_layout_${suffix}`;
  }
  return candidate;
}

export function StructuredTemplateLayoutEditor({
  templateId,
}: StructuredTemplateLayoutEditorProps) {
  const router = useRouter();
  const [loadedTemplate, setLoadedTemplate] = useState<LoadedTemplate | null>(
    null,
  );
  const [layouts, setLayouts] = useState<TemplateV2Layout[]>([]);
  const [activeLayoutIndex, setActiveLayoutIndex] = useState(0);
  const [promptLayoutIndex, setPromptLayoutIndex] = useState<number | null>(
    null,
  );
  const [returnLayoutIndex, setReturnLayoutIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    TemplateService.getStructuredTemplateDetails(templateId)
      .then((details) => {
        if (cancelled) return;
        const document = adaptUpstreamTemplateV2LayoutsToStudio(details.layouts);
        const nextLayouts = layoutsFromDocument(document);
        setLoadedTemplate({ details, document });
        setLayouts(nextLayouts);
        setActiveLayoutIndex(0);
        setPromptLayoutIndex(null);
        setDirty(false);
      })
      .catch((requestError: unknown) => {
        if (cancelled) return;
        setError(
          requestError instanceof Error
            ? requestError.message
            : "구조화 템플릿을 불러오지 못했습니다.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [templateId]);

  const canEdit = Boolean(
    loadedTemplate && loadedTemplate.details.is_default !== true,
  );
  const activeLayout = layouts[activeLayoutIndex] ?? null;
  const previewSlide = useMemo(
    () =>
      activeLayout
        ? createPreviewSlide(activeLayout, activeLayoutIndex)
        : null,
    [activeLayout, activeLayoutIndex],
  );

  const updateLayout = useCallback(
    (
      layoutIndex: number,
      field: "id" | "description",
      value: string,
    ) => {
      if (!canEdit || promptLayoutIndex !== null) return;
      setLayouts((current) =>
        current.map((layout, index) =>
          index === layoutIndex
            ? updateLayoutMetadata(layout, field, value)
            : layout,
        ),
      );
      setDirty(true);
    },
    [canEdit, promptLayoutIndex],
  );

  const copyLayoutId = useCallback(
    async (layoutIndex: number) => {
      const layout = layouts[layoutIndex];
      if (!layout) return;
      try {
        await navigator.clipboard.writeText(readLayoutId(layout, layoutIndex));
        notify.success("레이아웃 ID를 복사했습니다.");
      } catch {
        notify.error("레이아웃 ID를 복사하지 못했습니다.");
      }
    },
    [layouts],
  );

  const startPrompt = useCallback(() => {
    if (!canEdit || promptLayoutIndex !== null) return;
    const draftIndex = layouts.length;
    const draftId = nextGeneratedLayoutId(layouts);
    const draftLayout: TemplateV2Layout = {
      id: draftId,
      description: "AI로 생성할 새 레이아웃",
      components: [],
    };
    setReturnLayoutIndex(activeLayoutIndex);
    setLayouts((current) => [...current, draftLayout]);
    setActiveLayoutIndex(draftIndex);
    setPromptLayoutIndex(draftIndex);
  }, [activeLayoutIndex, canEdit, layouts, promptLayoutIndex]);

  const dismissPrompt = useCallback(() => {
    if (generating || promptLayoutIndex === null) return;
    setLayouts((current) =>
      current.filter((_, index) => index !== promptLayoutIndex),
    );
    setActiveLayoutIndex(Math.max(0, returnLayoutIndex));
    setPromptLayoutIndex(null);
  }, [generating, promptLayoutIndex, returnLayoutIndex]);

  const generatePromptedLayout = useCallback(
    async (prompt: string) => {
      if (
        !canEdit ||
        promptLayoutIndex === null ||
        generating ||
        !loadedTemplate
      ) {
        return false;
      }

      setGenerating(true);
      try {
        const response = await TemplateService.generateTemplateLayout({
          template_id: templateId,
          prompt,
        });
        if (!isRecord(response.layout)) {
          throw new Error("생성된 레이아웃 응답이 올바르지 않습니다.");
        }

        const generatedLayout = normalizeBackendAssetUrls(
          response.layout,
        ) as TemplateV2Layout;
        setLayouts((current) => {
          const generatedId = readLayoutId(generatedLayout, promptLayoutIndex);
          const idIsUsed = current.some(
            (layout, index) =>
              index !== promptLayoutIndex &&
              readLayoutId(layout, index) === generatedId,
          );
          const draftId = readLayoutId(
            current[promptLayoutIndex],
            promptLayoutIndex,
          );
          return current.map((layout, index) =>
            index === promptLayoutIndex
              ? {
                  ...generatedLayout,
                  id: idIsUsed ? draftId : generatedId,
                }
              : layout,
          );
        });
        setPromptLayoutIndex(null);
        setDirty(true);
        notify.success(
          "AI 레이아웃을 생성했습니다.",
          "미리보기를 확인한 뒤 저장해 주세요.",
        );
        // The state update above unmounts the overlay. Returning true would
        // make it call onDismiss too, which is the cancel path that removes
        // the draft layout.
        return false;
      } catch (generationError) {
        notify.error(
          "AI 레이아웃을 생성하지 못했습니다.",
          generationError instanceof Error
            ? generationError.message
            : "잠시 후 다시 시도해 주세요.",
        );
        return false;
      } finally {
        setGenerating(false);
      }
    },
    [
      canEdit,
      generating,
      loadedTemplate,
      promptLayoutIndex,
      templateId,
    ],
  );

  const save = useCallback(async () => {
    if (
      !loadedTemplate ||
      !canEdit ||
      !dirty ||
      saving ||
      promptLayoutIndex !== null
    ) {
      return;
    }

    setSaving(true);
    try {
      const serializedLayouts = serializeStudioLayoutsForUpstream(
        loadedTemplate.document,
        {
          ...loadedTemplate.document.studioLayouts,
          layouts: layouts as JsonRecord[],
        },
      );
      const details = await TemplateService.updateStructuredTemplateLayouts(
        templateId,
        {
          layouts: serializedLayouts,
          expected_revision: loadedTemplate.details.revision,
        },
      );
      const document = adaptUpstreamTemplateV2LayoutsToStudio(details.layouts);
      const savedLayouts = layoutsFromDocument(document);
      setLoadedTemplate({ details, document });
      setLayouts(savedLayouts);
      setActiveLayoutIndex((index) =>
        Math.min(index, Math.max(0, savedLayouts.length - 1)),
      );
      setDirty(false);
      notify.success("템플릿 레이아웃을 저장했습니다.");
    } catch (saveError) {
      notify.error(
        "템플릿 레이아웃을 저장하지 못했습니다.",
        saveError instanceof Error
          ? saveError.message
          : "잠시 후 다시 시도해 주세요.",
      );
    } finally {
      setSaving(false);
    }
  }, [
    canEdit,
    dirty,
    layouts,
    loadedTemplate,
    promptLayoutIndex,
    saving,
    templateId,
  ]);

  if (loading) {
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

  if (error || !loadedTemplate) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <div className="flex flex-col items-center justify-center py-24">
          <h2 className="mb-4 text-2xl font-bold text-red-600">
            구조화 템플릿을 불러오지 못했습니다.
          </h2>
          <p className="mb-4 text-gray-600">{error}</p>
          <Button onClick={() => router.push("/templates")}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            템플릿으로 돌아가기
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#F7F8FA]">
      <Header />
      <div className="flex min-h-[calc(100vh-72px)]">
        <LayoutsPanel
          activeLayoutIndex={activeLayoutIndex}
          layouts={layouts}
          onCopyLayoutId={copyLayoutId}
          onLayoutDescriptionChange={(index, value) =>
            updateLayout(index, "description", value)
          }
          onLayoutIdChange={(index, value) => updateLayout(index, "id", value)}
          onSelectLayout={(index) => {
            if (promptLayoutIndex !== null && index !== promptLayoutIndex) return;
            setActiveLayoutIndex(index);
          }}
        />

        <main className="min-w-0 flex-1 overflow-hidden p-6">
          <div className="mx-auto flex max-w-[1380px] items-center justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold text-gray-900">
                {loadedTemplate.details.name}
              </h1>
              <p className="mt-1 text-sm text-gray-500">
                Revision {loadedTemplate.details.revision}
                {dirty ? " · 저장하지 않은 변경 사항" : ""}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                disabled={!canEdit || promptLayoutIndex !== null}
                onClick={startPrompt}
                variant="outline"
              >
                <Sparkles className="mr-2 h-4 w-4" />
                AI 레이아웃 생성
              </Button>
              <Button
                disabled={
                  !canEdit ||
                  !dirty ||
                  saving ||
                  promptLayoutIndex !== null
                }
                onClick={save}
              >
                {saving ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                저장
              </Button>
            </div>
          </div>

          {!canEdit ? (
            <div className="mx-auto mt-4 max-w-[1380px] rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              기본 템플릿은 미리보기만 제공됩니다. 복제한 템플릿에서 편집해 주세요.
            </div>
          ) : null}

          <div className="mt-6 overflow-auto rounded-2xl border border-gray-200 bg-gray-100 p-5 shadow-sm">
            <div className="relative mx-auto h-[720px] w-[1280px] overflow-hidden rounded-lg bg-white shadow">
              {previewSlide ? (
                <TemplateV2HtmlSlidePreview slide={previewSlide} fixedSize />
              ) : (
                <div className="flex h-full items-center justify-center text-gray-500">
                  템플릿에 레이아웃이 없습니다. AI 레이아웃을 생성해 주세요.
                </div>
              )}
              {promptLayoutIndex === activeLayoutIndex && activeLayout ? (
                <TemplateV2PromptOverlay
                  isSubmitting={generating}
                  layout={activeLayout}
                  onDismiss={dismissPrompt}
                  onSubmitPrompt={generatePromptedLayout}
                  showLayoutPreview
                  slideIndex={activeLayoutIndex}
                />
              ) : null}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}
