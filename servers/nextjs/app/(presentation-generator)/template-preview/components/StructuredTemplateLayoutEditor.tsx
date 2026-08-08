"use client";

import React, { useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";

import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { notify } from "@/components/ui/sonner";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "@/lib/template-v2-upstream-compat";
import type { JsonRecord } from "@/lib/template-v2-studio";
import { normalizeBackendAssetUrls } from "@/utils/api";
import TemplateService from "../../services/api/template";
import Header from "../../(dashboard)/dashboard/components/Header";
import { LayoutsPanel } from "./editor/LayoutsPanel";
import {
  isRecord,
  readLayoutId,
  updateLayoutMetadata,
} from "@/lib/template-v2-preview";
import {
  EditReadOnlyWarning,
  EditorHeader,
  ErrorState,
  LoadingState,
} from "./editor/EditorChrome";
import { TemplatePreviewSection } from "./editor/TemplatePreviewSection";
import {
  layoutsFromDocument,
  useTemplateEditorReducer,
} from "./editor/useTemplateEditorReducer";

type StructuredTemplateLayoutEditorProps = {
  templateId: string;
};

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
  const [state, dispatch] = useTemplateEditorReducer();

  // Load template on mount or when templateId changes
  useEffect(() => {
    let cancelled = false;
    dispatch({ type: "LOAD_START" });

    TemplateService.getStructuredTemplateDetails(templateId)
      .then((details) => {
        if (cancelled) return;
        const document = adaptUpstreamTemplateV2LayoutsToStudio(details.layouts);
        dispatch({
          type: "LOAD_SUCCESS",
          payload: { details, document },
        });
      })
      .catch((requestError: unknown) => {
        if (cancelled) return;
        dispatch({
          type: "LOAD_ERROR",
          payload:
            requestError instanceof Error
              ? requestError.message
              : "구조화 템플릿을 불러오지 못했습니다.",
        });
      });

    return () => {
      cancelled = true;
    };
  }, [templateId, dispatch]);

  // ========================================================================
  // Derived state
  // ========================================================================

  const canEdit = Boolean(
    state.loadedTemplate && state.loadedTemplate.details.is_default !== true,
  );
  const activeLayout = state.layouts[state.activeLayoutIndex] ?? null;

  // ========================================================================
  // Callbacks - grouped by domain
  // ========================================================================

  // Layout editing callbacks
  const updateLayout = useCallback(
    (
      layoutIndex: number,
      field: "id" | "description",
      value: string,
    ) => {
      if (!canEdit || state.promptLayoutIndex !== null) return;
      dispatch({
        type: "UPDATE_LAYOUTS",
        payload: state.layouts.map((layout, index) =>
          index === layoutIndex
            ? updateLayoutMetadata(layout, field, value)
            : layout,
        ),
      });
    },
    [canEdit, state.promptLayoutIndex, state.layouts, dispatch],
  );

  const copyLayoutId = useCallback(
    async (layoutIndex: number) => {
      const layout = state.layouts[layoutIndex];
      if (!layout) return;
      try {
        await navigator.clipboard.writeText(readLayoutId(layout, layoutIndex));
        notify.success("레이아웃 ID를 복사했습니다.");
      } catch {
        notify.error("레이아웃 ID를 복사하지 못했습니다.");
      }
    },
    [state.layouts],
  );

  // Prompt/generation callbacks
  const startPrompt = useCallback(() => {
    if (!canEdit || state.promptLayoutIndex !== null) return;
    const draftIndex = state.layouts.length;
    const draftId = nextGeneratedLayoutId(state.layouts);

    dispatch({
      type: "START_PROMPT",
      payload: {
        draftIndex,
        draftId,
        currentActiveIndex: state.activeLayoutIndex,
      },
    });
  }, [canEdit, state.promptLayoutIndex, state.layouts, state.activeLayoutIndex, dispatch]);

  const dismissPrompt = useCallback(() => {
    if (state.generating || state.promptLayoutIndex === null) return;
    dispatch({ type: "DISMISS_PROMPT", payload: state.returnLayoutIndex });
  }, [state.generating, state.promptLayoutIndex, state.returnLayoutIndex, dispatch]);

  const generatePromptedLayout = useCallback(
    async (prompt: string) => {
      if (
        !canEdit ||
        state.promptLayoutIndex === null ||
        state.generating ||
        !state.loadedTemplate
      ) {
        return false;
      }

      dispatch({ type: "SET_GENERATING", payload: true });
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
        const updatedLayouts = state.layouts.map((layout, index) => {
          if (index !== state.promptLayoutIndex) return layout;

          const generatedId = readLayoutId(generatedLayout, state.promptLayoutIndex);
          const idIsUsed = state.layouts.some(
            (l, i) =>
              i !== state.promptLayoutIndex &&
              readLayoutId(l, i) === generatedId,
          );
          const draftId = readLayoutId(layout, state.promptLayoutIndex);
          return {
            ...generatedLayout,
            id: idIsUsed ? draftId : generatedId,
          };
        });

        dispatch({ type: "UPDATE_LAYOUTS", payload: updatedLayouts });
        dispatch({ type: "SET_GENERATING", payload: false });
        dispatch({ type: "COMPLETE_PROMPT" });
        notify.success(
          "AI 레이아웃을 생성했습니다.",
          "미리보기를 확인한 뒤 저장해 주세요.",
        );
        return false;
      } catch (generationError) {
        dispatch({ type: "SET_GENERATING", payload: false });
        notify.error(
          "AI 레이아웃을 생성하지 못했습니다.",
          generationError instanceof Error
            ? generationError.message
            : "잠시 후 다시 시도해 주세요.",
        );
        return false;
      }
    },
    [
      canEdit,
      state.generating,
      state.loadedTemplate,
      state.promptLayoutIndex,
      state.layouts,
      templateId,
      dispatch,
    ],
  );

  // Save callback
  const save = useCallback(async () => {
    if (
      !state.loadedTemplate ||
      !canEdit ||
      !state.dirty ||
      state.saving ||
      state.promptLayoutIndex !== null
    ) {
      return;
    }

    dispatch({ type: "SET_SAVING", payload: true });
    try {
      const serializedLayouts = serializeStudioLayoutsForUpstream(
        state.loadedTemplate.document,
        {
          ...state.loadedTemplate.document.studioLayouts,
          layouts: state.layouts as JsonRecord[],
        },
      );
      const details = await TemplateService.updateStructuredTemplateLayouts(
        templateId,
        {
          layouts: serializedLayouts,
          expected_revision: state.loadedTemplate.details.revision,
        },
      );
      const document = adaptUpstreamTemplateV2LayoutsToStudio(details.layouts);
      const savedLayouts = layoutsFromDocument(document);
      dispatch({
        type: "SAVE_SUCCESS",
        payload: {
          details,
          document,
          layouts: savedLayouts,
        },
      });
      notify.success("템플릿 레이아웃을 저장했습니다.");
    } catch (saveError) {
      dispatch({ type: "SET_SAVING", payload: false });
      notify.error(
        "템플릿 레이아웃을 저장하지 못했습니다.",
        saveError instanceof Error
          ? saveError.message
          : "잠시 후 다시 시도해 주세요.",
      );
    }
  }, [
    canEdit,
    state.dirty,
    state.layouts,
    state.loadedTemplate,
    state.promptLayoutIndex,
    state.saving,
    templateId,
    dispatch,
  ]);

  // ========================================================================
  // Render
  // ========================================================================

  if (state.loading) {
    return <LoadingState />;
  }

  if (state.error || !state.loadedTemplate) {
    return (
      <ErrorState
        error={state.error}
        onNavigateBack={() => router.push("/templates")}
      />
    );
  }

  return (
    <div className="min-h-screen bg-[#F7F8FA]">
      <Header />
      <div className="flex min-h-[calc(100vh-72px)]">
        <LayoutsPanelMemoized
          activeLayoutIndex={state.activeLayoutIndex}
          layouts={state.layouts}
          onCopyLayoutId={copyLayoutId}
          onLayoutDescriptionChange={(index, value) =>
            updateLayout(index, "description", value)
          }
          onLayoutIdChange={(index, value) => updateLayout(index, "id", value)}
          onSelectLayout={(index) => {
            if (state.promptLayoutIndex !== null && index !== state.promptLayoutIndex) return;
            dispatch({ type: "SET_ACTIVE_LAYOUT", payload: index });
          }}
        />

        <main className="min-w-0 flex-1 overflow-hidden p-6">
          <EditorHeader
            templateName={state.loadedTemplate.details.name}
            revision={state.loadedTemplate.details.revision}
            isDirty={state.dirty}
            canEdit={canEdit}
            isPrompting={state.promptLayoutIndex !== null}
            isSaving={state.saving}
            onClickGenerateAI={startPrompt}
            onClickSave={save}
          />

          {!canEdit && <EditReadOnlyWarning />}

          <TemplatePreviewSection
            activeLayout={activeLayout}
            activeLayoutIndex={state.activeLayoutIndex}
            isPrompting={state.promptLayoutIndex === state.activeLayoutIndex}
            isGenerating={state.generating}
            onDismissPrompt={dismissPrompt}
            onSubmitPrompt={generatePromptedLayout}
          />
        </main>
      </div>
    </div>
  );
}

// Memoized components to prevent unnecessary re-renders
const LayoutsPanelMemoized = React.memo(LayoutsPanel);
