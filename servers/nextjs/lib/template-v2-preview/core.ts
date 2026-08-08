import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";

export type UnknownRecord = Record<string, unknown>;
export type TemplateSavePayload = UnknownRecord & {
  id: string;
  name: string;
  layout_count: number;
  layouts: unknown;
};
export type PanelMode =
  | "blocks"
  | "texts"
  | "charts"
  | "tables"
  | "images"
  | "elements"
  | "schema"
  | "layouts";
export type LayoutPath = Array<string | number>;
export type HistoryCommand = { action: "undo" | "redo"; token: number };
export type HistoryAvailability = { canUndo: boolean; canRedo: boolean };

export type CreatedTemplateLayout = {
  index: number;
  layout: TemplateV2Layout;
};

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readString(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function readNumber(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

export function readArray(value: unknown) {
  return Array.isArray(value) ? value : [];
}

export function cloneLayout<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function withEditedLayouts(
  currentLayoutsValue: unknown,
  layouts: TemplateV2Layout[],
) {
  if (Array.isArray(currentLayoutsValue)) {
    return layouts;
  }

  if (isRecord(currentLayoutsValue)) {
    return {
      ...currentLayoutsValue,
      layouts,
    };
  }

  return { layouts };
}

export function buildTemplateSavePayload({
  layouts,
  name,
  targetTemplateId,
  template,
}: {
  layouts: TemplateV2Layout[];
  name: string;
  targetTemplateId: string;
  template: unknown;
}): TemplateSavePayload {
  const templateRecord = isRecord(template) ? template : {};
  const payload = cloneLayout(templateRecord);

  payload.id = targetTemplateId;
  payload.name = name;
  payload.layout_count = layouts.length;
  payload.layouts = withEditedLayouts(templateRecord.layouts, layouts);

  return payload as TemplateSavePayload;
}

export function hashKey(value: string) {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(hash).toString(36);
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

export function extractCreatedLayouts(value: unknown): CreatedTemplateLayout[] {
  if (!isRecord(value)) return [];
  const layoutsValue = value.layouts;
  if (!Array.isArray(layoutsValue)) return [];

  return layoutsValue.flatMap((item) => {
    if (!isRecord(item)) return [];
    const index = item.index;
    if (!Number.isInteger(index) || !item.layout) return [];
    return [
      {
        index: index as number,
        layout: item.layout as TemplateV2Layout,
      },
    ];
  });
}
