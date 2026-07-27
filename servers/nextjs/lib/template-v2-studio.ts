import {
  applyTemplateV2StudioCommand,
  isTemplateV2SelectionLocked,
  normalizeTemplateV2SelectionSet,
  templateV2SelectionKey,
} from "./template-v2-studio-commands.ts";
import {
  updateTemplateV2ContentRun,
  type TemplateV2RunTarget,
} from "./template-v2-studio-content.ts";
import {
  applyTemplateV2TextSelectionPatch,
  type TemplateV2TextSelectionPatch,
} from "./template-v2-ai-rewrite.ts";
import { templateV2VariantDigest } from "./template-v2-slide-variants.ts";
import { translateTemplateV2Vector } from "./template-v2-vector.ts";

export type JsonRecord = Record<string, unknown>;
export type ElementPath = Array<string | number>;

export interface StudioSelection {
  layoutId: string;
  componentId: string;
  elementPath: ElementPath;
}

export interface TemplateV2SessionHistoryEntry {
  beforeLockedElementKeys: ReadonlySet<string>;
  afterLockedElementKeys: ReadonlySet<string>;
}

export interface TemplateV2StudioState {
  layouts: JsonRecord | null;
  activeLayoutId: string | null;
  activeComponentId: string | null;
  selection: StudioSelection | null;
  selectionSet: StudioSelection[];
  lockedElementKeys: ReadonlySet<string>;
  past: JsonRecord[];
  pastSessionHistory: TemplateV2SessionHistoryEntry[];
  future: JsonRecord[];
  futureSessionHistory: TemplateV2SessionHistoryEntry[];
  savedLayouts: JsonRecord | null;
  pendingSave: {
    token: number;
    layouts: JsonRecord;
  } | null;
  lastHistoryKey: string | null;
  dirty: boolean;
}

export interface TemplateV2Scene {
  layout: JsonRecord;
  component: JsonRecord;
  elements: JsonRecord[];
}

export type ElementGeometry = {
  x: number;
  y: number;
  width?: number;
  height?: number;
  rotation?: number;
  translateX?: number;
  translateY?: number;
};

export interface TemplateV2GeometryUpdate {
  selection: StudioSelection;
  geometry: ElementGeometry;
}

export type TemplateV2ReorderDirection =
  | "front"
  | "back"
  | "forward"
  | "backward";

export type TemplateV2AlignDirection =
  | "left"
  | "center"
  | "right"
  | "top"
  | "middle"
  | "bottom";

export type TemplateV2DistributeDirection = "horizontal" | "vertical";

export type TemplateV2StudioCommand =
  | {
      type: "update-geometry-batch";
      updates: TemplateV2GeometryUpdate[];
    }
  | {
      type: "reorder-siblings";
      direction: TemplateV2ReorderDirection;
      selections?: StudioSelection[];
    }
  | {
      type: "group-siblings";
      selections?: StudioSelection[];
      name?: string;
    }
  | {
      type: "ungroup";
      selections?: StudioSelection[];
    }
  | {
      type: "align-siblings";
      direction: TemplateV2AlignDirection;
      selections?: StudioSelection[];
    }
  | {
      type: "distribute-siblings";
      direction: TemplateV2DistributeDirection;
      selections?: StudioSelection[];
    };

export type TemplateV2StudioAction =
  | { type: "load"; layouts: JsonRecord }
  | { type: "restore-draft"; layouts: JsonRecord }
  | { type: "begin-save"; token: number; layouts: JsonRecord }
  | { type: "save-succeeded"; token: number; layouts: JsonRecord }
  | { type: "save-failed"; token: number }
  | { type: "set-active"; layoutId: string; componentId: string | null }
  | { type: "select"; selection: StudioSelection | null }
  | { type: "set-selection"; selections: StudioSelection[] }
  | {
      type: "set-element-lock";
      selection: StudioSelection;
      locked: boolean;
    }
  | { type: "execute-command"; command: TemplateV2StudioCommand }
  | TemplateV2StudioCommand
  | {
      type: "move-component";
      layoutId: string;
      componentId: string;
      x: number;
      y: number;
    }
  | {
      type: "update-element-geometry";
      selection: StudioSelection;
      geometry: ElementGeometry;
    }
  | {
      type: "edit-text-run";
      selection: StudioSelection;
      runIndex: number;
      text: string;
      historyKey?: string;
    }
  | {
      type: "edit-content-run";
      selection: StudioSelection;
      target: TemplateV2RunTarget;
      text: string;
      historyKey?: string;
    }
  | {
      type: "apply-text-selection-patch";
      selection: StudioSelection;
      patch: TemplateV2TextSelectionPatch;
      historyKey: string;
    }
  | {
      type: "apply-bounded-layouts";
      layouts: JsonRecord;
      expectedDigest: string;
      historyKey: string;
    }
  | {
      type: "apply-bounded-element";
      selection: StudioSelection;
      replacement: JsonRecord;
      expectedElementDigest: string;
      expectedRevision: number;
      currentRevision: number;
      historyKey: string;
    }
  | {
      type: "add-rectangle";
      layoutId: string;
      componentId: string;
      element: JsonRecord;
    }
  | { type: "undo" }
  | { type: "redo" };

const HISTORY_LIMIT = 40;

export const EMPTY_TEMPLATE_V2_STUDIO_STATE: TemplateV2StudioState = {
  layouts: null,
  activeLayoutId: null,
  activeComponentId: null,
  selection: null,
  selectionSet: [],
  lockedElementKeys: new Set<string>(),
  past: [],
  pastSessionHistory: [],
  future: [],
  futureSessionHistory: [],
  savedLayouts: null,
  pendingSave: null,
  lastHistoryKey: null,
  dirty: false,
};

export interface TemplateV2SaveGate {
  tryAcquire(): boolean;
  release(): void;
}

export interface TemplateV2StudioCommandFacade {
  setSelection(selections: StudioSelection[]): void;
  setElementLock(selection: StudioSelection, locked: boolean): void;
  updateGeometryBatch(updates: TemplateV2GeometryUpdate[]): void;
  reorderSiblings(
    direction: TemplateV2ReorderDirection,
    selections?: StudioSelection[]
  ): void;
  alignSiblings(
    direction: TemplateV2AlignDirection,
    selections?: StudioSelection[]
  ): void;
  distributeSiblings(
    direction: TemplateV2DistributeDirection,
    selections?: StudioSelection[]
  ): void;
  groupSiblings(selections?: StudioSelection[], name?: string): void;
  ungroup(selections?: StudioSelection[]): void;
  undo(): void;
  redo(): void;
}

export function createTemplateV2StudioCommandFacade(
  dispatch: (action: TemplateV2StudioAction) => void
): TemplateV2StudioCommandFacade {
  const execute = (command: TemplateV2StudioCommand) =>
    dispatch({ type: "execute-command", command });
  return {
    setSelection: (selections) =>
      dispatch({ type: "set-selection", selections }),
    setElementLock: (selection, locked) =>
      dispatch({ type: "set-element-lock", selection, locked }),
    updateGeometryBatch: (updates) =>
      execute({ type: "update-geometry-batch", updates }),
    reorderSiblings: (direction, selections) =>
      execute({ type: "reorder-siblings", direction, selections }),
    alignSiblings: (direction, selections) =>
      execute({ type: "align-siblings", direction, selections }),
    distributeSiblings: (direction, selections) =>
      execute({ type: "distribute-siblings", direction, selections }),
    groupSiblings: (selections, name) =>
      execute({ type: "group-siblings", selections, name }),
    ungroup: (selections) => execute({ type: "ungroup", selections }),
    undo: () => dispatch({ type: "undo" }),
    redo: () => dispatch({ type: "redo" }),
  };
}

export {
  normalizeTemplateV2SelectionSet,
  parseTemplateV2SelectionKey,
  remapTemplateV2SelectionPath,
  templateV2SelectionKey,
} from "./template-v2-studio-commands.ts";

export function isTemplateV2ElementLocked(
  state: Pick<TemplateV2StudioState, "lockedElementKeys">,
  selection: StudioSelection
): boolean {
  return isTemplateV2SelectionLocked(state.lockedElementKeys, selection);
}

export function createTemplateV2SaveGate(): TemplateV2SaveGate {
  let saving = false;
  return {
    tryAcquire() {
      if (saving) return false;
      saving = true;
      return true;
    },
    release() {
      saving = false;
    },
  };
}

export function isTemplateV2StudioEnabled(
  value = process.env.NEXT_PUBLIC_TEMPLATE_V2_STUDIO_ENABLED
): boolean {
  return value === "true";
}

export function isJsonRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isJsonRecord) : [];
}

function recordId(value: JsonRecord): string | null {
  return typeof value.id === "string" ? value.id : null;
}

export function listTemplateV2Layouts(layouts: JsonRecord | null): JsonRecord[] {
  return layouts ? records(layouts.layouts) : [];
}

export function listTemplateV2Components(layout: JsonRecord | null): JsonRecord[] {
  return layout ? records(layout.components) : [];
}

export function findTemplateV2Layout(
  layouts: JsonRecord | null,
  layoutId: string | null
): JsonRecord | null {
  if (!layoutId) return null;
  return (
    listTemplateV2Layouts(layouts).find((layout) => recordId(layout) === layoutId) ??
    null
  );
}

export function findTemplateV2Component(
  layout: JsonRecord | null,
  componentId: string | null
): JsonRecord | null {
  if (!componentId) return null;
  return (
    listTemplateV2Components(layout).find(
      (component) => recordId(component) === componentId
    ) ?? null
  );
}

function defaultActiveIds(layouts: JsonRecord): {
  layoutId: string | null;
  componentId: string | null;
} {
  const layout = listTemplateV2Layouts(layouts)[0] ?? null;
  const component = listTemplateV2Components(layout)[0] ?? null;
  return {
    layoutId: layout ? recordId(layout) : null,
    componentId: component ? recordId(component) : null,
  };
}

export function getTemplateV2Scene(
  layouts: JsonRecord | null,
  layoutId: string | null,
  componentId: string | null
): TemplateV2Scene | null {
  const layout = findTemplateV2Layout(layouts, layoutId);
  const component = findTemplateV2Component(layout, componentId);
  if (!layout || !component) return null;
  return {
    layout,
    component,
    elements: records(component.elements),
  };
}

export function getSelectedElement(
  layouts: JsonRecord | null,
  selection: StudioSelection | null
): JsonRecord | null {
  if (!selection) return null;
  const scene = getTemplateV2Scene(
    layouts,
    selection.layoutId,
    selection.componentId
  );
  if (!scene) return null;
  let current: unknown = scene.elements;
  for (const segment of selection.elementPath) {
    if (typeof segment === "number" && Array.isArray(current)) {
      current = current[segment];
    } else if (typeof segment === "string" && isJsonRecord(current)) {
      current = current[segment];
    } else {
      return null;
    }
  }
  return isJsonRecord(current) ? current : null;
}

function replaceAtPath(
  value: unknown,
  path: ElementPath,
  update: (element: JsonRecord) => JsonRecord
): unknown {
  if (path.length === 0) return isJsonRecord(value) ? update(value) : value;
  const [head, ...tail] = path;
  if (typeof head === "number" && Array.isArray(value)) {
    if (head < 0 || head >= value.length) return value;
    const child = replaceAtPath(value[head], tail, update);
    if (child === value[head]) return value;
    const next = value.slice();
    next[head] = child;
    return next;
  }
  if (typeof head === "string" && isJsonRecord(value) && head in value) {
    const child = replaceAtPath(value[head], tail, update);
    if (child === value[head]) return value;
    return { ...value, [head]: child };
  }
  return value;
}

function updateComponent(
  layouts: JsonRecord,
  layoutId: string,
  componentId: string,
  update: (component: JsonRecord) => JsonRecord
): JsonRecord {
  const layoutItems = Array.isArray(layouts.layouts) ? layouts.layouts : [];
  const layoutIndex = layoutItems.findIndex(
    (layout) => isJsonRecord(layout) && recordId(layout) === layoutId
  );
  if (layoutIndex < 0 || !isJsonRecord(layoutItems[layoutIndex])) return layouts;
  const layout = layoutItems[layoutIndex] as JsonRecord;
  const components = Array.isArray(layout.components) ? layout.components : [];
  const componentIndex = components.findIndex(
    (component) => isJsonRecord(component) && recordId(component) === componentId
  );
  if (componentIndex < 0 || !isJsonRecord(components[componentIndex])) {
    return layouts;
  }
  const component = components[componentIndex] as JsonRecord;
  const nextComponent = update(component);
  if (nextComponent === component) return layouts;
  const nextComponents = components.slice();
  nextComponents[componentIndex] = nextComponent;
  const nextLayouts = layoutItems.slice();
  nextLayouts[layoutIndex] = { ...layout, components: nextComponents };
  return { ...layouts, layouts: nextLayouts };
}

export function updateTemplateV2Element(
  layouts: JsonRecord,
  selection: StudioSelection,
  update: (element: JsonRecord) => JsonRecord
): JsonRecord {
  return updateComponent(
    layouts,
    selection.layoutId,
    selection.componentId,
    (component) => {
      const nextElements = replaceAtPath(
        Array.isArray(component.elements) ? component.elements : [],
        selection.elementPath,
        update
      );
      return nextElements === component.elements
        ? component
        : { ...component, elements: nextElements };
    }
  );
}

function withGeometry(element: JsonRecord, geometry: ElementGeometry): JsonRecord {
  if (element.type === "vector") {
    return geometry.translateX !== undefined &&
      geometry.translateY !== undefined
      ? translateTemplateV2Vector(
          element,
          geometry.translateX,
          geometry.translateY
        )
      : element;
  }
  const currentPosition = isJsonRecord(element.position)
    ? element.position
    : {};
  let next: JsonRecord =
    currentPosition.x !== geometry.x || currentPosition.y !== geometry.y
    ? {
        ...element,
        position: {
          ...currentPosition,
          x: geometry.x,
          y: geometry.y,
        },
      }
    : element;
  if (geometry.width !== undefined && geometry.height !== undefined) {
    const currentSize = isJsonRecord(element.size) ? element.size : {};
    if (
      currentSize.width !== geometry.width ||
      currentSize.height !== geometry.height
    ) {
      next = {
        ...next,
        size: {
          ...currentSize,
          width: geometry.width,
          height: geometry.height,
        },
      };
    }
  }
  if (
    geometry.rotation !== undefined &&
    element.type !== "group" &&
    element.rotation !== geometry.rotation
  ) {
    next = { ...next, rotation: geometry.rotation };
  }
  return next;
}

function commit(
  state: TemplateV2StudioState,
  layouts: JsonRecord,
  historyKey?: string,
  nextLockedElementKeys = state.lockedElementKeys
): TemplateV2StudioState {
  if (!state.layouts || layouts === state.layouts) return state;
  const coalesce = Boolean(historyKey && historyKey === state.lastHistoryKey);
  const past = coalesce
    ? state.past
    : [...state.past, state.layouts].slice(-HISTORY_LIMIT);
  const sessionEntry: TemplateV2SessionHistoryEntry = {
    beforeLockedElementKeys: new Set(state.lockedElementKeys),
    afterLockedElementKeys: new Set(nextLockedElementKeys),
  };
  const pastSessionHistory = coalesce
    ? [
        ...state.pastSessionHistory.slice(0, -1),
        {
          beforeLockedElementKeys:
            state.pastSessionHistory.at(-1)?.beforeLockedElementKeys ??
            sessionEntry.beforeLockedElementKeys,
          afterLockedElementKeys: sessionEntry.afterLockedElementKeys,
        },
      ]
    : [...state.pastSessionHistory, sessionEntry].slice(-HISTORY_LIMIT);
  return {
    ...state,
    layouts,
    past,
    pastSessionHistory,
    future: [],
    futureSessionHistory: [],
    lastHistoryKey: historyKey ?? null,
    dirty: layouts !== state.savedLayouts,
  };
}

function commitCommand(
  state: TemplateV2StudioState,
  command: TemplateV2StudioCommand
): TemplateV2StudioState {
  if (!state.layouts) return state;
  const result = applyTemplateV2StudioCommand(
    state.layouts,
    state.selectionSet,
    state.lockedElementKeys,
    command
  );
  if (result.layouts === state.layouts) return state;
  const committed = commit(
    state,
    result.layouts,
    undefined,
    result.lockedElementKeys
  );
  return {
    ...committed,
    selection: result.selectionSet[0] ?? null,
    selectionSet: result.selectionSet,
    lockedElementKeys: result.lockedElementKeys,
    lastHistoryKey: null,
  };
}

function reconcileTemplateV2SessionLocks(
  targetBase: ReadonlySet<string>,
  sourceBase: ReadonlySet<string>,
  current: ReadonlySet<string>
): Set<string> {
  const targetKeys = [...targetBase];
  const sourceKeys = [...sourceBase];
  const reconciled = new Set(targetKeys);
  for (let index = 0; index < sourceKeys.length; index += 1) {
    if (!current.has(sourceKeys[index]) && targetKeys[index]) {
      reconciled.delete(targetKeys[index]);
    }
  }
  for (const key of current) {
    if (!sourceBase.has(key)) reconciled.add(key);
  }
  return reconciled;
}

export function templateV2StudioReducer(
  state: TemplateV2StudioState,
  action: TemplateV2StudioAction
): TemplateV2StudioState {
  switch (action.type) {
    case "load": {
      const active = defaultActiveIds(action.layouts);
      return {
        ...EMPTY_TEMPLATE_V2_STUDIO_STATE,
        layouts: action.layouts,
        savedLayouts: action.layouts,
        activeLayoutId: active.layoutId,
        activeComponentId: active.componentId,
      };
    }
    case "restore-draft": {
      if (!state.savedLayouts) return state;
      const active = defaultActiveIds(action.layouts);
      return {
        ...state,
        layouts: action.layouts,
        activeLayoutId: active.layoutId,
        activeComponentId: active.componentId,
        selection: null,
        selectionSet: [],
        lockedElementKeys: new Set<string>(),
        past: state.layouts ? [state.layouts] : [],
        pastSessionHistory: [],
        future: [],
        futureSessionHistory: [],
        pendingSave: null,
        lastHistoryKey: null,
        dirty: action.layouts !== state.savedLayouts,
      };
    }
    case "begin-save":
      if (
        !state.layouts ||
        action.layouts !== state.layouts ||
        state.pendingSave
      ) {
        return state;
      }
      return {
        ...state,
        pendingSave: {
          token: action.token,
          layouts: action.layouts,
        },
        lastHistoryKey: null,
      };
    case "save-succeeded": {
      if (!state.layouts || state.pendingSave?.token !== action.token) {
        return state;
      }
      const changedSinceRequest =
        state.layouts !== state.pendingSave.layouts;
      const layouts = changedSinceRequest ? state.layouts : action.layouts;
      const savedLayouts = changedSinceRequest
        ? state.pendingSave.layouts
        : action.layouts;
      return {
        ...state,
        layouts,
        savedLayouts,
        pendingSave: null,
        lastHistoryKey: null,
        dirty: layouts !== savedLayouts,
      };
    }
    case "save-failed":
      if (state.pendingSave?.token !== action.token) return state;
      return {
        ...state,
        pendingSave: null,
        lastHistoryKey: null,
      };
    case "set-active":
      return {
        ...state,
        activeLayoutId: action.layoutId,
        activeComponentId: action.componentId,
        selection: null,
        selectionSet: [],
        lastHistoryKey: null,
      };
    case "select":
      if (
        action.selection &&
        isTemplateV2SelectionLocked(
          state.lockedElementKeys,
          action.selection
        )
      ) {
        return state;
      }
      return {
        ...state,
        selection: action.selection,
        selectionSet: action.selection ? [action.selection] : [],
        lastHistoryKey: null,
      };
    case "set-selection": {
      const selectionSet = normalizeTemplateV2SelectionSet(action.selections);
      if (
        selectionSet.some((selection) =>
          isTemplateV2SelectionLocked(
            state.lockedElementKeys,
            selection
          )
        )
      ) {
        return state;
      }
      return {
        ...state,
        selection: selectionSet[0] ?? null,
        selectionSet,
        lastHistoryKey: null,
      };
    }
    case "set-element-lock": {
      if (!getSelectedElement(state.layouts, action.selection)) return state;
      const key = templateV2SelectionKey(action.selection);
      if (state.lockedElementKeys.has(key) === action.locked) return state;
      const lockedElementKeys = new Set(state.lockedElementKeys);
      if (action.locked) lockedElementKeys.add(key);
      else lockedElementKeys.delete(key);
      return { ...state, lockedElementKeys, lastHistoryKey: null };
    }
    case "execute-command":
      return commitCommand(state, action.command);
    case "update-geometry-batch":
    case "reorder-siblings":
    case "group-siblings":
    case "ungroup":
    case "align-siblings":
    case "distribute-siblings":
      return commitCommand(state, action);
    case "move-component":
      if (!state.layouts) return state;
      return commit(
        state,
        updateComponent(
          state.layouts,
          action.layoutId,
          action.componentId,
          (component) => {
            const position = isJsonRecord(component.position)
              ? component.position
              : {};
            if (position.x === action.x && position.y === action.y) {
              return component;
            }
            return {
              ...component,
              position: { ...position, x: action.x, y: action.y },
            };
          }
        )
      );
    case "update-element-geometry":
      if (!state.layouts) return state;
      if (
        isTemplateV2SelectionLocked(
          state.lockedElementKeys,
          action.selection,
          true
        )
      ) {
        return state;
      }
      return commit(
        state,
        updateTemplateV2Element(state.layouts, action.selection, (element) =>
          withGeometry(element, action.geometry)
        )
      );
    case "edit-text-run":
    case "edit-content-run":
      if (!state.layouts) return state;
      if (
        isTemplateV2SelectionLocked(
          state.lockedElementKeys,
          action.selection
        )
      ) {
        return state;
      }
      return commit(
        state,
        updateTemplateV2Element(state.layouts, action.selection, (element) =>
          updateTemplateV2ContentRun(
            element,
            action.type === "edit-text-run"
              ? { kind: "text", runIndex: action.runIndex }
              : action.target,
            action.text
          )
        ),
        action.historyKey
      );
    case "apply-text-selection-patch":
      if (!state.layouts) return state;
      if (
        isTemplateV2SelectionLocked(
          state.lockedElementKeys,
          action.selection
        )
      ) {
        return state;
      }
      return commit(
        state,
        updateTemplateV2Element(state.layouts, action.selection, (element) => {
          const result = applyTemplateV2TextSelectionPatch(
            element,
            action.patch
          );
          return result.ok ? result.element : element;
        }),
        action.historyKey
      );
    case "apply-bounded-layouts":
      if (
        !state.layouts ||
        templateV2VariantDigest(state.layouts) !== action.expectedDigest
      ) {
        return state;
      }
      return commit(state, action.layouts, action.historyKey);
    case "apply-bounded-element": {
      if (
        !state.layouts ||
        action.expectedRevision !== action.currentRevision ||
        isTemplateV2SelectionLocked(
          state.lockedElementKeys,
          action.selection
        )
      ) {
        return state;
      }
      const current = getSelectedElement(state.layouts, action.selection);
      if (
        !current ||
        templateV2VariantDigest(current) !== action.expectedElementDigest ||
        (typeof current.id === "string" &&
          action.replacement.id !== current.id)
      ) {
        return state;
      }
      return commit(
        state,
        updateTemplateV2Element(
          state.layouts,
          action.selection,
          () => action.replacement
        ),
        action.historyKey
      );
    }
    case "add-rectangle":
      if (!state.layouts) return state;
      {
        const selection: StudioSelection = {
          layoutId: action.layoutId,
          componentId: action.componentId,
          elementPath: [
            getTemplateV2Scene(
              state.layouts,
              action.layoutId,
              action.componentId
            )?.elements.length ?? 0,
          ],
        };
        return commit(
          {
            ...state,
            selection,
            selectionSet: [selection],
          },
          updateComponent(
            state.layouts,
            action.layoutId,
            action.componentId,
            (component) => ({
              ...component,
              elements: [
                ...(Array.isArray(component.elements) ? component.elements : []),
                action.element,
              ],
            })
          )
        );
      }
    case "undo": {
      if (!state.layouts || state.past.length === 0) return state;
      const layouts = state.past[state.past.length - 1];
      const sessionEntry = state.pastSessionHistory.at(-1);
      const lockedElementKeys = sessionEntry
        ? reconcileTemplateV2SessionLocks(
            sessionEntry.beforeLockedElementKeys,
            sessionEntry.afterLockedElementKeys,
            state.lockedElementKeys
          )
        : new Set(state.lockedElementKeys);
      return {
        ...state,
        layouts,
        past: state.past.slice(0, -1),
        pastSessionHistory: state.pastSessionHistory.slice(0, -1),
        future: [state.layouts, ...state.future].slice(0, HISTORY_LIMIT),
        futureSessionHistory: [
          ...(sessionEntry ? [sessionEntry] : []),
          ...state.futureSessionHistory,
        ].slice(0, HISTORY_LIMIT),
        selection: null,
        selectionSet: [],
        lockedElementKeys,
        lastHistoryKey: null,
        dirty: layouts !== state.savedLayouts,
      };
    }
    case "redo": {
      if (!state.layouts || state.future.length === 0) return state;
      const [layouts, ...future] = state.future;
      const [sessionEntry, ...futureSessionHistory] =
        state.futureSessionHistory;
      const lockedElementKeys = sessionEntry
        ? reconcileTemplateV2SessionLocks(
            sessionEntry.afterLockedElementKeys,
            sessionEntry.beforeLockedElementKeys,
            state.lockedElementKeys
          )
        : new Set(state.lockedElementKeys);
      return {
        ...state,
        layouts,
        past: [...state.past, state.layouts].slice(-HISTORY_LIMIT),
        pastSessionHistory: [
          ...state.pastSessionHistory,
          ...(sessionEntry ? [sessionEntry] : []),
        ].slice(-HISTORY_LIMIT),
        future,
        futureSessionHistory,
        selection: null,
        selectionSet: [],
        lockedElementKeys,
        lastHistoryKey: null,
        dirty: layouts !== state.savedLayouts,
      };
    }
    default:
      return state;
  }
}

export function createTemplateV2Rectangle(): JsonRecord {
  return {
    type: "container",
    position: { x: 80, y: 80 },
    size: { width: 240, height: 120 },
    rotation: 0,
    fill: { color: "#dbeafe", opacity: 1 },
    stroke: { color: "#2563eb", opacity: 1, width: 2 },
    border_radius: { tl: 12, tr: 12, bl: 12, br: 12 },
    child: null,
  };
}

export function textContent(element: JsonRecord): string {
  if (element.type !== "text" || !Array.isArray(element.runs)) return "";
  return element.runs
    .map((run) =>
      isJsonRecord(run) && typeof run.text === "string" ? run.text : ""
    )
    .join("");
}
