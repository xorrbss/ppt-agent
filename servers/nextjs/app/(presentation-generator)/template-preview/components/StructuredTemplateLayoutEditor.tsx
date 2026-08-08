"use client";

import React, { useCallback, useEffect, useMemo, useReducer } from "react";
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

// ============================================================================
// State Management
// ============================================================================

type TemplateEditorState = {
  loadedTemplate: LoadedTemplate | null;
  layouts: TemplateV2Layout[];
  activeLayoutIndex: number;
  promptLayoutIndex: number | null;
  returnLayoutIndex: number;
  loading: boolean;
  saving: boolean;
  generating: boolean;
  dirty: boolean;
  error: string | null;
};

type TemplateEditorAction =
  | { type: "LOAD_START" }
  | { type: "LOAD_SUCCESS"; payload: { details: StructuredTemplateDetailsResponse; document: TemplateV2LayoutsCompatibilityDocument } }
  | { type: "LOAD_ERROR"; payload: string }
  | { type: "UPDATE_LAYOUTS"; payload: TemplateV2Layout[] }
  | { type: "SET_ACTIVE_LAYOUT"; payload: number }
  | { type: "START_PROMPT"; payload: { draftIndex: number; draftId: string; currentActiveIndex: number } }
  | { type: "DISMISS_PROMPT"; payload: number }
  | { type: "SET_GENERATING"; payload: boolean }
  | { type: "SET_SAVING"; payload: boolean }
  | { type: "SET_DIRTY"; payload: boolean }
  | { type: "SAVE_SUCCESS"; payload: { details: StructuredTemplateDetailsResponse; document: TemplateV2LayoutsCompatibilityDocument; layouts: TemplateV2Layout[] } }
  | { type: "RESET_TO_SAVED" };

const initialState: TemplateEditorState = {
  loadedTemplate: null,
  layouts: [],
  activeLayoutIndex: 0,
  promptLayoutIndex: null,
  returnLayoutIndex: 0,
  loading: true,
  saving: false,
  generating: false,
  dirty: false,
  error: null,
};

function templateEditorReducer(
  state: TemplateEditorState,
  action: TemplateEditorAction,
): TemplateEditorState {
  switch (action.type) {
    case "LOAD_START":
      return {
        ...state,
        loading: true,
        error: null,
      };
    case "LOAD_SUCCESS":
      return {
        ...state,
        loading: false,
        loadedTemplate: {
          details: action.payload.details,
          document: action.payload.document,
        },
        layouts: layoutsFromDocument(action.payload.document),
        activeLayoutIndex: 0,
        promptLayoutIndex: null,
        dirty: false,
        error: null,
      };
    case "LOAD_ERROR":
      return {
        ...state,
        loading: false,
        error: action.payload,
      };
    case "UPDATE_LAYOUTS":
      return {
        ...state,
        layouts: action.payload,
        dirty: true,
      };
    case "SET_ACTIVE_LAYOUT":
      return {
        ...state,
        activeLayoutIndex: action.payload,
      };
    case "START_PROMPT":
      return {
        ...state,
        layouts: [...state.layouts, { id: action.payload.draftId, description: "AI로 생성할 새 레이아웃", components: [] }],
        activeLayoutIndex: action.payload.draftIndex,
        promptLayoutIndex: action.payload.draftIndex,
        returnLayoutIndex: action.payload.currentActiveIndex,
      };
    case "DISMISS_PROMPT":
      return {
        ...state,
        layouts: state.layouts.filter((_, index) => index !== state.promptLayoutIndex),
        activeLayoutIndex: Math.max(0, state.returnLayoutIndex),
        promptLayoutIndex: null,
      };
    case "SET_GENERATING":
      return {
        ...state,
        generating: action.payload,
      };
    case "SET_SAVING":
      return {
        ...state,
        saving: action.payload,
      };
    case "SET_DIRTY":
      return {
        ...state,
        dirty: action.payload,
      };
    case "SAVE_SUCCESS":
      return {
        ...state,
        saving: false,
        loadedTemplate: {
          details: action.payload.details,
          document: action.payload.document,
        },
        layouts: action.payload.layouts,
        dirty: false,
      };
    case "RESET_TO_SAVED":
      return {
        ...state,
        dirty: false,
      };
    default:
      return state;
  }
}

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
  const [state, dispatch] = useReducer(
    templateEditorReducer,
    initialState,
  );

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
  }, [templateId]);

  // ========================================================================
  // Derived state
  // ========================================================================

  const canEdit = Boolean(
    state.loadedTemplate && state.loadedTemplate.details.is_default !== true,
  );
  const activeLayout = state.layouts[state.activeLayoutIndex] ?? null;
  const previewSlide = useMemo(
    () =>
      activeLayout
        ? createPreviewSlide(activeLayout, state.activeLayoutIndex)
        : null,
    [activeLayout, state.activeLayoutIndex],
  );

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
    [canEdit, state.promptLayoutIndex, state.layouts],
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
  }, [canEdit, state.promptLayoutIndex, state.layouts, state.activeLayoutIndex]);

  const dismissPrompt = useCallback(() => {
    if (state.generating || state.promptLayoutIndex === null) return;
    dispatch({ type: "DISMISS_PROMPT", payload: state.returnLayoutIndex });
  }, [state.generating, state.promptLayoutIndex, state.returnLayoutIndex]);

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
        dispatch({ type: "DISMISS_PROMPT", payload: state.returnLayoutIndex });
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
      state.returnLayoutIndex,
      templateId,
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

          <PreviewSection
            previewSlide={previewSlide}
            activeLayout={activeLayout}
            isPrompting={state.promptLayoutIndex === state.activeLayoutIndex}
            isGenerating={state.generating}
            activeLayoutIndex={state.activeLayoutIndex}
            onDismissPrompt={dismissPrompt}
            onSubmitPrompt={generatePromptedLayout}
          />
        </main>
      </div>
    </div>
  );
}

// ============================================================================
// Sub-components
// ============================================================================

function LoadingState() {
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

type ErrorStateProps = {
  error: string | null;
  onNavigateBack: () => void;
};

function ErrorState({ error, onNavigateBack }: ErrorStateProps) {
  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <div className="flex flex-col items-center justify-center py-24">
        <h2 className="mb-4 text-2xl font-bold text-red-600">
          구조화 템플릿을 불러오지 못했습니다.
        </h2>
        <p className="mb-4 text-gray-600">{error}</p>
        <Button onClick={onNavigateBack}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          템플릿으로 돌아가기
        </Button>
      </div>
    </div>
  );
}

function EditReadOnlyWarning() {
  return (
    <div className="mx-auto mt-4 max-w-[1380px] rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      기본 템플릿은 미리보기만 제공됩니다. 복제한 템플릿에서 편집해 주세요.
    </div>
  );
}

type EditorHeaderProps = {
  templateName: string;
  revision: number;
  isDirty: boolean;
  canEdit: boolean;
  isPrompting: boolean;
  isSaving: boolean;
  onClickGenerateAI: () => void;
  onClickSave: () => void;
};

function EditorHeader({
  templateName,
  revision,
  isDirty,
  canEdit,
  isPrompting,
  isSaving,
  onClickGenerateAI,
  onClickSave,
}: EditorHeaderProps) {
  return (
    <div className="mx-auto flex max-w-[1380px] items-center justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">{templateName}</h1>
        <p className="mt-1 text-sm text-gray-500">
          Revision {revision}
          {isDirty ? " · 저장하지 않은 변경 사항" : ""}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button
          disabled={!canEdit || isPrompting}
          onClick={onClickGenerateAI}
          variant="outline"
        >
          <Sparkles className="mr-2 h-4 w-4" />
          AI 레이아웃 생성
        </Button>
        <Button
          disabled={!canEdit || !isDirty || isSaving || isPrompting}
          onClick={onClickSave}
        >
          {isSaving ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          저장
        </Button>
      </div>
    </div>
  );
}

type PreviewSectionProps = {
  previewSlide: ReturnType<typeof createPreviewSlide> | null;
  activeLayout: TemplateV2Layout | null;
  isPrompting: boolean;
  isGenerating: boolean;
  activeLayoutIndex: number;
  onDismissPrompt: () => void;
  onSubmitPrompt: (prompt: string) => Promise<boolean>;
};

function PreviewSection({
  previewSlide,
  activeLayout,
  isPrompting,
  isGenerating,
  activeLayoutIndex,
  onDismissPrompt,
  onSubmitPrompt,
}: PreviewSectionProps) {
  return (
    <div className="mt-6 overflow-auto rounded-2xl border border-gray-200 bg-gray-100 p-5 shadow-sm">
      <div className="relative mx-auto h-[720px] w-[1280px] overflow-hidden rounded-lg bg-white shadow">
        {previewSlide ? (
          <TemplateV2HtmlSlidePreviewMemoized slide={previewSlide} fixedSize />
        ) : (
          <div className="flex h-full items-center justify-center text-gray-500">
            템플릿에 레이아웃이 없습니다. AI 레이아웃을 생성해 주세요.
          </div>
        )}
        {isPrompting && activeLayout ? (
          <TemplateV2PromptOverlay
            isSubmitting={isGenerating}
            layout={activeLayout}
            onDismiss={onDismissPrompt}
            onSubmitPrompt={onSubmitPrompt}
            showLayoutPreview
            slideIndex={activeLayoutIndex}
          />
        ) : null}
      </div>
    </div>
  );
}

// Memoized components to prevent unnecessary re-renders
const LayoutsPanelMemoized = React.memo(LayoutsPanel);
const TemplateV2HtmlSlidePreviewMemoized = React.memo(
  TemplateV2HtmlSlidePreview,
);
