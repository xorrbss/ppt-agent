import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import type { TemplateV2LayoutsCompatibilityDocument } from "@/lib/template-v2-upstream-compat";
import type { StructuredTemplateDetailsResponse } from "../../../services/api/template";

export type LoadedTemplate = {
  details: StructuredTemplateDetailsResponse;
  document: TemplateV2LayoutsCompatibilityDocument;
};

export type TemplateEditorState = {
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

export type TemplateEditorAction =
  | { type: "LOAD_START" }
  | { type: "LOAD_SUCCESS"; payload: { details: StructuredTemplateDetailsResponse; document: TemplateV2LayoutsCompatibilityDocument } }
  | { type: "LOAD_ERROR"; payload: string }
  | { type: "UPDATE_LAYOUTS"; payload: TemplateV2Layout[] }
  | { type: "SET_ACTIVE_LAYOUT"; payload: number }
  | { type: "START_PROMPT"; payload: { draftIndex: number; draftId: string; currentActiveIndex: number } }
  | { type: "DISMISS_PROMPT"; payload: number }
  | { type: "COMPLETE_PROMPT" }
  | { type: "SET_GENERATING"; payload: boolean }
  | { type: "SET_SAVING"; payload: boolean }
  | { type: "SET_DIRTY"; payload: boolean }
  | { type: "SAVE_SUCCESS"; payload: { details: StructuredTemplateDetailsResponse; document: TemplateV2LayoutsCompatibilityDocument; layouts: TemplateV2Layout[] } }
  | { type: "RESET_TO_SAVED" };
