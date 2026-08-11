import type { ServerIssuedPresentationProfile } from "../../../../lib/presentation-profile";

/**
 * The page consumes this view model instead of branching on persisted version
 * or generation-mode fields.  It is intentionally a render-only projection:
 * commands remain owned by the canonical workspace conversation boundary.
 */
export type PresentationFeatureSlotName =
  | "editorSurface"
  | "assistantPanel"
  | "selectionOverlay"
  | "headerActions"
  | "exportActions"
  | "runtimeDependencies";

export type PresentationFeatureSlots = Readonly<
  Record<PresentationFeatureSlotName, boolean>
>;

export interface PresentationFeatureViewModel {
  readonly slots: PresentationFeatureSlots;
  readonly driverId: ServerIssuedPresentationProfile["profile"]["driver_id"] | null;
  readonly authored: boolean;
  readonly reviewRequired: boolean;
  readonly runtimeDependencies: readonly string[];
  readonly renderActions: readonly ("preview" | "pdf" | "pptx")[];
  readonly background: string;
  readonly componentAdapters: Readonly<{
    editor: string | null;
    assistant: "canonical-workspace" | null;
    export: string | null;
    selection: string | null;
  }>;
  readonly selectionEnabled: boolean;
}

export interface PresentationPageDriverAdapter {
  readonly driverId: NonNullable<PresentationFeatureViewModel["driverId"]>;
  readonly editorAdapterId: string;
  readonly assistantAdapterId: "canonical-workspace";
  readonly exportAdapterId: string;
  readonly selectionControllerId: string | null;
  readonly background: string;
  readonly renderActions: readonly ("preview" | "pdf" | "pptx")[];
}

const RENDER_ACTIONS = Object.freeze(["preview", "pdf", "pptx"] as const);

export const PRESENTATION_PAGE_DRIVER_ADAPTER_REGISTRY = Object.freeze({
  standard: Object.freeze({
    driverId: "standard",
    editorAdapterId: "legacy-elements",
    assistantAdapterId: "canonical-workspace",
    exportAdapterId: "legacy-general",
    selectionControllerId: null,
    background: "#EDEEEF",
    renderActions: RENDER_ACTIONS,
  }),
  smart: Object.freeze({
    driverId: "smart",
    editorAdapterId: "smart-html",
    assistantAdapterId: "canonical-workspace",
    exportAdapterId: "legacy-general",
    selectionControllerId: "smart-html-selection",
    background: "#F6F6F9",
    renderActions: RENDER_ACTIONS,
  }),
  "template-v2": Object.freeze({
    driverId: "template-v2",
    editorAdapterId: "template-v2",
    assistantAdapterId: "canonical-workspace",
    exportAdapterId: "template-v2-general",
    selectionControllerId: null,
    background: "#EDEEEF",
    renderActions: RENDER_ACTIONS,
  }),
  authored: Object.freeze({
    driverId: "authored",
    editorAdapterId: "authored-readonly",
    assistantAdapterId: "canonical-workspace",
    exportAdapterId: "authored-hybrid",
    selectionControllerId: null,
    background: "#EDEEEF",
    renderActions: RENDER_ACTIONS,
  }),
} satisfies Readonly<
  Record<
    NonNullable<PresentationFeatureViewModel["driverId"]>,
    PresentationPageDriverAdapter
  >
>);

export function buildPresentationFeatureViewModel({
  runtime,
  workspaceReady,
  loading = false,
}: {
  readonly runtime: ServerIssuedPresentationProfile | null;
  readonly workspaceReady: boolean;
  readonly loading?: boolean;
}): PresentationFeatureViewModel {
  // A loading response is not an unknown profile.  Keep the shell mounted for
  // its skeleton, but grant no profile-specific actions until the server sends
  // a validated profile/capability pair.
  if (!runtime) {
    return Object.freeze({
      driverId: null,
      authored: false,
      reviewRequired: !loading,
      runtimeDependencies: Object.freeze([]),
      renderActions: Object.freeze([]),
      background: "#EDEEEF",
      componentAdapters: Object.freeze({
        editor: null,
        assistant: null,
        export: null,
        selection: null,
      }),
      selectionEnabled: false,
      slots: Object.freeze({
        editorSurface: loading,
        assistantPanel: workspaceReady,
        selectionOverlay: false,
        headerActions: false,
        exportActions: false,
        runtimeDependencies: false,
      }),
    });
  }

  const { capabilities, profile } = runtime;
  const adapter = PRESENTATION_PAGE_DRIVER_ADAPTER_REGISTRY[profile.driver_id];
  if (
    adapter.driverId !== profile.driver_id ||
    adapter.editorAdapterId !== capabilities.editor_adapter_id ||
    adapter.exportAdapterId !== capabilities.export_adapter_id ||
    Boolean(adapter.selectionControllerId) !== capabilities.editor.smart_selection
  ) {
    throw new Error("presentation_page_adapter_capability_conflict");
  }
  return Object.freeze({
    driverId: profile.driver_id,
    authored: profile.driver_id === "authored",
    reviewRequired: false,
    runtimeDependencies: Object.freeze([
      capabilities.renderer_adapter_id,
      capabilities.editor_adapter_id,
      capabilities.export_adapter_id,
    ]),
    renderActions: adapter.renderActions,
    background: adapter.background,
    componentAdapters: Object.freeze({
      editor: adapter.editorAdapterId,
      assistant: workspaceReady ? adapter.assistantAdapterId : null,
      export: adapter.exportAdapterId,
      selection: adapter.selectionControllerId,
    }),
    selectionEnabled: adapter.selectionControllerId !== null,
    slots: Object.freeze({
      // Viewing the canonical artifact is supported even by an editor adapter
      // that intentionally denies mutations (the authored driver).
      editorSurface: true,
      assistantPanel: workspaceReady,
      selectionOverlay: capabilities.editor.smart_selection,
      headerActions: true,
      exportActions: Boolean(capabilities.export_adapter_id),
      runtimeDependencies: true,
    }),
  });
}
