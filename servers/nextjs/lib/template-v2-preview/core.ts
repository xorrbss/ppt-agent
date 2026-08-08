import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";

export type UnknownRecord = Record<string, unknown>;

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readString(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function cloneLayout<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function readLayoutId(layout: TemplateV2Layout, index: number) {
  const id = readString((layout as UnknownRecord).id).trim();
  return id || `slide-${index + 1}`;
}

export function readLayoutIdValue(layout: TemplateV2Layout, index: number) {
  const layoutRecord = layout as UnknownRecord;
  if (Object.prototype.hasOwnProperty.call(layoutRecord, "id")) {
    return readString(layoutRecord.id);
  }

  return readLayoutId(layout, index);
}

export function readLayoutDescription(layout: TemplateV2Layout) {
  return readString((layout as UnknownRecord).description);
}

export function updateLayoutMetadata(
  layout: TemplateV2Layout,
  field: "id" | "description",
  value: string,
) {
  const nextLayout = cloneLayout(layout) as UnknownRecord;
  nextLayout[field] = value;

  return nextLayout as TemplateV2Layout;
}
