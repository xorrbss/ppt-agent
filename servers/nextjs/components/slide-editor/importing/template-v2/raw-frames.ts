/**
 * Stage 1 (parse) — geometry over *raw* payload records, before any element is
 * mapped to the editor schema. Frames are used to give frameless components a
 * bounding box and to re-base their children onto it.
 */
import {
  asRecord,
  isRecord,
  readArray,
  readNumber,
  readRecord,
  readValue,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";

export type RawFrame = { x: number; y: number; width: number; height: number };

export function rawElementsFrame(elements: UnknownRecord[]) {
  return mergeRawElementFrames(
    elements
      .map((element) => rawElementFrame(element))
      .filter((frame): frame is RawFrame => Boolean(frame)),
  );
}

export function rawElementFrame(
  element: UnknownRecord,
  offsetX = 0,
  offsetY = 0,
): RawFrame | null {
  const position = readRecord(element, "position");
  const size = readRecord(element, "size");
  const x = readNumber(position ?? {}, "x");
  const y = readNumber(position ?? {}, "y");
  const width = readNumber(size ?? {}, "width");
  const height = readNumber(size ?? {}, "height");
  const hasPosition = x != null && y != null;
  const frame =
    hasPosition && width != null && height != null
      ? {
        x: offsetX + x,
        y: offsetY + y,
        width: Math.max(1, width),
        height: Math.max(1, height),
      }
      : null;
  const childOffsetX = hasPosition ? offsetX + x : offsetX;
  const childOffsetY = hasPosition ? offsetY + y : offsetY;
  const childFrames = rawElementChildren(element)
    .map((child) => rawElementFrame(child, childOffsetX, childOffsetY))
    .filter((childFrame): childFrame is RawFrame => Boolean(childFrame));

  return mergeRawElementFrames(frame ? [frame, ...childFrames] : childFrames);
}

function rawElementChildren(element: UnknownRecord): UnknownRecord[] {
  const children = readArray(element, "children").filter(isRecord);
  const child = asRecord(readValue(element, "child"));
  return [...children, ...(child ? [child] : [])];
}

function mergeRawElementFrames(frames: RawFrame[]): RawFrame | null {
  if (frames.length === 0) {
    return null;
  }

  const minX = Math.min(...frames.map((frame) => frame.x));
  const minY = Math.min(...frames.map((frame) => frame.y));
  const maxX = Math.max(...frames.map((frame) => frame.x + frame.width));
  const maxY = Math.max(...frames.map((frame) => frame.y + frame.height));

  return {
    x: minX,
    y: minY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

export function rawComponentFrame(component: UnknownRecord) {
  const position = readRecord(component, "position");
  const size = readRecord(component, "size");
  const x = readNumber(position ?? {}, "x");
  const y = readNumber(position ?? {}, "y");
  const width = readNumber(size ?? {}, "width");
  const height = readNumber(size ?? {}, "height");
  if (x == null || y == null) return null;
  if (width == null || height == null) {
    const elements = readArray(component, "elements").filter(isRecord);
    const content = rawElementsContentSize(elements);
    return {
      x,
      y,
      width: content.width,
      height: content.height,
    };
  }

  return {
    x,
    y,
    width: Math.max(1, width),
    height: Math.max(1, height),
  };
}

function rawElementsContentSize(elements: UnknownRecord[]) {
  const frame = rawElementsFrame(elements);
  if (!frame) return { width: 1, height: 1 };
  return {
    width: Math.max(1, frame.x + frame.width),
    height: Math.max(1, frame.y + frame.height),
  };
}

export function localizeRawElementToFrame(
  element: UnknownRecord,
  frame: { x: number; y: number },
) {
  const position = readRecord(element, "position");
  const localized: UnknownRecord = position
    ? {
      ...element,
      position: {
        ...position,
        x: (readNumber(position, "x") ?? 0) - frame.x,
        y: (readNumber(position, "y") ?? 0) - frame.y,
      },
    }
    : { ...element };

  if (!position) {
    const children = readArray(localized, "children");
    if (children.length > 0) {
      localized.children = children.map((child) => {
        const rawChild = asRecord(child);
        return rawChild ? localizeRawElementToFrame(rawChild, frame) : child;
      });
    }

    const child = asRecord(readValue(localized, "child"));
    if (child) {
      localized.child = localizeRawElementToFrame(child, frame);
    }
  }

  return localized;
}
