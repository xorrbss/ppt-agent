/**
 * Stage 1 (parse) — locating the renderable pieces of a template v2 payload.
 * Everything here still speaks raw JSON records: layouts, components, and the
 * flow-child sizing that has to happen before children are mapped.
 */
import {
  MIN_ELEMENT_SIZE,
  asRecord,
  isRecord,
  readArray,
  readNumber,
  readRecord,
  readString,
  readValue,
  round,
  stripNullish,
  truncateString,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";
import {
  localizeRawElementToFrame,
  rawComponentFrame,
  rawElementsFrame,
  type RawFrame,
} from "@/components/slide-editor/importing/template-v2/raw-frames";

export type TemplateV2Layout = {
  id?: unknown;
  description?: unknown;
  elements?: unknown;
  components?: unknown;
};

export function extractTemplateV2Layouts(value: unknown): TemplateV2Layout[] {
  if (Array.isArray(value)) {
    return value.filter(isRecord) as TemplateV2Layout[];
  }

  const record = asRecord(value);
  if (Array.isArray(record?.layouts)) {
    return record.layouts.filter(isRecord) as TemplateV2Layout[];
  }
  const nestedLayouts = asRecord(record?.layouts);
  if (Array.isArray(nestedLayouts?.layouts)) {
    return nestedLayouts.layouts.filter(isRecord) as TemplateV2Layout[];
  }

  return [];
}

export function extractLayoutElements(layout: TemplateV2Layout): unknown[] {
  if (Array.isArray(layout.elements) && layout.elements.length > 0) {
    return layout.elements;
  }

  const components = readArray(layout as UnknownRecord, "components");
  return components
    .map((component, componentIndex) =>
      componentToGroupElement(component, componentIndex),
    )
    .filter((element): element is UnknownRecord => Boolean(asRecord(element)));
}

export function componentToGroupElement(
  component: unknown,
  componentIndex: number,
): UnknownRecord | null {
  const raw = asRecord(component);
  if (!raw) return null;

  const elements = readArray(raw, "elements");
  if (elements.length === 0) return null;

  const renderedElements = elements.filter(isRecord);
  const componentFrame = rawComponentFrame(raw);
  const frame = componentFrame ?? rawElementsFrame(renderedElements);
  if (!frame) return null;

  const componentId =
    truncateString(readString(raw.id) ?? `component_${componentIndex}`, 120) ||
    `component_${componentIndex}`;
  const componentDescription = truncateString(
    readString(raw.description) ?? "",
    600,
  );

  return stripNullish({
    type: "group",
    position: { x: frame.x, y: frame.y },
    size: { width: frame.width, height: frame.height },
    children: componentFrame
      ? renderedElements.map((element) =>
        frameTopLevelFlowElementToComponent(element, componentFrame),
      )
      : renderedElements.map((element) =>
        localizeRawElementToFrame(element, frame),
      ),
    name: componentId,
    component_id: componentId,
    component_instance_id: `${componentId}:${componentIndex}`,
    component_description: componentDescription || null,
    design_variables: readArray(raw, "design_variables"),
  });
}

export function extractTemplateV2MergedComponents(template: unknown): unknown[] {
  const raw = asRecord(template);
  if (!raw) return [];

  const value = readValue(raw, "merged_components");
  if (Array.isArray(value)) {
    return value.filter(isRecord);
  }

  const record = asRecord(value);
  if (!record) return [];

  const components = readArray(record, "components");
  if (components.length > 0) {
    return components.filter(isRecord);
  }

  return Object.values(record).filter(isRecord);
}

function frameTopLevelFlowElementToComponent(
  element: UnknownRecord,
  frame: RawFrame,
): UnknownRecord {
  const type = readString(element.type);
  if (
    (type !== "grid" && type !== "flex") ||
    readRecord(element, "position") ||
    readRecord(element, "size")
  ) {
    return element;
  }

  return {
    ...element,
    position: { x: 0, y: 0 },
    size: { width: frame.width, height: frame.height },
  };
}

export function withEqualTemplateV2FlowChildSizes(
  element: Record<string, unknown>,
): unknown[] {
  const children = readArray(element, "children");
  if (children.length === 0) return children;

  const size = readRecord(element, "size");
  const width = readNumber(size ?? {}, "width");
  const height = readNumber(size ?? {}, "height");
  if (width == null || height == null) return children;

  const padding = readRecord(element, "padding");
  const contentWidth = Math.max(
    MIN_ELEMENT_SIZE,
    width -
    (readNumber(padding ?? {}, "left") ?? 0) -
    (readNumber(padding ?? {}, "right") ?? 0),
  );
  const contentHeight = Math.max(
    MIN_ELEMENT_SIZE,
    height -
    (readNumber(padding ?? {}, "top") ?? 0) -
    (readNumber(padding ?? {}, "bottom") ?? 0),
  );
  const type = readString(element.type);

  if (type === "flex" || type === "list-view") {
    const childTypes = children.map((child) => readString(asRecord(child)?.type));
    if (
      childTypes.some((childType) => childType == null) ||
      new Set(childTypes).size > 1
    ) {
      return children;
    }

    const direction = readString(element.direction) === "row" ? "row" : "column";
    const gap =
      direction === "row"
        ? readNumber(element, "column_gap") ??
        readNumber(element, "gap") ??
        0
        : readNumber(element, "row_gap") ??
        readNumber(element, "gap") ??
        0;
    const availableMain = Math.max(
      MIN_ELEMENT_SIZE,
      (direction === "row" ? contentWidth : contentHeight) -
      gap * Math.max(0, children.length - 1),
    );
    const mainSize = availableMain / children.length;
    return children.map((child) =>
      withTemplateV2ChildSize(
        child,
        direction === "row" ? mainSize : contentWidth,
        direction === "row" ? contentHeight : mainSize,
      ),
    );
  }

  if (type === "grid" || type === "grid-view") {
    const columns = Math.max(
      1,
      Math.min(children.length, Math.trunc(readNumber(element, "columns") ?? 1)),
    );
    const rows = Math.max(
      1,
      Math.trunc(readNumber(element, "rows") ?? Math.ceil(children.length / columns)),
    );
    const columnGap =
      readNumber(element, "column_gap") ??
      readNumber(element, "gap") ??
      0;
    const rowGap =
      readNumber(element, "row_gap") ??
      readNumber(element, "gap") ??
      0;
    const cellWidth = Math.max(
      MIN_ELEMENT_SIZE,
      (contentWidth - columnGap * Math.max(0, columns - 1)) / columns,
    );
    const cellHeight = Math.max(
      MIN_ELEMENT_SIZE,
      (contentHeight - rowGap * Math.max(0, rows - 1)) / rows,
    );
    return children.map((child) =>
      withTemplateV2ChildSize(child, cellWidth, cellHeight),
    );
  }

  return children;
}

function withTemplateV2ChildSize(
  child: unknown,
  width: number,
  height: number,
) {
  const record = asRecord(child);
  if (!record) return child;
  const size: UnknownRecord = readRecord(record, "size") ?? {};
  return {
    ...record,
    size: {
      ...size,
      width: readNumber(size, "width") ?? round(width),
      height: readNumber(size, "height") ?? round(height),
    },
  };
}

export function titleFromLayout(layout: TemplateV2Layout, index: number) {
  const id = readString(layout.id);
  const slideNumber = id?.match(/\d+/)?.[0] ?? `${index + 1}`;
  return truncateString(`Slide ${slideNumber}`, 60);
}
