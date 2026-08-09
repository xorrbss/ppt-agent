import { chartConfig } from "./chart-config";
import { cssNumber, escapeAttribute } from "./css";
import { isComponent } from "./element-types";
import { frameStyle, readBox, transformStyle } from "./style";
import type { JsonRecord, RenderMode } from "./types";
import { readArray, readRecord, readRecordOrNull, readString } from "./values";

export function renderChart(item: JsonRecord, mode: RenderMode): string {
  const box = readBox(item);
  const width = Math.max(1, box.width ?? 1);
  const height = Math.max(1, box.height ?? 1);
  const config = chartConfig(item, height);

  return `<div style="${frameStyle(item, mode)}${transformStyle(
    item
  )}overflow:hidden"><canvas data-presenton-chart="true" data-chart-config="${escapeAttribute(
    JSON.stringify(config)
  )}" width="${cssNumber(Math.round(width))}" height="${cssNumber(
    Math.round(height)
  )}" style="display:block;width:100%;height:100%"></canvas></div>`;
}

export function hasChartItem(item: JsonRecord): boolean {
  if (isComponent(item)) {
    return readArray(item.elements).map(readRecord).some(hasChartItem);
  }

  if (readString(item.type) === "chart") return true;
  if (Array.isArray(item.children)) {
    return readArray(item.children).map(readRecord).some(hasChartItem);
  }

  if (Array.isArray(item.elements)) {
    return readArray(item.elements).map(readRecord).some(hasChartItem);
  }

  const child = readRecordOrNull(item.child);
  if (child) return hasChartItem(child);

  const itemChild = readRecordOrNull(item.item);
  return itemChild ? hasChartItem(itemChild) : false;
}
