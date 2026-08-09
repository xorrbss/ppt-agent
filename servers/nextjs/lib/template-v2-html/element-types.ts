import type { JsonRecord } from "./types";
import { readString } from "./values";

const ELEMENT_TYPES = new Set([
  "text",
  "math",
  "container",
  "image",
  "text-list",
  "table",
  "vector",
  "svg",
  "chart",
  "infographic",
  "flex",
  "grid",
  "group",
  "list-view",
  "grid-view",
]);

export function isComponent(item: JsonRecord): item is JsonRecord & { elements: unknown[] } {
  const type = readString(item.type);
  return Array.isArray(item.elements) && (!type || !ELEMENT_TYPES.has(type));
}
