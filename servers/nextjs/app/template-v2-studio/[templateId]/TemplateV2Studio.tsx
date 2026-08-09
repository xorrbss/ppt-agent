"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";

import {
  EMPTY_TEMPLATE_V2_STUDIO_STATE,
  createTemplateV2Rectangle,
  findTemplateV2Layout,
  getSelectedElement,
  getTemplateV2Scene,
  isJsonRecord,
  listTemplateV2Components,
  listTemplateV2Layouts,
  parseTemplateV2SelectionKey,
  templateV2StudioReducer,
  type ElementPath,
  type JsonRecord,
  type StudioSelection,
  type TemplateV2AlignDirection,
  type TemplateV2DistributeDirection,
  type TemplateV2ReorderDirection,
} from "@/lib/template-v2-studio";
import { applyTemplateV2ImageReplacementPatch } from "@/lib/template-v2-image-replacement";
import { templateV2VariantDigest } from "@/lib/template-v2-slide-variants";
import { elementPosition } from "@/lib/template-v2-konva";
import {
  getTemplateV2HistoryKeyboardIntent,
  getTemplateV2SelectionKeyboardIntent,
} from "@/lib/template-v2-studio-keyboard";
import type {
  TemplateV2CanvasCropDraft,
  TemplateV2CanvasSelectionBounds,
} from "@/lib/template-v2-studio-media";
import {
  positionTemplateV2Toolbar,
  type TemplateV2ToolbarPosition,
} from "@/lib/template-v2-toolbar";
import { toggleTemplateV2Selection } from "@/lib/template-v2-studio-ui";
import TemplateV2Canvas from "./TemplateV2Canvas";
import TemplateV2AiRewritePanel from "./TemplateV2AiRewritePanel";
import TemplateV2ChartCopilotPanel from "./TemplateV2ChartCopilotPanel";
import TemplateV2ConflictRecovery from "./TemplateV2ConflictRecovery";
import TemplateV2ContentInspector from "./TemplateV2ContentInspector";
import TemplateV2DraftRecovery from "./TemplateV2DraftRecovery";
import TemplateV2ElementTree, {
  getTemplateV2SelectionControls,
  pathLabel,
} from "./TemplateV2ElementTree";
import TemplateV2GeometryInspector from "./TemplateV2GeometryInspector";
import TemplateV2ImageReplacementPanel from "./TemplateV2ImageReplacementPanel";
import TemplateV2PptxImportPanel from "./TemplateV2PptxImportPanel";
import TemplateV2QualityInspector from "./TemplateV2QualityInspector";
import TemplateV2SlideVariantsPanel from "./TemplateV2SlideVariantsPanel";
import TemplateV2TableEditorPanel from "./TemplateV2TableEditorPanel";
import {
  TemplateV2SelectionToolbar,
  type TemplateV2ImageCropDraft,
  type TemplateV2ToolbarPatch,
  type TemplateV2ToolbarPatchOptions,
} from "./toolbar";
import { useTemplateV2StudioPersistence } from "./useTemplateV2StudioPersistence";
import {
  ELEMENT_INSERT_GROUPS,
  createElementInsertElements,
  createTextInsertElements,
} from "@/components/slide-editor/insert/insert-elements";
import { useTranslations } from "next-intl";
import { UiLocaleSwitcher } from "@/components/i18n/UiLocaleSwitcher";

const TEMPLATE_V2_TOOLBAR_WIDTH = 760;
const TEMPLATE_V2_TOOLBAR_HEIGHT = 56;

function cloneJsonValue<T>(value: T): T {
  return structuredClone(value);
}

function setPathValue(
  value: unknown,
  path: Array<string | number>,
  nextValue: unknown
): unknown {
  if (path.length === 0) return cloneJsonValue(nextValue);
  const [part, ...rest] = path;
  const container =
    typeof part === "number"
      ? Array.isArray(value)
        ? cloneJsonValue(value)
        : []
      : isJsonRecord(value)
        ? cloneJsonValue(value)
        : {};
  if (typeof part === "number" && Array.isArray(container)) {
    container[part] = setPathValue(container[part], rest, nextValue);
  } else if (typeof part === "string" && isJsonRecord(container)) {
    container[part] = setPathValue(container[part], rest, nextValue);
  }
  return container;
}

/**
 * The command facade patches objects recursively and replaces arrays. Build a
 * complete copy of every touched top-level field so run/item edits keep all
 * unrecognised JSON beside the edited value.
 */
function losslessToolbarPatch(
  element: JsonRecord,
  patches: TemplateV2ToolbarPatch[]
): JsonRecord {
  const result: JsonRecord = {};
  for (const patch of patches) {
    const [root, ...rest] = patch.path;
    if (typeof root !== "string") continue;
    const current = Object.prototype.hasOwnProperty.call(result, root)
      ? result[root]
      : element[root];
    result[root] = setPathValue(current, rest, patch.value);
  }
  return result;
}

export default function TemplateV2Studio({
  templateId,
}: {
  templateId: string;
}) {
  const t = useTranslations("studio");
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
  const canvasSurfaceRef = useRef<HTMLElement | null>(null);
  const [selectionBounds, setSelectionBounds] =
    useState<TemplateV2CanvasSelectionBounds | null>(null);
  const [toolbarPosition, setToolbarPosition] =
    useState<TemplateV2ToolbarPosition | null>(null);
  const [cropModePath, setCropModePath] = useState<ElementPath | null>(null);
  const [cropDraft, setCropDraft] =
    useState<TemplateV2CanvasCropDraft | null>(null);

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
  const selectionIdentity = useMemo(
    () =>
      JSON.stringify(
        state.selectionSet.map((selection) => [
          selection.layoutId,
          selection.componentId,
          selection.elementPath,
        ])
      ),
    [state.selectionSet]
  );

  const updateToolbarPosition = useCallback(() => {
    const surface = canvasSurfaceRef.current;
    if (!surface || state.selectionSet.length === 0) {
      setToolbarPosition(null);
      return;
    }
    const canvasRect =
      surface.firstElementChild?.getBoundingClientRect() ??
      surface.getBoundingClientRect();
    const anchor = selectionBounds
      ? {
          x: canvasRect.left + selectionBounds.left,
          y: canvasRect.top + selectionBounds.top,
          width: selectionBounds.width,
          height: selectionBounds.height,
        }
      : {
          x: canvasRect.left + canvasRect.width / 2,
          y: canvasRect.top + 12,
          width: 1,
          height: 1,
        };
    setToolbarPosition(
      positionTemplateV2Toolbar({
        anchor,
        bounds: {
          left: 0,
          top: 0,
          right: window.innerWidth,
          bottom: window.innerHeight,
        },
        toolbarWidth: Math.min(
          TEMPLATE_V2_TOOLBAR_WIDTH,
          Math.max(0, window.innerWidth - 16)
        ),
        toolbarHeight: TEMPLATE_V2_TOOLBAR_HEIGHT,
      })
    );
  }, [selectionBounds, state.selectionSet.length]);

  useEffect(() => {
    updateToolbarPosition();
    const surface = canvasSurfaceRef.current;
    const observer =
      surface && typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(updateToolbarPosition)
        : null;
    if (surface) observer?.observe(surface);
    if (surface?.firstElementChild instanceof Element) {
      observer?.observe(surface.firstElementChild);
    }
    window.addEventListener("resize", updateToolbarPosition);
    window.addEventListener("scroll", updateToolbarPosition, true);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateToolbarPosition);
      window.removeEventListener("scroll", updateToolbarPosition, true);
    };
  }, [updateToolbarPosition]);

  useEffect(() => {
    setCropModePath(null);
    setCropDraft(null);
  }, [selectionIdentity]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const historyIntent = getTemplateV2HistoryKeyboardIntent(event, {
        canUndo: state.past.length > 0,
        canRedo: state.future.length > 0,
      });
      if (historyIntent) {
        event.preventDefault();
        dispatch({ type: historyIntent });
        return;
      }
      const selectionIntent = getTemplateV2SelectionKeyboardIntent(event, {
        selectionCount: state.selectionSet.length,
        canGroup: selectionControls.canGroup,
        canUngroup: selectionControls.canUngroup,
        canDelete:
          selectionControls.hasSelection && !selectionControls.lockConflict,
        canDuplicate:
          selectionControls.hasSelection && !selectionControls.lockConflict,
      });
      if (!selectionIntent) return;
      event.preventDefault();
      dispatch({
        type: "execute-command",
        command:
          selectionIntent === "delete-selection"
            ? { type: "delete-elements", selections: state.selectionSet }
            : selectionIntent === "duplicate-selection"
              ? { type: "duplicate-elements", selections: state.selectionSet }
              : { type: selectionIntent, selections: state.selectionSet },
      });
      setNotice(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    selectionControls.canGroup,
    selectionControls.canUngroup,
    selectionControls.hasSelection,
    selectionControls.lockConflict,
    setNotice,
    state.future.length,
    state.past.length,
    state.selectionSet,
  ]);

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

  function setSelectionLocked(locked: boolean) {
    for (const selection of state.selectionSet) {
      dispatch({
        type: "set-element-lock",
        selection,
        locked,
      });
    }
    setNotice(null);
  }

  function deleteSelection() {
    dispatch({
      type: "execute-command",
      command: { type: "delete-elements", selections: state.selectionSet },
    });
    setNotice(null);
  }

  function duplicateSelection() {
    dispatch({
      type: "execute-command",
      command: { type: "duplicate-elements", selections: state.selectionSet },
    });
    setNotice(null);
  }

  function patchSelectedElement(
    patches: TemplateV2ToolbarPatch[],
    options?: TemplateV2ToolbarPatchOptions
  ) {
    if (
      !state.selection ||
      !selectedElement ||
      selectionControls.lockConflict ||
      patches.length === 0
    ) {
      return;
    }
    dispatch({
      type: "execute-command",
      command: {
        type: "patch-element",
        selection: state.selection,
        patch: losslessToolbarPatch(selectedElement, patches),
        historyKey: options?.continuous && options.historyKey
          ? `${options.historyKey}:${selectionIdentity}`
          : undefined,
      },
    });
    setNotice(null);
  }

  function replaceSelectedMedia(label: "image" | "icon") {
    if (!selectedElement || !state.selection || selectionControls.lockConflict) {
      return;
    }
    const replacement = window.prompt(
      label === "icon"
        ? t("root.replaceIconPrompt")
        : t("root.replaceImagePrompt"),
      typeof selectedElement.data === "string" ? selectedElement.data : ""
    );
    if (replacement === null || replacement.trim() === "") return;
    patchSelectedElement(
      [
        { path: ["data"], value: replacement.trim() },
        ...(label === "icon"
          ? [{ path: ["is_icon"], value: true } satisfies TemplateV2ToolbarPatch]
          : []),
      ],
      { historyKey: `toolbar:${label}-replace` }
    );
  }

  if (loading) {
    return (
      <main className="grid min-h-screen place-items-center bg-slate-950 text-slate-200">
        {t("root.loadingStructured")}
      </main>
    );
  }

  if (error && !template) {
    return (
      <main className="grid min-h-screen place-items-center bg-slate-950 p-8 text-slate-200">
        <section className="max-w-xl rounded-xl border border-red-500/40 bg-red-950/30 p-6">
          <h1 className="text-xl font-semibold">{t("root.unavailableTitle")}</h1>
          <p className="mt-2 text-red-200">{t("loadFailed")}</p>
        </section>
      </main>
    );
  }

  if (!template || !state.layouts) return null;

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 px-6 py-4">
        <div>
          <Link
            href="/templates"
            data-testid="studio-back-to-templates"
            className="mb-1 inline-flex items-center gap-1 text-sm text-slate-400 transition hover:text-slate-200"
          >
            ← {t("root.backToTemplates")}
          </Link>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-violet-300">
            {t("root.experimental")}
          </p>
          <h1 className="mt-1 text-xl font-semibold">{template.name}</h1>
          <p className="text-sm text-slate-400">
            {t("root.revision", { revision: template.revision })}
            {state.dirty ? ` · ${t("root.unsavedChanges")}` : ""}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <UiLocaleSwitcher />
          <Link
            href={`/template-preview?templateV2Id=${encodeURIComponent(
              templateId
            )}`}
            data-testid="open-ai-layout-editor"
            className="rounded-lg border border-violet-400/60 bg-violet-500/10 px-3 py-2 text-sm font-medium text-violet-200 transition hover:bg-violet-500/20"
          >
            {t("root.aiLayout")}
          </Link>
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
            aria-label={t("root.undoAria")}
            title={t("root.undoTitle")}
            className="rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-40"
          >
            {t("root.undo")}
          </button>
          <button
            type="button"
            onClick={() => dispatch({ type: "redo" })}
            disabled={!state.future.length}
            aria-label={t("root.redoAria")}
            title={t("root.redoTitle")}
            className="rounded-lg border border-slate-700 px-3 py-2 text-sm disabled:opacity-40"
          >
            {t("root.redo")}
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
            {t("root.addRectangle")}
          </button>
          <button
            type="button"
            onClick={() => {
              if (!scene) return;
              const [element] = createTextInsertElements("equation");
              if (!element) return;
              dispatch({
                type: "add-rectangle",
                layoutId: String(scene.layout.id),
                componentId: String(scene.component.id),
                element: element as unknown as JsonRecord,
              });
              setNotice(null);
            }}
            disabled={!scene}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm disabled:opacity-40"
          >
            {t("root.addMath")}
          </button>
          <select
            aria-label={t("root.addVectorShape")}
            defaultValue=""
            disabled={!scene}
            className="rounded-lg border border-slate-600 bg-slate-950 px-3 py-2 text-sm disabled:opacity-40"
            onChange={(event) => {
              if (!scene || !event.target.value) return;
              const [element] = createElementInsertElements(event.target.value);
              event.target.value = "";
              if (!element) return;
              dispatch({
                type: "add-rectangle",
                layoutId: String(scene.layout.id),
                componentId: String(scene.component.id),
                element: element as unknown as JsonRecord,
              });
              setNotice(null);
            }}
          >
            <option value="">{t("root.addShape")}</option>
            {ELEMENT_INSERT_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void flushAutosave()}
            disabled={!state.dirty || saving || Boolean(conflict)}
            title={
              conflict
                ? t("root.reloadBeforeSaving")
                : t("root.flushAutosave")
            }
            className="rounded-lg bg-violet-500 px-4 py-2 text-sm font-semibold disabled:opacity-40"
          >
            {saving
              ? t("saving")
              : error && !conflict
                ? t("root.retrySave")
                : t("root.saveNow")}
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
          <span>{t("saveFailed")}</span>
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
          <h2 className="font-semibold">{t("root.layouts")}</h2>
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
                          {t("root.noComponents")}
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
              <h3 className="mt-6 text-sm font-semibold">{t("root.elements")}</h3>
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

        <section
          ref={canvasSurfaceRef}
          className="rounded-xl border border-slate-800 bg-slate-900 p-3"
        >
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
              cropModePath={cropModePath}
              cropDraft={cropDraft}
              onSelectionBoundsChange={setSelectionBounds}
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
              {t("root.selectLayout")}
            </div>
          )}
        </section>

        <TemplateV2SelectionToolbar
          selectionCount={state.selectionSet.length}
          selectionPath={state.selection?.elementPath ?? null}
          element={selectedElement}
          position={toolbarPosition}
          fontFamilies={[
            "Arial",
            "Calibri",
            "Inter",
            "Noto Sans",
            "Noto Sans KR",
            "Times New Roman",
          ]}
          actions={{
            locked: selectionControls.allLocked,
            canGroup: selectionControls.canGroup,
            canUngroup: selectionControls.canUngroup,
            onGroup: selectionControls.canGroup
              ? () => executeSelectionCommand("group-siblings")
              : undefined,
            onUngroup: selectionControls.canUngroup
              ? () => executeSelectionCommand("ungroup")
              : undefined,
            onLayer: selectionControls.lockConflict
              ? undefined
              : (direction) => executeSelectionCommand(direction),
            onLockChange: selectionControls.hasSelection
              ? setSelectionLocked
              : undefined,
            onAlign: selectionControls.canAlign
              ? (direction) => executeSelectionCommand(`align-${direction}`)
              : undefined,
            onDistribute: selectionControls.canDistribute
              ? (direction) =>
                  executeSelectionCommand(`distribute-${direction}`)
              : undefined,
            onDelete:
              selectionControls.hasSelection && !selectionControls.lockConflict
                ? deleteSelection
                : undefined,
            onDuplicate:
              selectionControls.hasSelection && !selectionControls.lockConflict
                ? duplicateSelection
                : undefined,
          }}
          onPatch={patchSelectedElement}
          onCropModeChange={(path) => {
            setCropModePath(path ? [...path] : null);
            if (!path) setCropDraft(null);
          }}
          onCropDraftChange={(draft: TemplateV2ImageCropDraft | null) => {
            if (!draft) {
              setCropDraft(null);
              return;
            }
            const path = cropModePath ?? state.selection?.elementPath;
            setCropDraft(
              path
                ? {
                    path: [...path],
                    focusX: draft.focusX,
                    focusY: draft.focusY,
                    cropScale: draft.cropScale,
                  }
                : null
            );
          }}
          onReplaceImage={() => replaceSelectedMedia("image")}
          onChangeIcon={() => replaceSelectedMedia("icon")}
        />

        <aside className="rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h2 className="font-semibold">{t("root.inspector")}</h2>
          {scene ? (
            <p className="mt-1 text-xs text-slate-400">
              {String(scene.layout.id)} / {String(scene.component.id)}
            </p>
          ) : null}
          <section className="mt-5 border-t border-slate-800 pt-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium">{t("root.selection")}</h3>
              <span className="text-xs text-slate-400">
                {t("root.selectedCount", { count: state.selectionSet.length })}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">
              {t("root.selectionHelp")}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              {[
                [t("root.bringToFront"), "front", selectionControls.canBringToFront],
                [t("root.bringForward"), "forward", selectionControls.canMoveForward],
                [t("root.sendBackward"), "backward", selectionControls.canMoveBackward],
                [t("root.sendToBack"), "back", selectionControls.canSendToBack],
              ].map(([label, direction, enabled]) => (
                <button
                  key={String(direction)}
                  type="button"
                  disabled={!enabled}
                  title={
                    enabled
                      ? undefined
                      : t("root.cannotMove")
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
                    : t("root.selectTwo")
                }
                onClick={() => executeSelectionCommand("group-siblings")}
                className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("root.group")}
              </button>
              <button
                type="button"
                disabled={!selectionControls.canUngroup}
                title={
                  selectionControls.canUngroup
                    ? undefined
                    : t("root.selectGroups")
                }
                onClick={() => executeSelectionCommand("ungroup")}
                className="rounded border border-slate-700 px-2 py-1.5 text-xs disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t("root.ungroup")}
              </button>
              <button
                type="button"
                disabled={!selectionControls.hasSelection}
                onClick={() => {
                  setSelectionLocked(!selectionControls.allLocked);
                }}
                className="col-span-2 rounded border border-amber-500/50 px-2 py-1.5 text-xs text-amber-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {selectionControls.allLocked
                  ? t("root.unlockSelected")
                  : t("root.lockSelected")}
              </button>
            </div>
            <div className="mt-3">
              <h4 className="text-xs font-medium text-slate-300">
                {t("root.align")}
              </h4>
              <div className="mt-2 grid grid-cols-3 gap-2">
                {(
                  [
                    [t("root.alignLeft"), "left"],
                    [t("root.alignCenter"), "center"],
                    [t("root.alignRight"), "right"],
                    [t("root.alignTop"), "top"],
                    [t("root.alignMiddle"), "middle"],
                    [t("root.alignBottom"), "bottom"],
                  ] as const
                ).map(([label, direction]) => (
                  <button
                    key={direction}
                    type="button"
                    disabled={!selectionControls.canAlign}
                    title={
                      selectionControls.canAlign
                        ? undefined
                        : t("root.selectTwo")
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
              <h4 className="text-xs font-medium text-slate-300">
                {t("root.distribute")}
              </h4>
              <div className="mt-2 grid grid-cols-2 gap-2">
                {(
                  [
                    [t("root.distributeHorizontally"), "horizontal"],
                    [t("root.distributeVertically"), "vertical"],
                  ] as const
                ).map(([label, direction]) => (
                  <button
                    key={direction}
                    type="button"
                    disabled={!selectionControls.canDistribute}
                    title={
                      selectionControls.canDistribute
                        ? undefined
                        : t("root.selectThree")
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
                {t("root.lockedNotice")}
              </p>
            ) : null}
          </section>
          <TemplateV2QualityInspector
            layouts={state.layouts}
            revision={template.revision}
            disabled={Boolean(conflict)}
            onApply={(result) => {
              if (
                result.autosave.expected_revision !== template.revision
              ) {
                setNotice(t("notices.qualityStale"));
                return;
              }
              dispatch({
                type: "apply-bounded-layouts",
                layouts: result.layouts,
                expectedDigest: templateV2VariantDigest(state.layouts),
                historyKey: result.historyKey,
              });
              setNotice(t("notices.qualityApplied"));
            }}
          />
          {activeLayout && state.activeLayoutId ? (
            <TemplateV2SlideVariantsPanel
              key={state.activeLayoutId}
              layouts={state.layouts}
              layoutId={state.activeLayoutId}
              revision={template.revision}
              disabled={Boolean(conflict)}
              onCommit={({ layouts, expectedDigest, historyKey, notice }) => {
                dispatch({
                  type: "apply-bounded-layouts",
                  layouts,
                  expectedDigest,
                  historyKey,
                });
                setNotice(notice);
              }}
            />
          ) : null}
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
              <TemplateV2ChartCopilotPanel
                element={selectedElement}
                disabled={selectionControls.lockConflict || Boolean(conflict)}
                onApply={(replacement, historyKey) => {
                  dispatch({
                    type: "apply-bounded-element",
                    selection: state.selection as StudioSelection,
                    replacement,
                    expectedElementDigest:
                      templateV2VariantDigest(selectedElement),
                    expectedRevision: template.revision,
                    currentRevision: template.revision,
                    historyKey,
                  });
                  setNotice(t("notices.chartApplied"));
                }}
              />
              <TemplateV2TableEditorPanel
                element={selectedElement}
                revision={template.revision}
                disabled={selectionControls.lockConflict || Boolean(conflict)}
                onApply={(replacement, mutation) => {
                  if (mutation.expectedRevision !== template.revision) {
                    setNotice(t("notices.tableStale"));
                    return;
                  }
                  dispatch({
                    type: "apply-bounded-element",
                    selection: state.selection as StudioSelection,
                    replacement,
                    expectedElementDigest:
                      templateV2VariantDigest(selectedElement),
                    expectedRevision: mutation.expectedRevision,
                    currentRevision: template.revision,
                    historyKey: mutation.historyKey,
                  });
                  setNotice(t("notices.tableApplied"));
                }}
              />
              <TemplateV2ImageReplacementPanel
                element={selectedElement}
                revision={template.revision}
                disabled={selectionControls.lockConflict || Boolean(conflict)}
                onApply={(intent, historyKey) => {
                  if (intent.expectedRevision !== template.revision) {
                    setNotice(t("notices.imageStale"));
                    return;
                  }
                  dispatch({
                    type: "apply-bounded-element",
                    selection: state.selection as StudioSelection,
                    replacement: applyTemplateV2ImageReplacementPatch(
                      selectedElement,
                      intent.patch
                    ),
                    expectedElementDigest:
                      templateV2VariantDigest(selectedElement),
                    expectedRevision: intent.expectedRevision,
                    currentRevision: template.revision,
                    historyKey,
                  });
                  setNotice(t("notices.imageApplied"));
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
                  setNotice(t("notices.rewriteApplied"));
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
              {t("root.unsupportedSelection")}
            </p>
          )}
          {scene ? (
            <dl className="mt-6 space-y-3 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-slate-400">{t("root.elements")}</dt>
                <dd>{scene.elements.length}</dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-400">{t("root.componentPosition")}</dt>
                <dd>
                  {elementPosition(scene.component).x} /{" "}
                  {elementPosition(scene.component).y}
                </dd>
              </div>
              <div className="flex justify-between gap-4">
                <dt className="text-slate-400">{t("root.history")}</dt>
                <dd>
                  {state.past.length} / {state.future.length}
                </dd>
              </div>
            </dl>
          ) : null}
          {!activeLayout ? (
            <p className="mt-4 text-sm text-slate-500">{t("root.noLayouts")}</p>
          ) : null}
        </aside>
      </div>
    </main>
  );
}
