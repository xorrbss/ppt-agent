import type { JsonRecord } from "./types";
import { TEMPLATE_V2_ELEMENT_TYPES } from "./template-v2-contract.generated";
import { readString } from "./values";

const LEGACY_RENDERER_ELEMENT_TYPES = [
  "svg",
  "list-view",
  "grid-view",
] as const;

const ELEMENT_TYPES = new Set<string>([
  ...TEMPLATE_V2_ELEMENT_TYPES,
  ...LEGACY_RENDERER_ELEMENT_TYPES,
]);

export function isComponent(item: JsonRecord): item is JsonRecord & { elements: unknown[] } {
  const type = readString(item.type);
  return Array.isArray(item.elements) && (!type || !ELEMENT_TYPES.has(type));
}
