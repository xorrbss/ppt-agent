/**
 * Stage 3 (map to schema) — the element dispatcher plus the box-model element
 * adapters. Graphics (vector/chart/infographic) live in `graphics-elements.ts`.
 */
import { resolveBackendAssetUrl } from "@/utils/api";
import {
  EDITOR_STAGE_HEIGHT,
  EDITOR_STAGE_WIDTH,
  type Padding,
  type SlideElement,
  type TextElement,
} from "@/components/slide-editor/types";
import {
  MIN_ELEMENT_SIZE,
  asRecord,
  clamp,
  positiveInteger,
  positiveIntegerOrNull,
  readArray,
  readBoolean,
  readEnum,
  readLayoutAlignment,
  readNumber,
  readRecord,
  readString,
  readValue,
  round,
  truncateString,
  type AdaptedSize,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";
import { withEqualTemplateV2FlowChildSizes } from "@/components/slide-editor/importing/template-v2/raw-layout";
import {
  baseElement,
  requiredBaseElement,
} from "@/components/slide-editor/importing/template-v2/element-base";
import { groupFrame } from "@/components/slide-editor/importing/template-v2/element-bounds";
import {
  adaptAlignment,
  adaptBorderRadius,
  adaptFill,
  adaptFont,
  adaptPadding,
  adaptShadow,
  adaptStroke,
} from "@/components/slide-editor/importing/template-v2/styles";
import {
  adaptTableCells,
  adaptTextListItems,
  adaptTextRuns,
} from "@/components/slide-editor/importing/template-v2/content";
import {
  adaptChart,
  adaptInfographic,
  adaptVector,
} from "@/components/slide-editor/importing/template-v2/graphics-elements";

export function adaptElement(value: unknown): SlideElement | null {
  const raw = asRecord(value);
  const type = readString(raw?.type);
  if (!raw || !type) return null;

  switch (type) {
    case "text":
      return adaptText(raw);
    case "math":
      return adaptMath(raw);
    case "container":
      return adaptContainer(raw);
    case "image":
      return adaptImage(raw);
    case "text-list":
      return adaptTextList(raw);
    case "table":
      return adaptTable(raw);
    case "vector":
      return adaptVector(raw);
    case "chart":
      return adaptChart(raw);
    case "infographic":
      return adaptInfographic(raw);
    case "flex":
      return adaptFlex(raw);
    case "grid":
      return adaptGrid(raw);
    case "group":
      return adaptGroup(raw);
    default:
      return null;
  }
}

function adaptText(raw: UnknownRecord): SlideElement {
  const font = adaptFont(readRecord(raw, "font"));
  const element: TextElement = {
    ...baseElement(raw),
    type: "text",
    font: font && font.line_height == null ? { ...font, line_height: 1 } : font,
    alignment: adaptAlignment(readRecord(raw, "alignment")),
    fill: adaptFill(readRecord(raw, "fill")),
    stroke: adaptStroke(readRecord(raw, "stroke")),
    runs: adaptTextRuns(readArray(raw, "runs"), readString(raw.text)),
    max_length: readNumber(raw, "max_length"),
    min_length: readNumber(raw, "min_length"),
  };
  return element;
}

function adaptMath(raw: UnknownRecord): SlideElement {
  return {
    ...baseElement(raw),
    type: "math",
    latex: truncateString(readString(raw.latex) ?? "", 4000),
    display_mode: readBoolean(raw, "display_mode") ?? true,
    font: adaptFont(readRecord(raw, "font")),
    alignment: adaptAlignment(readRecord(raw, "alignment")),
    max_length: readNumber(raw, "max_length"),
    min_length: readNumber(raw, "min_length"),
  };
}

function adaptContainer(raw: UnknownRecord): SlideElement {
  const child = adaptElement(readValue(raw, "child"));
  const base = baseElement(raw);
  const padding = adaptPadding(readRecord(raw, "padding"));
  const inferredSize =
    !base.size && child?.size ? paddedChildSize(child.size, padding) : null;
  return {
    ...base,
    ...(inferredSize ? { size: inferredSize } : {}),
    type: "container",
    alignment: adaptAlignment(readRecord(raw, "alignment")),
    fill: adaptFill(readRecord(raw, "fill")),
    stroke: adaptStroke(readRecord(raw, "stroke")),
    border_radius: adaptBorderRadius(readRecord(raw, "border_radius")),
    padding,
    shadow: adaptShadow(readRecord(raw, "shadow")),
    child,
  };
}

function paddedChildSize(
  size: AdaptedSize,
  padding: Padding | null,
): AdaptedSize {
  return {
    width: clamp(
      round(size.width + (padding?.left ?? 0) + (padding?.right ?? 0)),
      MIN_ELEMENT_SIZE,
      EDITOR_STAGE_WIDTH,
    ),
    height: clamp(
      round(size.height + (padding?.top ?? 0) + (padding?.bottom ?? 0)),
      MIN_ELEMENT_SIZE,
      EDITOR_STAGE_HEIGHT,
    ),
  };
}

function adaptImage(raw: UnknownRecord): SlideElement {
  const data = readString(raw.data);
  return {
    ...baseElement(raw),
    type: "image",
    flip_h: readBoolean(raw, "flip_h"),
    flip_v: readBoolean(raw, "flip_v"),
    data: data ? resolveBackendAssetUrl(data) : null,
    name: truncateString(readString(raw.name) ?? "", 120) || null,
    fit: readEnum(raw, ["contain", "cover", "fill"], "fit"),
    focus_x: readNumber(raw, "focus_x"),
    focus_y: readNumber(raw, "focus_y"),
    crop_scale: readNumber(raw, "crop_scale"),
    border_radius: adaptBorderRadius(readRecord(raw, "border_radius")),
    clippath: readString(raw.clippath ?? raw.clipPath ?? raw.clip_path),
    color: readString(raw.color),
    is_icon: readBoolean(raw, "is_icon"),
    icon_type: readEnum(
      raw,
      ["bold", "duotone", "fill", "light", "regular", "thin"],
      "icon_type",
    ),
  };
}

function adaptTextList(raw: UnknownRecord): SlideElement {
  const font = adaptFont(readRecord(raw, "font"));
  const marker = readEnum(raw, ["bullet", "number", "none"], "marker");
  return {
    ...baseElement(raw),
    type: "text-list",
    font,
    marker,
    items: adaptTextListItems(readArray(raw, "items")),
    max_items: readNumber(raw, "max_items"),
    min_items: readNumber(raw, "min_items"),
    max_item_length: readNumber(raw, "max_item_length"),
    min_item_length: readNumber(raw, "min_item_length"),
  };
}

function adaptTable(raw: UnknownRecord): SlideElement {
  const columns = adaptTableCells(readArray(raw, "columns"));
  const rows = readArray(raw, "rows")
    .map((row) => adaptTableCells(Array.isArray(row) ? row : []))
    .filter((row) => row.length > 0);

  return {
    ...baseElement(raw),
    type: "table",
    font: adaptFont(readRecord(raw, "font")),
    columns,
    rows: rows.length > 0 ? rows : [columns],
    max_columns: readNumber(raw, "max_columns"),
    min_columns: readNumber(raw, "min_columns"),
    max_rows: readNumber(raw, "max_rows"),
    min_rows: readNumber(raw, "min_rows"),
  };
}

function adaptFlex(raw: UnknownRecord): SlideElement {
  return {
    ...requiredBaseElement(raw),
    type: "flex",
    direction: readEnum(raw, ["row", "column"], "direction") ?? "column",
    wrap: readBoolean(raw, "wrap"),
    align_items: readLayoutAlignment(raw, "align_items"),
    justify_content: readLayoutAlignment(raw, "justify_content"),
    padding: adaptPadding(readRecord(raw, "padding")),
    gap: readNumber(raw, "gap"),
    column_gap: readNumber(raw, "column_gap"),
    row_gap: readNumber(raw, "row_gap"),
    children: withEqualTemplateV2FlowChildSizes(raw)
      .map(adaptElement)
      .filter(Boolean) as SlideElement[],
    max_children: readNumber(raw, "max_children"),
    min_children: readNumber(raw, "min_children"),
  };
}

function adaptGrid(raw: UnknownRecord): SlideElement {
  return {
    ...requiredBaseElement(raw),
    type: "grid",
    columns: positiveInteger(readNumber(raw, "columns"), 1),
    rows: positiveIntegerOrNull(readNumber(raw, "rows")),
    gap: readNumber(raw, "gap"),
    column_gap: readNumber(raw, "column_gap"),
    row_gap: readNumber(raw, "row_gap"),
    align_items: readLayoutAlignment(raw, "align_items"),
    justify_items: readLayoutAlignment(raw, "justify_items"),
    padding: adaptPadding(readRecord(raw, "padding")),
    children: withEqualTemplateV2FlowChildSizes(raw)
      .map(adaptElement)
      .filter(Boolean) as SlideElement[],
    max_children: readNumber(raw, "max_children"),
    min_children: readNumber(raw, "min_children"),
  };
}

function adaptGroup(raw: UnknownRecord): SlideElement {
  const children = readArray(raw, "children")
    .map(adaptElement)
    .filter(Boolean) as SlideElement[];
  const base = baseElement(raw);
  const frame = groupFrame(base, children);

  return {
    ...base,
    ...frame,
    type: "group",
    children,
    max_children: readNumber(raw, "max_children"),
    min_children: readNumber(raw, "min_children"),
  };
}
