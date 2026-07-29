"use client";

import {
  isJsonRecord,
  records,
  templateV2SelectionKey,
  type ElementPath,
  type JsonRecord,
  type StudioSelection,
  type TemplateV2Scene,
} from "@/lib/template-v2-studio";
import { stringValue } from "@/lib/template-v2-konva";

export interface TemplateV2SelectionControls {
  allLocked: boolean;
  canAlign: boolean;
  canDistribute: boolean;
  canGroup: boolean;
  canUngroup: boolean;
  canMoveForward: boolean;
  canMoveBackward: boolean;
  canBringToFront: boolean;
  canSendToBack: boolean;
  hasSelection: boolean;
  exactLocked: boolean;
  lockConflict: boolean;
}

export function pathLabel(path: ElementPath): string {
  return path.map(String).join(" / ");
}

function elementChildren(
  element: JsonRecord,
  path: ElementPath
): Array<{ element: JsonRecord; path: ElementPath }> {
  if (element.type === "container" && isJsonRecord(element.child)) {
    return [{ element: element.child, path: [...path, "child"] }];
  }
  if (element.type === "group") {
    return records(element.children).map((child, index) => ({
      element: child,
      path: [...path, "children", index],
    }));
  }
  return [];
}

export function elementAtPath(
  elements: JsonRecord[],
  path: ElementPath
): JsonRecord | null {
  let value: unknown = elements;
  for (const part of path) {
    value =
      typeof part === "number" && Array.isArray(value)
        ? value[part]
        : typeof part === "string" && isJsonRecord(value)
          ? value[part]
          : null;
  }
  return isJsonRecord(value) ? value : null;
}

export function pathStartsWith(
  path: ElementPath,
  prefix: ElementPath
): boolean {
  return (
    path.length >= prefix.length &&
    prefix.every((part, index) => path[index] === part)
  );
}

export function getTemplateV2SelectionControls({
  scene,
  selections,
  lockedSelections,
  lockedElementKeys,
}: {
  scene: TemplateV2Scene | null;
  selections: StudioSelection[];
  lockedSelections: StudioSelection[];
  lockedElementKeys: ReadonlySet<string>;
}): TemplateV2SelectionControls {
  const selectedElements = scene
    ? selections.map((selection) =>
        selection.layoutId === String(scene.layout.id) &&
        selection.componentId === String(scene.component.id)
          ? elementAtPath(scene.elements, selection.elementPath)
          : null
      )
    : [];
  const exactLocked = selections.some((selection) =>
    lockedElementKeys.has(templateV2SelectionKey(selection))
  );
  const lockConflict = selections.some((selection) =>
    lockedSelections.some(
      (locked) =>
        locked.layoutId === selection.layoutId &&
        locked.componentId === selection.componentId &&
        (pathStartsWith(locked.elementPath, selection.elementPath) ||
          pathStartsWith(selection.elementPath, locked.elementPath))
    )
  );
  const allLocked =
    selections.length > 0 &&
    selections.every((selection) =>
      lockedElementKeys.has(templateV2SelectionKey(selection))
    );

  let siblingCount = 0;
  let indices: number[] = [];
  if (scene && selections.length > 0) {
    const parentPath = selections[0].elementPath.slice(0, -1);
    let siblings: unknown = scene.elements;
    for (const part of parentPath) {
      siblings =
        typeof part === "number" && Array.isArray(siblings)
          ? siblings[part]
          : typeof part === "string" && isJsonRecord(siblings)
            ? siblings[part]
            : null;
    }
    if (Array.isArray(siblings)) {
      siblingCount = siblings.length;
      indices = selections
        .map((selection) => selection.elementPath.at(-1))
        .filter((index): index is number => typeof index === "number");
    }
  }
  const selectedIndices = new Set(indices);
  const canReorder =
    selections.length > 0 &&
    indices.length === selections.length &&
    selectedElements.every(Boolean) &&
    !lockConflict;
  const canAlign =
    selections.length >= 2 &&
    indices.length === selections.length &&
    selectedElements.every(Boolean) &&
    !lockConflict;
  const canDistribute =
    selections.length >= 3 &&
    indices.length === selections.length &&
    selectedElements.every(Boolean) &&
    !lockConflict;
  const canMoveForward =
    canReorder &&
    indices.some(
      (index) => index < siblingCount - 1 && !selectedIndices.has(index + 1)
    );
  const canMoveBackward =
    canReorder &&
    indices.some((index) => index > 0 && !selectedIndices.has(index - 1));

  return {
    allLocked,
    canAlign,
    canDistribute,
    canGroup:
      selections.length >= 2 && selectedElements.every(Boolean) && !lockConflict,
    canUngroup:
      selections.length > 0 &&
      selectedElements.every((element) => element?.type === "group") &&
      !lockConflict,
    canMoveForward,
    canMoveBackward,
    canBringToFront: canMoveForward,
    canSendToBack: canMoveBackward,
    hasSelection: selections.length > 0,
    exactLocked,
    lockConflict,
  };
}

function ElementBranch({
  element,
  path,
  selectedPathKeys,
  lockedPathKeys,
  onSelect,
}: {
  element: JsonRecord;
  path: ElementPath;
  selectedPathKeys: ReadonlySet<string>;
  lockedPathKeys: ReadonlySet<string>;
  onSelect(path: ElementPath, additive: boolean): void;
}) {
  const children = elementChildren(element, path);
  const key = JSON.stringify(path);
  const selected = selectedPathKeys.has(key);
  const locked = lockedPathKeys.has(key);

  return (
    <li className="my-1">
      <button
        type="button"
        aria-pressed={selected}
        onClick={(event) => onSelect(path, event.ctrlKey || event.metaKey)}
        className={`w-full rounded px-2 py-1 text-left text-xs ${
          selected
            ? "bg-violet-500/30 text-violet-100"
            : "text-slate-300 hover:bg-slate-800"
        }`}
      >
        {stringValue(element.name, stringValue(element.type, "element"))}
        <span className="ml-1 text-slate-500">({pathLabel(path)})</span>
        {locked ? (
          <span className="ml-1 text-amber-300" aria-label="Locked">
            Locked
          </span>
        ) : null}
      </button>
      {children.length ? (
        <ul className="ml-3 border-l border-slate-700 pl-2">
          {children.map((child) => (
            <ElementBranch
              key={pathLabel(child.path)}
              element={child.element}
              path={child.path}
              selectedPathKeys={selectedPathKeys}
              lockedPathKeys={lockedPathKeys}
              onSelect={onSelect}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export default function TemplateV2ElementTree({
  elements,
  selectedPathKeys,
  lockedPathKeys,
  onSelect,
}: {
  elements: JsonRecord[];
  selectedPathKeys: ReadonlySet<string>;
  lockedPathKeys: ReadonlySet<string>;
  onSelect(path: ElementPath, additive: boolean): void;
}) {
  return (
    <ul>
      {elements.map((element, index) => (
        <ElementBranch
          key={index}
          element={element}
          path={[index]}
          selectedPathKeys={selectedPathKeys}
          lockedPathKeys={lockedPathKeys}
          onSelect={onSelect}
        />
      ))}
    </ul>
  );
}
