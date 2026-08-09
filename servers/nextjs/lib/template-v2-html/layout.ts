import { cssNumber } from "./css";
import { frameStyleFromBox, childrenBounds, readBox } from "./style";
import type { Box, JsonRecord, RenderMode } from "./types";
import {
  readArray,
  readBoolean,
  readNumber,
  readRecord,
  readRecordOrNull,
  readString,
} from "./values";

export function containerOverflowStyle(
  item: JsonRecord,
  child: JsonRecord | null
): string {
  const overflow = readString(item.overflow);
  if (overflow === "hidden" || overflow === "visible") {
    return `overflow:${overflow}`;
  }
  if (readBoolean(item.clip)) return "overflow:hidden";
  if (!child || readString(child.type) !== "image") return "overflow:visible";

  const childHasClipPath = Boolean(readString(child.clip_path ?? child.clipPath));
  const hasPositionedChild = Boolean(readRecordOrNull(child.position));
  if (!hasPositionedChild) return "overflow:visible";
  if (childHasClipPath) return "overflow:hidden";

  const containerBox = readBox(item);
  const childBox = readBox(child);
  if (containerBox.width == null || containerBox.height == null) {
    return "overflow:visible";
  }

  const epsilon = 0.01;
  const childOverflows =
    childBox.x < -epsilon ||
    childBox.y < -epsilon ||
    (childBox.width != null &&
      childBox.x + childBox.width > containerBox.width + epsilon) ||
    (childBox.height != null &&
      childBox.y + childBox.height > containerBox.height + epsilon);

  return childOverflows ? "overflow:hidden" : "overflow:visible";
}

export function flexFrameStyle(
  item: JsonRecord,
  mode: RenderMode,
  children: unknown[],
  direction: "row" | "column",
  wrap: boolean,
  columnGap: number,
  rowGap: number
) {
  const box = readBox(item);
  const expanded = flexExpandedSize(item, box, children, direction, wrap, columnGap, rowGap);
  let style = frameStyleFromBox(box, mode);
  if (expanded.width != null && (box.width == null || expanded.width > box.width)) {
    style += `width:${cssNumber(expanded.width)}px;`;
  }
  if (expanded.height != null && (box.height == null || expanded.height > box.height)) {
    style += `height:${cssNumber(expanded.height)}px;`;
  }
  return style;
}

function flexExpandedSize(
  item: JsonRecord,
  box: Box,
  children: unknown[],
  direction: "row" | "column",
  wrap: boolean,
  columnGap: number,
  rowGap: number
): { width?: number; height?: number } {
  const records = children.map(readRecord);
  if (!records.length) return {};

  const padding = readRecord(item.padding);
  const paddingX = (readNumber(padding.left) ?? 0) + (readNumber(padding.right) ?? 0);
  const paddingY = (readNumber(padding.top) ?? 0) + (readNumber(padding.bottom) ?? 0);
  const sizes = records.map(flowChildSize);

  if (!wrap) {
    if (direction === "row") {
      return {
        width:
          paddingX +
          sizes.reduce((sum, size) => sum + size.width, 0) +
          columnGap * Math.max(0, sizes.length - 1),
      };
    }
    return {
      height:
        paddingY +
        sizes.reduce((sum, size) => sum + size.height, 0) +
        rowGap * Math.max(0, sizes.length - 1),
    };
  }

  const mainLimit =
    direction === "row"
      ? box.width == null
        ? null
        : Math.max(1, box.width - paddingX)
      : box.height == null
        ? null
        : Math.max(1, box.height - paddingY);
  if (mainLimit == null) return {};

  const lines: Array<{ cross: number; main: number }> = [];
  const mainGap = direction === "row" ? columnGap : rowGap;
  const crossGap = direction === "row" ? rowGap : columnGap;
  sizes.forEach((size) => {
    const childMain = direction === "row" ? size.width : size.height;
    const childCross = direction === "row" ? size.height : size.width;
    let line = lines.at(-1);
    if (!line || (line.main > 0 && line.main + mainGap + childMain > mainLimit)) {
      line = { cross: 0, main: 0 };
      lines.push(line);
    }
    line.main += (line.main > 0 ? mainGap : 0) + childMain;
    line.cross = Math.max(line.cross, childCross);
  });

  const requiredCross =
    lines.reduce((sum, line) => sum + line.cross, 0) +
    crossGap * Math.max(0, lines.length - 1);
  return direction === "row"
    ? { height: paddingY + requiredCross }
    : { width: paddingX + requiredCross };
}

function flowChildSize(child: JsonRecord) {
  const fallback = Array.isArray(child.children)
    ? childrenBounds(readArray(child.children).map(readRecord))
    : undefined;
  const box = readBox(child, fallback);
  return {
    width: box.width ?? 1,
    height: box.height ?? 1,
  };
}

export function gridColumnTemplate(columns: number, explicitWidth: number | null, columnGap: number) {
  if (explicitWidth == null) return `repeat(${columns},minmax(0,1fr))`;
  const columnWidth = Math.max(1, (explicitWidth - columnGap * (columns - 1)) / columns);
  return `repeat(${columns},${cssNumber(columnWidth)}px)`;
}

export function gridRowTemplate(
  rows: number | null,
  renderedRows: number,
  explicitHeight: number | null,
  rowGap: number
) {
  if (!rows) return "";
  if (explicitHeight == null) {
    return `grid-template-rows:repeat(${Math.max(rows, renderedRows)},minmax(0,1fr));`;
  }

  const rowHeight = Math.max(1, (explicitHeight - rowGap * (rows - 1)) / rows);
  return `grid-template-rows:repeat(${Math.max(rows, renderedRows)},${cssNumber(rowHeight)}px);`;
}

export function readLayoutChildren(item: JsonRecord): unknown[] {
  const children = readArray(item.children);
  if (children.length) return children;

  const elements = readArray(item.elements);
  if (elements.length) return elements;

  const child = readRecordOrNull(item.item);
  const count = Math.max(0, Math.floor(readNumber(item.count) ?? 0));
  return child && count ? Array.from({ length: count }, () => child) : [];
}
