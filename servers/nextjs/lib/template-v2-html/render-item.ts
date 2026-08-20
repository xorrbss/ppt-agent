import { renderChart } from "./chart";
import { cssAlignment, cssNumber, horizontalAlign, paddingStyle, verticalAlign } from "./css";
import { isComponent } from "./element-types";
import { renderImage } from "./image";
import { renderInfographic } from "./infographic";
import {
  containerOverflowStyle,
  flexFrameStyle,
  gridColumnTemplate,
  gridRowTemplate,
  readLayoutChildren,
} from "./layout";
import { boxStyle, childrenBounds, frameStyle } from "./style";
import { renderTable } from "./table";
import { renderMath, renderText, renderTextList } from "./text";
import type { JsonRecord, RenderMode } from "./types";
import {
  readArray,
  readBoolean,
  readNumber,
  readRecord,
  readRecordOrNull,
  readString,
} from "./values";
import { renderPolygon, renderSvg } from "./vector";

export function renderItem(item: JsonRecord, mode: RenderMode): string {
  if (isComponent(item)) {
    // A component's persisted size can describe its stale authoring frame.
    // The HTML group must size itself from the element bounds instead.
    const { size, ...component } = item;
    void size;
    return renderGroup(
      { ...component, type: "group", children: item.elements },
      mode,
    );
  }

  switch (readString(item.type)) {
    case "vector":
      return renderPolygon(item, mode);
    case "svg":
      return renderSvg(item, mode);
    case "image":
      return renderImage(item, mode);
    case "text":
      return renderText(item, mode);
    case "math":
      return renderMath(item, mode);
    case "text-list":
      return renderTextList(item, mode);
    case "table":
      return renderTable(item, mode);
    case "container":
      return renderContainer(item, mode);
    case "flex":
    case "list-view":
      return renderFlex(item, mode);
    case "grid":
    case "grid-view":
      return renderGrid(item, mode);
    case "group":
      return renderGroup(item, mode);
    case "chart":
      return renderChart(item, mode);
    case "infographic":
      return renderInfographic(item, mode);
    default:
      if (Array.isArray(item.children)) return renderGroup(item, mode);
      if (readRecordOrNull(item.child)) return renderContainer(item, mode);
      return "";
  }
}

function renderContainer(item: JsonRecord, mode: RenderMode): string {
  const child = readRecordOrNull(item.child);
  const alignment = readRecord(item.alignment);
  const style = `${frameStyle(item, mode)}${boxStyle(item)}${paddingStyle(
    readRecord(item.padding)
  )}display:flex;align-items:${verticalAlign(
    readString(alignment.vertical)
  )};justify-content:${horizontalAlign(
    readString(alignment.horizontal)
  )};${containerOverflowStyle(item, child)}`;
  return `<div style="${style}">${
    child ? renderItem(child, readRecordOrNull(child.position) ? "absolute" : "flow") : ""
  }</div>`;
}

function renderFlex(item: JsonRecord, mode: RenderMode): string {
  const direction = readString(item.direction) === "row" ? "row" : "column";
  const gap = readNumber(item.gap) ?? 0;
  const rowGap = readNumber(item.rowGap ?? item.row_gap) ?? gap;
  const columnGap = readNumber(item.columnGap ?? item.column_gap) ?? gap;
  const childrenList = readLayoutChildren(item);
  const children = childrenList
    .map((child) => renderItem(readRecord(child), "flow"))
    .join("");
  const style = `${flexFrameStyle(
    item,
    mode,
    childrenList,
    direction,
    readBoolean(item.wrap),
    columnGap,
    rowGap,
  )}${boxStyle(item)}${paddingStyle(
    readRecord(item.padding)
  )}display:flex;flex-direction:${direction};flex-wrap:${readBoolean(item.wrap) ? "wrap" : "nowrap"};align-items:${cssAlignment(
    readString(item.alignItems ?? item.align_items),
    "stretch"
  )};justify-content:${cssAlignment(
    readString(item.justifyContent ?? item.justify_content),
    "flex-start"
  )};gap:${cssNumber(gap)}px;column-gap:${cssNumber(columnGap)}px;row-gap:${cssNumber(rowGap)}px;overflow:visible`;
  return `<div style="${style}">${children}</div>`;
}

function renderGrid(item: JsonRecord, mode: RenderMode): string {
  const columns = Math.max(1, Math.floor(readNumber(item.columns) ?? 1));
  const gap = readNumber(item.gap) ?? 0;
  const rowGap = readNumber(item.rowGap ?? item.row_gap) ?? gap;
  const columnGap = readNumber(item.columnGap ?? item.column_gap) ?? gap;
  const childrenList = readLayoutChildren(item);
  const renderedRows = Math.max(1, Math.ceil(childrenList.length / columns));
  const declaredRows = readNumber(item.rows);
  const rows = declaredRows == null ? null : Math.max(1, Math.floor(declaredRows));
  const size = readRecord(item.size);
  const explicitHeight = readNumber(size.height);
  const explicitWidth = readNumber(size.width);
  const children = childrenList
    .map((child) => renderItem(readRecord(child), "flow"))
    .join("");
  const rowTemplate = gridRowTemplate(rows, renderedRows, explicitHeight, rowGap);
  const columnTemplate = gridColumnTemplate(columns, explicitWidth, columnGap);
  const style = `${frameStyle(item, mode)}${boxStyle(item)}${paddingStyle(
    readRecord(item.padding)
  )}display:grid;grid-template-columns:${columnTemplate};${rowTemplate}align-items:${cssAlignment(
      readString(item.alignItems ?? item.align_items),
      "stretch"
    )};justify-items:${cssAlignment(
      readString(item.justifyItems ?? item.justify_items),
      "stretch"
    )};column-gap:${cssNumber(columnGap)}px;row-gap:${cssNumber(rowGap)}px;overflow:visible`;
  return `<div style="${style}">${children}</div>`;
}

function renderGroup(item: JsonRecord, mode: RenderMode): string {
  const children = readArray(item.children).map(readRecord);
  const content = children.map((child) => renderItem(child, "absolute")).join("");
  return `<div style="${frameStyle(item, mode, childrenBounds(children))}${boxStyle(
    item
  )}overflow:visible">${content}</div>`;
}
