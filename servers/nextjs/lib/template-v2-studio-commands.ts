import type {
  ElementPath,
  JsonRecord,
  StudioSelection,
  TemplateV2AlignDirection,
  TemplateV2DistributeDirection,
  TemplateV2GeometryUpdate,
  TemplateV2ReorderDirection,
  TemplateV2StudioCommand,
} from "./template-v2-studio.ts";
import { translateTemplateV2Vector } from "./template-v2-vector.ts";

export interface TemplateV2CommandResult {
  layouts: JsonRecord;
  selectionSet: StudioSelection[];
  lockedElementKeys: ReadonlySet<string>;
}

interface SiblingContext {
  layoutIndex: number;
  componentIndex: number;
  parentPath: ElementPath;
  siblings: unknown[];
  selections: StudioSelection[];
  indices: number[];
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordId(value: JsonRecord): string | null {
  return typeof value.id === "string" ? value.id : null;
}

function pathEquals(left: ElementPath, right: ElementPath): boolean {
  return (
    left.length === right.length &&
    left.every((segment, index) => segment === right[index])
  );
}

function pathStartsWith(path: ElementPath, prefix: ElementPath): boolean {
  return (
    path.length >= prefix.length &&
    prefix.every((segment, index) => segment === path[index])
  );
}

export function templateV2SelectionKey(selection: StudioSelection): string {
  return JSON.stringify([
    selection.layoutId,
    selection.componentId,
    selection.elementPath,
  ]);
}

export function parseTemplateV2SelectionKey(
  key: string
): StudioSelection | null {
  try {
    const parsed: unknown = JSON.parse(key);
    if (
      !Array.isArray(parsed) ||
      parsed.length !== 3 ||
      typeof parsed[0] !== "string" ||
      typeof parsed[1] !== "string" ||
      !Array.isArray(parsed[2]) ||
      !parsed[2].every(
        (segment) =>
          typeof segment === "string" ||
          (typeof segment === "number" && Number.isInteger(segment))
      )
    ) {
      return null;
    }
    return {
      layoutId: parsed[0],
      componentId: parsed[1],
      elementPath: parsed[2],
    };
  } catch {
    return null;
  }
}

export function normalizeTemplateV2SelectionSet(
  selections: StudioSelection[]
): StudioSelection[] {
  if (selections.length === 0) return [];
  const unique = new Map<string, StudioSelection>();
  for (const selection of selections) {
    unique.set(templateV2SelectionKey(selection), selection);
  }
  const normalized = [...unique.values()];
  const first = normalized[0];
  const firstParent = first.elementPath.slice(0, -1);
  const firstIndex = first.elementPath.at(-1);
  if (typeof firstIndex !== "number" || !Number.isInteger(firstIndex)) return [];
  if (
    normalized.some((selection) => {
      const index = selection.elementPath.at(-1);
      return (
        selection.layoutId !== first.layoutId ||
        selection.componentId !== first.componentId ||
        typeof index !== "number" ||
        !Number.isInteger(index) ||
        !pathEquals(selection.elementPath.slice(0, -1), firstParent)
      );
    })
  ) {
    return [];
  }
  return normalized.sort(
    (left, right) =>
      (left.elementPath.at(-1) as number) -
      (right.elementPath.at(-1) as number)
  );
}

export function remapTemplateV2SelectionPath(
  selection: StudioSelection,
  layoutId: string,
  componentId: string,
  parentPath: ElementPath,
  remap: (index: number, tail: ElementPath) => ElementPath | null
): StudioSelection | null {
  if (
    selection.layoutId !== layoutId ||
    selection.componentId !== componentId ||
    !pathStartsWith(selection.elementPath, parentPath)
  ) {
    return selection;
  }
  const index = selection.elementPath[parentPath.length];
  if (typeof index !== "number") return selection;
  const nextPath = remap(index, selection.elementPath.slice(parentPath.length + 1));
  return nextPath
    ? { ...selection, elementPath: [...parentPath, ...nextPath] }
    : null;
}

function valueAtPath(value: unknown, path: ElementPath): unknown {
  let current = value;
  for (const segment of path) {
    if (typeof segment === "number" && Array.isArray(current)) {
      current = current[segment];
    } else if (
      typeof segment === "string" &&
      isRecord(current) &&
      segment in current
    ) {
      current = current[segment];
    } else {
      return undefined;
    }
  }
  return current;
}

function replaceAtPath(
  value: unknown,
  path: ElementPath,
  replacement: unknown
): unknown {
  if (path.length === 0) return replacement;
  const [head, ...tail] = path;
  if (typeof head === "number" && Array.isArray(value)) {
    if (head < 0 || head >= value.length) return value;
    const child = replaceAtPath(value[head], tail, replacement);
    if (child === value[head]) return value;
    const next = value.slice();
    next[head] = child;
    return next;
  }
  if (typeof head === "string" && isRecord(value) && head in value) {
    const child = replaceAtPath(value[head], tail, replacement);
    if (child === value[head]) return value;
    return { ...value, [head]: child };
  }
  return value;
}

function componentContext(
  layouts: JsonRecord,
  layoutId: string,
  componentId: string
): {
  layoutItems: unknown[];
  layoutIndex: number;
  layout: JsonRecord;
  components: unknown[];
  componentIndex: number;
  component: JsonRecord;
} | null {
  const layoutItems = Array.isArray(layouts.layouts) ? layouts.layouts : [];
  const layoutIndex = layoutItems.findIndex(
    (layout) => isRecord(layout) && recordId(layout) === layoutId
  );
  const layout = layoutItems[layoutIndex];
  if (!isRecord(layout)) return null;
  const components = Array.isArray(layout.components) ? layout.components : [];
  const componentIndex = components.findIndex(
    (component) => isRecord(component) && recordId(component) === componentId
  );
  const component = components[componentIndex];
  if (!isRecord(component)) return null;
  return {
    layoutItems,
    layoutIndex,
    layout,
    components,
    componentIndex,
    component,
  };
}

function siblingContext(
  layouts: JsonRecord,
  selections: StudioSelection[]
): SiblingContext | null {
  const normalized = normalizeTemplateV2SelectionSet(selections);
  if (normalized.length === 0) return null;
  const first = normalized[0];
  const context = componentContext(
    layouts,
    first.layoutId,
    first.componentId
  );
  if (!context) return null;
  const parentPath = first.elementPath.slice(0, -1);
  const elements = Array.isArray(context.component.elements)
    ? context.component.elements
    : [];
  const siblings = valueAtPath(elements, parentPath);
  if (!Array.isArray(siblings)) return null;
  const indices = normalized.map(
    (selection) => selection.elementPath.at(-1) as number
  );
  if (
    indices.some(
      (index) => index < 0 || index >= siblings.length || !isRecord(siblings[index])
    )
  ) {
    return null;
  }
  return {
    layoutIndex: context.layoutIndex,
    componentIndex: context.componentIndex,
    parentPath,
    siblings,
    selections: normalized,
    indices,
  };
}

function replaceSiblings(
  layouts: JsonRecord,
  context: SiblingContext,
  siblings: unknown[]
): JsonRecord {
  const layoutItems = (layouts.layouts as unknown[]).slice();
  const layout = layoutItems[context.layoutIndex] as JsonRecord;
  const components = (layout.components as unknown[]).slice();
  const component = components[context.componentIndex] as JsonRecord;
  const elements = Array.isArray(component.elements) ? component.elements : [];
  const nextElements = replaceAtPath(elements, context.parentPath, siblings);
  components[context.componentIndex] = { ...component, elements: nextElements };
  layoutItems[context.layoutIndex] = { ...layout, components };
  return { ...layouts, layouts: layoutItems };
}

function numberField(value: unknown, key: string, fallback = 0): number {
  return isRecord(value) &&
    typeof value[key] === "number" &&
    Number.isFinite(value[key])
    ? value[key]
    : fallback;
}

function elementBounds(element: JsonRecord) {
  const x = numberField(element.position, "x");
  const y = numberField(element.position, "y");
  const width = Math.max(0, numberField(element.size, "width"));
  const height = Math.max(0, numberField(element.size, "height"));
  const rotation =
    element.type === "group" ? 0 : numberField(element, "rotation");
  const radians = (rotation * Math.PI) / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  const points = [
    [0, 0],
    [width, 0],
    [0, height],
    [width, height],
  ].map(([dx, dy]) => ({
    x: x + dx * cosine - dy * sine,
    y: y + dx * sine + dy * cosine,
  }));
  return {
    left: Math.min(...points.map((point) => point.x)),
    top: Math.min(...points.map((point) => point.y)),
    right: Math.max(...points.map((point) => point.x)),
    bottom: Math.max(...points.map((point) => point.y)),
  };
}

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function positionRecord(element: JsonRecord): JsonRecord {
  return isRecord(element.position) ? element.position : {};
}

function sizeRecord(element: JsonRecord): JsonRecord {
  return isRecord(element.size) ? element.size : {};
}

function translateElement(
  element: JsonRecord,
  deltaX: number,
  deltaY: number
): JsonRecord {
  if (Math.abs(deltaX) < 0.000001 && Math.abs(deltaY) < 0.000001) {
    return element;
  }
  const position = positionRecord(element);
  return {
    ...element,
    position: {
      ...position,
      x: round(numberField(position, "x") + deltaX),
      y: round(numberField(position, "y") + deltaY),
    },
  };
}

function moveElementToLocalCoordinates(
  element: JsonRecord,
  x: number,
  y: number
): JsonRecord {
  const position = positionRecord(element);
  return {
    ...element,
    position: {
      ...position,
      x: round(numberField(position, "x") - x),
      y: round(numberField(position, "y") - y),
    },
  };
}

function moveElementToParentCoordinates(
  element: JsonRecord,
  x: number,
  y: number
): JsonRecord {
  const position = positionRecord(element);
  return {
    ...element,
    position: {
      ...position,
      x: round(numberField(position, "x") + x),
      y: round(numberField(position, "y") + y),
    },
  };
}

function remapSession(
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  remap: (selection: StudioSelection) => StudioSelection | null
): Pick<TemplateV2CommandResult, "selectionSet" | "lockedElementKeys"> {
  const nextSelections = selectionSet
    .map(remap)
    .filter((selection): selection is StudioSelection => selection !== null);
  const nextLocked = new Set<string>();
  for (const key of lockedElementKeys) {
    const selection = parseTemplateV2SelectionKey(key);
    const remapped = selection ? remap(selection) : null;
    if (remapped) nextLocked.add(templateV2SelectionKey(remapped));
  }
  return { selectionSet: nextSelections, lockedElementKeys: nextLocked };
}

export function isTemplateV2SelectionLocked(
  lockedElementKeys: ReadonlySet<string>,
  selection: StudioSelection,
  includeDescendants = false
): boolean {
  const locked = [...lockedElementKeys]
    .map(parseTemplateV2SelectionKey)
    .filter((selection): selection is StudioSelection => selection !== null);
  return locked.some(
    (candidate) =>
      candidate.layoutId === selection.layoutId &&
      candidate.componentId === selection.componentId &&
      (pathStartsWith(selection.elementPath, candidate.elementPath) ||
        (includeDescendants &&
          pathStartsWith(candidate.elementPath, selection.elementPath)))
  );
}

function containsLockedPath(
  lockedElementKeys: ReadonlySet<string>,
  selections: StudioSelection[],
  includeDescendants = false
): boolean {
  return selections.some((selection) =>
    isTemplateV2SelectionLocked(
      lockedElementKeys,
      selection,
      includeDescendants
    )
  );
}

function unchanged(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>
): TemplateV2CommandResult {
  return { layouts, selectionSet, lockedElementKeys };
}

function applyGeometryBatch(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  updates: TemplateV2GeometryUpdate[]
): TemplateV2CommandResult {
  const selections = normalizeTemplateV2SelectionSet(
    updates.map((update) => update.selection)
  );
  if (
    selections.length !== updates.length ||
    containsLockedPath(lockedElementKeys, selections, true)
  ) {
    return unchanged(layouts, selectionSet, lockedElementKeys);
  }
  const context = siblingContext(layouts, selections);
  if (!context) return unchanged(layouts, selectionSet, lockedElementKeys);
  const geometryByKey = new Map(
    updates.map((update) => [
      templateV2SelectionKey(update.selection),
      update.geometry,
    ])
  );
  const siblings = context.siblings.slice();
  let changed = false;
  for (const selection of selections) {
    const index = selection.elementPath.at(-1) as number;
    const element = siblings[index] as JsonRecord;
    const geometry = geometryByKey.get(templateV2SelectionKey(selection));
    if (
      !geometry ||
      !Number.isFinite(geometry.x) ||
      !Number.isFinite(geometry.y) ||
      (geometry.width === undefined) !== (geometry.height === undefined) ||
      (geometry.width !== undefined &&
        (!Number.isFinite(geometry.width) || geometry.width < 0)) ||
      (geometry.height !== undefined &&
        (!Number.isFinite(geometry.height) || geometry.height < 0)) ||
      (geometry.rotation !== undefined &&
        !Number.isFinite(geometry.rotation)) ||
      (geometry.translateX === undefined) !==
        (geometry.translateY === undefined) ||
      (geometry.translateX !== undefined &&
        !Number.isFinite(geometry.translateX)) ||
      (geometry.translateY !== undefined &&
        !Number.isFinite(geometry.translateY)) ||
      (element.type === "vector" && geometry.translateX === undefined)
    ) {
      return unchanged(layouts, selectionSet, lockedElementKeys);
    }
    const position = positionRecord(element);
    let next: JsonRecord =
      element.type === "vector" &&
      geometry.translateX !== undefined &&
      geometry.translateY !== undefined
        ? translateTemplateV2Vector(
            element,
            geometry.translateX,
            geometry.translateY
          )
        : position.x === geometry.x && position.y === geometry.y
        ? element
        : {
            ...element,
            position: {
              ...position,
              x: geometry.x,
              y: geometry.y,
            },
          };
    if (geometry.width !== undefined && geometry.height !== undefined) {
      const size = sizeRecord(element);
      if (
        size.width !== geometry.width ||
        size.height !== geometry.height
      ) {
        next = {
          ...next,
          size: {
            ...size,
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
    siblings[index] = next;
    changed ||= next !== element;
  }
  if (!changed) return unchanged(layouts, selectionSet, lockedElementKeys);
  return {
    layouts: replaceSiblings(layouts, context, siblings),
    selectionSet: selections,
    lockedElementKeys,
  };
}

function applyAlign(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  direction: TemplateV2AlignDirection,
  requestedSelections?: StudioSelection[]
): TemplateV2CommandResult {
  const context = siblingContext(
    layouts,
    requestedSelections ?? selectionSet
  );
  if (
    !context ||
    context.indices.length < 2 ||
    containsLockedPath(lockedElementKeys, context.selections, true)
  ) {
    return unchanged(layouts, selectionSet, lockedElementKeys);
  }
  const entries = context.indices.map((index) => {
    const element = context.siblings[index] as JsonRecord;
    return { index, element, bounds: elementBounds(element) };
  });
  const left = Math.min(...entries.map((entry) => entry.bounds.left));
  const top = Math.min(...entries.map((entry) => entry.bounds.top));
  const right = Math.max(...entries.map((entry) => entry.bounds.right));
  const bottom = Math.max(...entries.map((entry) => entry.bounds.bottom));
  const siblings = context.siblings.slice();
  let changed = false;
  for (const entry of entries) {
    let deltaX = 0;
    let deltaY = 0;
    switch (direction) {
      case "left":
        deltaX = left - entry.bounds.left;
        break;
      case "center":
        deltaX =
          (left + right) / 2 -
          (entry.bounds.left + entry.bounds.right) / 2;
        break;
      case "right":
        deltaX = right - entry.bounds.right;
        break;
      case "top":
        deltaY = top - entry.bounds.top;
        break;
      case "middle":
        deltaY =
          (top + bottom) / 2 -
          (entry.bounds.top + entry.bounds.bottom) / 2;
        break;
      case "bottom":
        deltaY = bottom - entry.bounds.bottom;
        break;
    }
    const next = translateElement(entry.element, deltaX, deltaY);
    siblings[entry.index] = next;
    changed ||= next !== entry.element;
  }
  if (!changed) return unchanged(layouts, selectionSet, lockedElementKeys);
  return {
    layouts: replaceSiblings(layouts, context, siblings),
    selectionSet: context.selections,
    lockedElementKeys,
  };
}

function applyDistribute(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  direction: TemplateV2DistributeDirection,
  requestedSelections?: StudioSelection[]
): TemplateV2CommandResult {
  const context = siblingContext(
    layouts,
    requestedSelections ?? selectionSet
  );
  if (
    !context ||
    context.indices.length < 3 ||
    containsLockedPath(lockedElementKeys, context.selections, true)
  ) {
    return unchanged(layouts, selectionSet, lockedElementKeys);
  }
  const horizontal = direction === "horizontal";
  const entries = context.indices
    .map((index) => {
      const element = context.siblings[index] as JsonRecord;
      return { index, element, bounds: elementBounds(element) };
    })
    .sort((left, right) => {
      const leftStart = horizontal ? left.bounds.left : left.bounds.top;
      const rightStart = horizontal ? right.bounds.left : right.bounds.top;
      return leftStart - rightStart || left.index - right.index;
    });
  const start = horizontal ? entries[0].bounds.left : entries[0].bounds.top;
  const end = Math.max(
    ...entries.map((entry) =>
      horizontal ? entry.bounds.right : entry.bounds.bottom
    )
  );
  const occupied = entries.reduce(
    (total, entry) =>
      total +
      (horizontal
        ? entry.bounds.right - entry.bounds.left
        : entry.bounds.bottom - entry.bounds.top),
    0
  );
  const gap = (end - start - occupied) / (entries.length - 1);
  const siblings = context.siblings.slice();
  let cursor = start;
  let changed = false;
  entries.forEach((entry) => {
    const currentStart = horizontal ? entry.bounds.left : entry.bounds.top;
    const targetStart = cursor;
    const delta = targetStart - currentStart;
    const next = translateElement(
      entry.element,
      horizontal ? delta : 0,
      horizontal ? 0 : delta
    );
    siblings[entry.index] = next;
    changed ||= next !== entry.element;
    cursor =
      targetStart +
      (horizontal
        ? entry.bounds.right - entry.bounds.left
        : entry.bounds.bottom - entry.bounds.top) +
      gap;
  });
  if (!changed) return unchanged(layouts, selectionSet, lockedElementKeys);
  return {
    layouts: replaceSiblings(layouts, context, siblings),
    selectionSet: context.selections,
    lockedElementKeys,
  };
}

function reorderedEntries(
  length: number,
  selected: Set<number>,
  direction: TemplateV2ReorderDirection
): number[] {
  const indices = Array.from({ length }, (_, index) => index);
  if (direction === "front") {
    return [
      ...indices.filter((index) => !selected.has(index)),
      ...indices.filter((index) => selected.has(index)),
    ];
  }
  if (direction === "back") {
    return [
      ...indices.filter((index) => selected.has(index)),
      ...indices.filter((index) => !selected.has(index)),
    ];
  }
  if (direction === "forward") {
    for (let index = indices.length - 2; index >= 0; index -= 1) {
      if (
        selected.has(indices[index]) &&
        !selected.has(indices[index + 1])
      ) {
        [indices[index], indices[index + 1]] = [
          indices[index + 1],
          indices[index],
        ];
      }
    }
  } else {
    for (let index = 1; index < indices.length; index += 1) {
      if (
        selected.has(indices[index]) &&
        !selected.has(indices[index - 1])
      ) {
        [indices[index], indices[index - 1]] = [
          indices[index - 1],
          indices[index],
        ];
      }
    }
  }
  return indices;
}

function applyReorder(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  direction: TemplateV2ReorderDirection,
  requestedSelections?: StudioSelection[]
): TemplateV2CommandResult {
  const selections = requestedSelections ?? selectionSet;
  const context = siblingContext(layouts, selections);
  if (
    !context ||
    containsLockedPath(lockedElementKeys, context.selections, true)
  ) {
    return unchanged(layouts, selectionSet, lockedElementKeys);
  }
  const order = reorderedEntries(
    context.siblings.length,
    new Set(context.indices),
    direction
  );
  if (order.every((oldIndex, nextIndex) => oldIndex === nextIndex)) {
    return unchanged(layouts, selectionSet, lockedElementKeys);
  }
  const newIndexByOld = new Map(
    order.map((oldIndex, nextIndex) => [oldIndex, nextIndex])
  );
  const first = context.selections[0];
  const remap = (selection: StudioSelection) =>
    remapTemplateV2SelectionPath(
      selection,
      first.layoutId,
      first.componentId,
      context.parentPath,
      (oldIndex, tail) => {
        const nextIndex = newIndexByOld.get(oldIndex);
        return nextIndex === undefined ? null : [nextIndex, ...tail];
      }
    );
  const session = remapSession(selectionSet, lockedElementKeys, remap);
  return {
    layouts: replaceSiblings(
      layouts,
      context,
      order.map((oldIndex) => context.siblings[oldIndex])
    ),
    ...session,
  };
}

function applyGroup(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  requestedSelections?: StudioSelection[],
  name?: string
): TemplateV2CommandResult {
  const selections = requestedSelections ?? selectionSet;
  const context = siblingContext(layouts, selections);
  if (
    !context ||
    context.indices.length < 2 ||
    containsLockedPath(lockedElementKeys, context.selections, true)
  ) {
    return unchanged(layouts, selectionSet, lockedElementKeys);
  }
  const elements = context.indices.map(
    (index) => context.siblings[index] as JsonRecord
  );
  const bounds = elements.map(elementBounds);
  const left = Math.min(...bounds.map((bound) => bound.left));
  const top = Math.min(...bounds.map((bound) => bound.top));
  const right = Math.max(...bounds.map((bound) => bound.right));
  const bottom = Math.max(...bounds.map((bound) => bound.bottom));
  const insertAt = Math.min(...context.indices);
  const selected = new Set(context.indices);
  const group: JsonRecord = {
    type: "group",
    ...(name ? { name } : {}),
    position: { x: round(left), y: round(top) },
    size: { width: round(right - left), height: round(bottom - top) },
    rotation: 0,
    children: elements.map((element) =>
      moveElementToLocalCoordinates(element, left, top)
    ),
  };
  const siblings: unknown[] = [];
  const newIndexByOld = new Map<number, number>();
  for (let oldIndex = 0; oldIndex < context.siblings.length; oldIndex += 1) {
    if (oldIndex === insertAt) siblings.push(group);
    if (!selected.has(oldIndex)) {
      newIndexByOld.set(oldIndex, siblings.length);
      siblings.push(context.siblings[oldIndex]);
    }
  }
  const childIndexByOld = new Map(
    context.indices.map((oldIndex, childIndex) => [oldIndex, childIndex])
  );
  const first = context.selections[0];
  const remap = (selection: StudioSelection) =>
    remapTemplateV2SelectionPath(
      selection,
      first.layoutId,
      first.componentId,
      context.parentPath,
      (oldIndex, tail) => {
        const childIndex = childIndexByOld.get(oldIndex);
        if (childIndex !== undefined) {
          return [insertAt, "children", childIndex, ...tail];
        }
        const nextIndex = newIndexByOld.get(oldIndex);
        return nextIndex === undefined ? null : [nextIndex, ...tail];
      }
    );
  const session = remapSession([], lockedElementKeys, remap);
  return {
    layouts: replaceSiblings(layouts, context, siblings),
    selectionSet: [
      {
        layoutId: first.layoutId,
        componentId: first.componentId,
        elementPath: [...context.parentPath, insertAt],
      },
    ],
    lockedElementKeys: session.lockedElementKeys,
  };
}

function applyUngroup(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  requestedSelections?: StudioSelection[]
): TemplateV2CommandResult {
  const selections = requestedSelections ?? selectionSet;
  const context = siblingContext(layouts, selections);
  if (!context) return unchanged(layouts, selectionSet, lockedElementKeys);
  const groups = new Map<number, JsonRecord[]>();
  for (const index of context.indices) {
    const group = context.siblings[index];
    if (!isRecord(group) || group.type !== "group") {
      return unchanged(layouts, selectionSet, lockedElementKeys);
    }
    const groupSelection = context.selections.find(
      (selection) => selection.elementPath.at(-1) === index
    );
    if (
      !groupSelection ||
      containsLockedPath(lockedElementKeys, [groupSelection], true)
    ) {
      return unchanged(layouts, selectionSet, lockedElementKeys);
    }
    const x = numberField(group.position, "x");
    const y = numberField(group.position, "y");
    const children = Array.isArray(group.children) ? group.children : [];
    if (children.some((child) => !isRecord(child))) {
      return unchanged(layouts, selectionSet, lockedElementKeys);
    }
    groups.set(
      index,
      children.map((child) =>
        moveElementToParentCoordinates(child as JsonRecord, x, y)
      )
    );
  }
  const siblings: unknown[] = [];
  const newIndexByOld = new Map<number, number>();
  const childStartByOld = new Map<number, number>();
  for (let oldIndex = 0; oldIndex < context.siblings.length; oldIndex += 1) {
    const children = groups.get(oldIndex);
    if (children) {
      childStartByOld.set(oldIndex, siblings.length);
      siblings.push(...children);
    } else {
      newIndexByOld.set(oldIndex, siblings.length);
      siblings.push(context.siblings[oldIndex]);
    }
  }
  const first = context.selections[0];
  const remap = (selection: StudioSelection) =>
    remapTemplateV2SelectionPath(
      selection,
      first.layoutId,
      first.componentId,
      context.parentPath,
      (oldIndex, tail) => {
        const childStart = childStartByOld.get(oldIndex);
        if (childStart !== undefined) {
          if (tail[0] !== "children" || typeof tail[1] !== "number") return null;
          return [childStart + tail[1], ...tail.slice(2)];
        }
        const nextIndex = newIndexByOld.get(oldIndex);
        return nextIndex === undefined ? null : [nextIndex, ...tail];
      }
    );
  const session = remapSession([], lockedElementKeys, remap);
  const nextSelectionSet: StudioSelection[] = [];
  for (const index of context.indices) {
    const childStart = childStartByOld.get(index) as number;
    const childCount = groups.get(index)?.length ?? 0;
    for (let offset = 0; offset < childCount; offset += 1) {
      nextSelectionSet.push({
        layoutId: first.layoutId,
        componentId: first.componentId,
        elementPath: [...context.parentPath, childStart + offset],
      });
    }
  }
  return {
    layouts: replaceSiblings(layouts, context, siblings),
    selectionSet: nextSelectionSet,
    lockedElementKeys: session.lockedElementKeys,
  };
}

export function applyTemplateV2StudioCommand(
  layouts: JsonRecord,
  selectionSet: StudioSelection[],
  lockedElementKeys: ReadonlySet<string>,
  command: TemplateV2StudioCommand
): TemplateV2CommandResult {
  switch (command.type) {
    case "update-geometry-batch":
      return applyGeometryBatch(
        layouts,
        selectionSet,
        lockedElementKeys,
        command.updates
      );
    case "reorder-siblings":
      return applyReorder(
        layouts,
        selectionSet,
        lockedElementKeys,
        command.direction,
        command.selections
      );
    case "group-siblings":
      return applyGroup(
        layouts,
        selectionSet,
        lockedElementKeys,
        command.selections,
        command.name
      );
    case "ungroup":
      return applyUngroup(
        layouts,
        selectionSet,
        lockedElementKeys,
        command.selections
      );
    case "align-siblings":
      return applyAlign(
        layouts,
        selectionSet,
        lockedElementKeys,
        command.direction,
        command.selections
      );
    case "distribute-siblings":
      return applyDistribute(
        layouts,
        selectionSet,
        lockedElementKeys,
        command.direction,
        command.selections
      );
  }
}
