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
import TemplateService from "../../services/api/template";
import Header from "../../(dashboard)/dashboard/components/Header";
import { LayoutsPanel } from "./editor/LayoutsPanel";
import { readLayoutId, updateLayoutMetadata } from "@/lib/template-v2-preview";
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
      if (!canEdit) return;
      dispatch({
        type: "UPDATE_LAYOUTS",
        payload: state.layouts.map((layout, index) =>
          index === layoutIndex
            ? updateLayoutMetadata(layout, field, value)
            : layout,
        ),
      });
    },
    [canEdit, state.layouts, dispatch],
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

  // Save callback
  const save = useCallback(async () => {
    if (
      !state.loadedTemplate ||
      !canEdit ||
      !state.dirty ||
      state.saving
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
          onSelectLayout={(index) => dispatch({ type: "SET_ACTIVE_LAYOUT", payload: index })}
        />

        <main className="min-w-0 flex-1 overflow-hidden p-6">
          <EditorHeader
            templateName={state.loadedTemplate.details.name}
            revision={state.loadedTemplate.details.revision}
            isDirty={state.dirty}
            canEdit={canEdit}
            isSaving={state.saving}
            onClickSave={save}
          />

          {!canEdit && <EditReadOnlyWarning />}

          <TemplatePreviewSection
            activeLayout={activeLayout}
            activeLayoutIndex={state.activeLayoutIndex}
          />
        </main>
      </div>
    </div>
  );
}

// Memoized components to prevent unnecessary re-renders
const LayoutsPanelMemoized = React.memo(LayoutsPanel);
