"use client";

import { useEffect, useMemo, useReducer } from "react";

import {
  EMPTY_TEMPLATE_V2_STUDIO_STATE,
  createTemplateV2Rectangle,
  findTemplateV2Layout,
  getSelectedElement,
  getTemplateV2Scene,
  listTemplateV2Components,
  listTemplateV2Layouts,
  parseTemplateV2SelectionKey,
  templateV2StudioReducer,
  type ElementPath,
  type StudioSelection,
  type TemplateV2AlignDirection,
  type TemplateV2DistributeDirection,
  type TemplateV2ReorderDirection,
} from "@/lib/template-v2-studio";
import { elementPosition } from "@/lib/template-v2-konva";
import { getTemplateV2HistoryKeyboardIntent } from "@/lib/template-v2-studio-keyboard";
import { toggleTemplateV2Selection } from "@/lib/template-v2-studio-ui";
import TemplateV2Canvas from "./TemplateV2Canvas";
import TemplateV2AiRewritePanel from "./TemplateV2AiRewritePanel";
import TemplateV2ConflictRecovery from "./TemplateV2ConflictRecovery";
import TemplateV2ContentInspector from "./TemplateV2ContentInspector";
import TemplateV2DraftRecovery from "./TemplateV2DraftRecovery";
import TemplateV2ElementTree, {
  getTemplateV2SelectionControls,
  pathLabel,
} from "./TemplateV2ElementTree";
import TemplateV2GeometryInspector from "./TemplateV2GeometryInspector";
import TemplateV2PptxImportPanel from "./TemplateV2PptxImportPanel";
import { useTemplateV2StudioPersistence } from "./useTemplateV2StudioPersistence";

export default function TemplateV2Studio({
  templateId,
}: {
  templateId: string;
}) {
  const [state, dispatch] = useReducer(
    templateV2StudioReducer,
    EMPTY_TEMPLATE_V2_STUDIO_STATE
  );
  const {
    template,
    loading,
    saving,
    notice,
    setNotice,
    error,
    conflict,
    recoveryDraft,
    rebasing,
    flushAutosave,
    reloadServerVersion,
    restoreRecoveryDraft,
    discardRecoveryDraft,
    rebaseConflict,
  } = useTemplateV2StudioPersistence({ templateId, state, dispatch });

  const activeLayout = findTemplateV2Layout(
    state.layouts,
    state.activeLayoutId
  );
  const scene = getTemplateV2Scene(
    state.layouts,
    state.activeLayoutId,
    state.activeComponentId
  );
  const selectedElement = getSelectedElement(state.layouts, state.selection);
  const selectedPathKeys = useMemo(
    () =>
      new Set(
        state.selectionSet.map((selection) =>
          JSON.stringify(selection.elementPath)
        )
      ),
    [state.selectionSet]
  );
  const lockedSelections = useMemo(
    () =>
      [...state.lockedElementKeys]
        .map(parseTemplateV2SelectionKey)
        .filter((selection): selection is StudioSelection => Boolean(selection)),
    [state.lockedElementKeys]
  );
  const lockedPathKeys = useMemo(() => {
    if (!scene) return new Set<string>();
    const layoutId = String(scene.layout.id);
    const componentId = String(scene.component.id);
    return new Set(
      lockedSelections
        .filter(
          (selection) =>
            selection.layoutId === layoutId &&
            selection.componentId === componentId
        )
        .map((selection) => JSON.stringify(selection.elementPath))
    );
  }, [lockedSelections, scene]);
  const selectionControls = useMemo(
    () =>
      getTemplateV2SelectionControls({
        scene,
        selections: state.selectionSet,
        lockedSelections,
        lockedElementKeys: state.lockedElementKeys,
      }),
    [lockedSelections, scene, state.lockedElementKeys, state.selectionSet]
  );

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const intent = getTemplateV2HistoryKeyboardIntent(event, {
        canUndo: state.past.length > 0,
        canRedo: state.future.length > 0,
      });
      if (!intent) return;
      event.preventDefault();
      dispatch({ type: intent });
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [state.future.length, state.past.length]);

  function selectElement(path: ElementPath | null, additive = false) {
    if (!path || !scene) {
      dispatch({ type: "select", selection: null });
      return;
    }
    const candidate: StudioSelection = {
      layoutId: String(scene.layout.id),
      componentId: String(scene.component.id),
      elementPath: path,
    };
    const selections = toggleTemplateV2Selection(
      state.selectionSet,
      candidate,
      additive
    );
    if (selections.length > 1) {
      dispatch({ type: "set-selection", selections });
    } else {
      dispatch({ type: "select", selection: selections[0] ?? null });
    }
  }

  function executeSelectionCommand(
    command:
      | "group-siblings"
      | "ungroup"
      | "front"
      | "forward"
      | "backward"
      | "back"
      | `align-${TemplateV2AlignDirection}`
      | `distribute-${TemplateV2DistributeDirection}`
  ) {
    if (command === "group-siblings" || command === "ungroup") {
      dispatch({
        type: "execute-command",
        command: { type: command, selections: state.selectionSet },
      });
    } else if (command.startsWith("align-")) {
      dispatch({
        type: "execute-command",
        command: {
          type: "align-siblings",
          direction: command.slice("align-".length) as TemplateV2AlignDirection,
          selections: state.selectionSet,
        },
      });
    } else if (command.startsWith("distribute-")) {
      dispatch({
        type: "execute-command",
        command: {
          type: "distribute-siblings",
          direction: command.slice(
            "distribute-".length
          ) as TemplateV2DistributeDirection,
          selections: state.selectionSet,
        },
      });
    } else {
      dispatch({
        type: "execute-command",
        command: {
          type: "reorder-siblings",
          direction: command as TemplateV2ReorderDirection,
          selections: state.selectionSet,
        },
      });
    }
    setNotice(null);
  }

  if (loading) {
    return (
      <main className="grid min-h-screen place-items-center bg-slate-950 text-slate-200">
        Loading structured template…
      </main>
    );
  }

  if (error && !template) {
    return (
      <main className="grid min-h-screen place-items-center bg-slate-950 p-8 text-slate-200">
        <section className="max-w-xl rounded-xl border border-red-500/40 bg-red-950/30 p-6">
          <h1 className="text-xl font-semibold">Template V2 Studio unavailable</h1>
          <p className="mt-2 text-red-200">{error}</p>
        </section>
      </main>
    );
  }

  if (!template || !state.layouts) return null;

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 px-6 py-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-violet-300">
            Template V2 Studio · Experimental
          </p>
          <h1 className="mt-1 text-xl font-semibold">{template.name}</h1>
          <p className="text-sm text-slate-400">
            Revision {template.revision}
            {state.dirty ? " · Unsaved changes" : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {notice ? (
            <span
              role="status"
              aria-live="polite"
              className="text-sm text-emerald-300"
            >
              {notice}
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => dispatch({ type: "undo" })}
            disabled={!state.past.length}
            aria-label="Undo (Ctrl or Command plus Z)"
            title="Undo · Ctrl/Cmd+Z"
            className="rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-40"
          >
            Undo
          </button>
          <button
            type="button"
            onClick={() => dispatch({ type: "redo" })}
            disabled={!state.future.length}
            aria-label="Redo (Ctrl or Command plus Shift plus Z, or Ctrl plus Y)"
            title="Redo · Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y"
            className="rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-40"
          >
            Redo
          </button>
          <button
            type="button"
            onClick={() => {
              if (!scene) return;
              dispatch({
                type: "add-rectangle",
                layoutId: String(scene.layout.id),
                componentId: String(scene.component.id),
                element: createTemplateV2Rectangle(),
              });
              setNotice(null);
            }}
            disabled={!scene}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm disabled:opacity-40"
          >
            Add rectangle
          </button>
          <button
            type="button"
            onClick={() => void flushAutosave()}
            disabled={!state.dirty || saving || Boolean(conflict)}
            title={
              conflict
                ? "Reload the server version before saving again."
                : "Flush pending autosave now"
            }
            className="rounded-lg bg-violet-500 px-4 py-2 text-sm font-semibold disabled:opacity-40"
          >
            {saving ? "Saving…" : error && !conflict ? "Retry save" : "Save now"}
          </button>
        </div>
      </header>

      {recoveryDraft ? (
        <TemplateV2DraftRecovery
          draft={recoveryDraft}
          serverRevision={template.revision}
          safeToRestore={JSON.stringify(recoveryDraft.baseLayouts) === JSON.stringify(template.layouts)}
          onRestore={restoreRecoveryDraft}
          onDiscard={discardRecoveryDraft}
        />
      ) : null}

      <TemplateV2PptxImportPanel currentTemplateId={template.id} />

      {error ? (
        <div
          role="alert"
          className="flex items-center justify-between gap-4 border-b border-red-500/30 bg-red-950/40 px-6 py-3 text-sm text-red-200"
        >
          <span>{error}</span>
          {conflict ? (
            <TemplateV2ConflictRecovery
              snapshot={conflict}
              onReload={reloadServerVersion}
              onRebase={() => void rebaseConflict()}
              rebasing={rebasing}
            />
          ) : null}
        </div>
      ) : null}

      <div className="grid gap-4 p-4 xl:grid-cols-[230px_minmax(0,1fr)_300px]">
        <nav className="rounded-xl border border-slate-800 bg-slate-900 p-4">
          <h2 className="font-semibold">Layouts</h2>
          <div className="mt-3 space-y-2">
            {listTemplateV2Layouts(state.layouts).map((layout) => {
              const layoutId = String(layout.id);
              const components = listTemplateV2Components(layout);
              return (
                <section key={layoutId}>
                  <button
                    type="button"
                    className={`w-full rounded px-2 py-1 text-left text-sm ${
                      state.activeLayoutId === layoutId
                        ? "bg-slate-800 text-white"
                        : "text-slate-400"
                    }`}
                    onClick={() =>
                      dispatch({
                        type: "set-active",
                        layoutId,
                        componentId:
                          components.length > 0 ? String(components[0].id) : null,
                      })
                    }
                  >
                    {layoutId}
                  </button>
                  {state.activeLayoutId === layoutId ? (
                    <div className="ml-2 mt-1 space-y-1 border-l border-slate-700 pl-2">
                      {components.map((component) => {
                        const componentId = String(component.id);
                        return (
                          <button
                            key={componentId}
                            type="button"
                            className={`block w-full rounded px-2 py-1 text-left text-xs ${
                              state.activeComponentId === componentId
                                ? "bg-violet-500/25 text-violet-100"
                                : "text-slate-400 hover:bg-slate-800"
                            }`}
                            onClick={() =>
                              dispatch({
                                type: "set-active",
                                layoutId,
                                componentId,
                              })
                            }
                          >
                            {componentId}
                          </button>
                        );
                      })}
                      {!components.length ? (
                        <p className="px-2 py-1 text-xs text-slate-500">
                          No components
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                </section>
              );
            })}
          </div>
          {scene ? (
            <>
              <h3 className="mt-6 text-sm font-semibold">Elements</h3>
              <div className="mt-2">
                <TemplateV2ElementTree
                  elements={scene.elements}
                  selectedPathKeys={selectedPathKeys}
                  lockedPathKeys={lockedPathKeys}
                  onSelect={selectElement}
                />
              </div>
            </>
          ) : null}
        </nav>

        <section className="rounded-xl border border-slate-800 bg-slate-900 p-3">
          {scene ? (
            <TemplateV2Canvas
              scene={scene}
              selectedPaths={state.selectionSet.map(
                (selection) => selection.elementPath
              )}
              lockedPaths={lockedSelections
                .filter(
                  (selection) =>
                    selection.layoutId === String(scene.layout.id) &&
                    selection.componentId === String(scene.component.id)
                )
                .map((selection) => selection.elementPath)}
              onSelect={selectElement}
              onGeometryBatch={(updates) => {
                dispatch({
                  type: "execute-command",
                  command: {
                    type: "update-geometry-batch",
                    updates: updates.map(({ elementPath, geometry }) => ({
                      selection: {
                        layoutId: String(scene.layout.id),
                        componentId: String(scene.component.id),
                        elementPath,
                      },
                      geometry,
                    })),
                  },
                });
                setNotice(null);
              }}
            />
          ) : (
            <div className="grid h-[68vh] min-h-[360px] place-items-center text-slate-500">
              Select a layout with a component.
            </div>
          )}
        </section>

        <aside className="rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h2 className="font-semibold">Inspector</h2>
          {scene ? (
            <p className="mt-1 text-xs text-slate-400">
              {String(scene.layout.id)} / {String(scene.component.id)}
            </p>
          ) : null}
          <section className="mt-5 border-t border-slate-800 pt-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium">Selection</h3>
              <span className="text-xs text-slate-400">
                {state.selectionSet.length} selected
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Ctrl/Cmd-click sibling elements to select more than one.
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {[
                ["Bring to front", "front", selectionControls.canBringToFront],
                ["Bring forward", "forward", selectionControls.canMoveForward],
                ["Send backward", "backward", selectionControls.canMoveBackward],
                ["Send to back", "back", selectionControls.canSendToBack],
              ].map(([label, direction, enabled]) => (
                <button
                  key={String(direction)}
                  type="button"
                  disabled={!enabled}
                  title={
                    enabled
                      ? undefined
                      : "Select unlocked siblings that can move in this direction."
                  }
                  onClick={() =>
                    executeSelectionCommand(
                      direction as "front" | "forward" | "backward" | "back"
                    )
                  }
                  className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {label}
                </button>
              ))}
              <button
                type="button"
                disabled={!selectionControls.canGroup}
                title={
                  selectionControls.canGroup
                    ? undefined
                    : "Select at least two unlocked sibling elements."
                }
                onClick={() => executeSelectionCommand("group-siblings")}
                className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
              >
                Group
              </button>
              <button
                type="button"
                disabled={!selectionControls.canUngroup}
                title={
                  selectionControls.canUngroup
                    ? undefined
                    : "Select one or more unlocked groups."
                }
                onClick={() => executeSelectionCommand("ungroup")}
                className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
              >
                Ungroup
              </button>
              <button
                type="button"
                disabled={!selectionControls.hasSelection}
                onClick={() => {
                  const locked = !selectionControls.allLocked;
                  for (const selection of state.selectionSet) {
                    dispatch({
                      type: "set-element-lock",
                      selection,
                      locked,
                    });
                  }
                  setNotice(null);
                }}
                className="col-span-2 rounded border border-amber-500/50 px-2 py-1.5 text-xs text-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {selectionControls.allLocked
                  ? "Unlock selected"
                  : "Lock selected"}
              </button>
            </div>
            <div className="mt-3">
              <h4 className="text-xs font-medium text-slate-300">Align</h4>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {(
                  [
                    ["Align left", "left"],
                    ["Align center", "center"],
                    ["Align right", "right"],
                    ["Align top", "top"],
                    ["Align middle", "middle"],
                    ["Align bottom", "bottom"],
                  ] as const
                ).map(([label, direction]) => (
                  <button
                    key={direction}
                    type="button"
                    disabled={!selectionControls.canAlign}
                    title={
                      selectionControls.canAlign
                        ? undefined
                        : "Select at least two unlocked sibling elements."
                    }
                    onClick={() =>
                      executeSelectionCommand(`align-${direction}`)
                    }
                    className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-3">
              <h4 className="text-xs font-medium text-slate-300">Distribute</h4>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {(
                  [
                    ["Distribute horizontally", "horizontal"],
                    ["Distribute vertically", "vertical"],
                  ] as const
                ).map(([label, direction]) => (
                  <button
                    key={direction}
                    type="button"
                    disabled={!selectionControls.canDistribute}
                    title={
                      selectionControls.canDistribute
                        ? undefined
                        : "Select at least three unlocked sibling elements."
                    }
                    onClick={() =>
                      executeSelectionCommand(`distribute-${direction}`)
                    }
                    className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {selectionControls.lockConflict ? (
              <p className="mt-2 text-xs text-amber-300">
                Locked elements cannot be transformed, reordered, or grouped.
              </p>
            ) : null}
          </section>
          {selectedElement &&
          state.selection &&
          state.selectionSet.length === 1 ? (
            <>
              <TemplateV2GeometryInspector
                element={selectedElement}
                disabled={selectionControls.lockConflict}
                onChange={(geometry) => {
                  dispatch({
                    type: "update-element-geometry",
                    selection: state.selection as StudioSelection,
                    geometry,
                  });
                  setNotice(null);
                }}
              />
              <TemplateV2AiRewritePanel
                key={`${state.selection.layoutId}:${state.selection.componentId}:${pathLabel(
                  state.selection.elementPath
                )}`}
                element={selectedElement}
                targetId={`${state.selection.layoutId}:${state.selection.componentId}:${pathLabel(
                  state.selection.elementPath
                )}`}
                revision={template.revision}
                disabled={selectionControls.lockConflict}
                onApply={(patch, historyKey) => {
                  dispatch({
                    type: "apply-text-selection-patch",
                    selection: state.selection as StudioSelection,
                    patch,
                    historyKey,
                  });
                  setNotice("AI rewrite applied. Autosave scheduled.");
                }}
              />
              <TemplateV2ContentInspector
                element={selectedElement}
                pathLabel={pathLabel(state.selection.elementPath)}
                disabled={selectionControls.lockConflict}
                onBlur={() =>
                  dispatch({
                    type: "select",
                    selection: state.selection,
                  })
                }
                onEdit={(target, text, historyKey) => {
                  dispatch({
                    type: "edit-content-run",
                    selection: state.selection as StudioSelection,
                    target,
                    text,
                    historyKey,
                  });
                  setNotice(null);
                }}
              />
            </>
          ) : (
            <p className="mt-5 rounded-lg bg-slate-950 p-3 text-sm text-slate-400">
              Select a text, container, image, or group. Groups are move-only;
              unsupported elements remain lossless.
            </p>
          )}
          {scene ? (
            <dl className="mt-6 space-y-3 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-slate-400">Elements</dt>
                <dd>{scene.elements.length}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-400">Component X/Y</dt>
                <dd>
                  {elementPosition(scene.component).x} /{" "}
                  {elementPosition(scene.component).y}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-400">History</dt>
                <dd>
                  {state.past.length} / {state.future.length}
                </dd>
              </div>
            </dl>
          ) : null}
          {!activeLayout ? (
            <p className="mt-4 text-sm text-slate-500">No layouts available.</p>
          ) : null}
        </aside>
      </div>
    </main>
  );
}
