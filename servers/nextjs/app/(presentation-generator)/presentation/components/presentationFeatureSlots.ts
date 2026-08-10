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
}

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
  return Object.freeze({
    driverId: profile.driver_id,
    authored: profile.driver_id === "authored",
    reviewRequired: false,
    runtimeDependencies: Object.freeze([
      capabilities.renderer_adapter_id,
      capabilities.editor_adapter_id,
      capabilities.export_adapter_id,
    ]),
    renderActions: Object.freeze([
      "preview",
      "pdf",
      "pptx",
    ] as const),
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
