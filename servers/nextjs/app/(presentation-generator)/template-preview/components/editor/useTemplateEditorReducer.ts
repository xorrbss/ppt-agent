import { useReducer } from "react";

import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import type { TemplateV2LayoutsCompatibilityDocument } from "@/lib/template-v2-upstream-compat";
import { cloneLayout } from "@/lib/template-v2-preview";
import type { TemplateEditorAction, TemplateEditorState } from "./types";

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

export function templateEditorReducer(
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
    case "COMPLETE_PROMPT":
      // Success path: keep the generated layout in place and only clear the
      // prompt state. DISMISS_PROMPT is the cancel path that removes the draft.
      return {
        ...state,
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

export function layoutsFromDocument(
  document: TemplateV2LayoutsCompatibilityDocument,
): TemplateV2Layout[] {
  const layouts = document.studioLayouts.layouts;
  if (!Array.isArray(layouts)) {
    throw new Error("Structured template layouts are invalid.");
  }
  return layouts.map((layout) => cloneLayout(layout as TemplateV2Layout));
}

export function useTemplateEditorReducer() {
  return useReducer(templateEditorReducer, initialState);
}
