import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { cloneLayout, isRecord, readArray, readNumber, readString, type LayoutPath, type UnknownRecord } from "./core";
import { humanize, textListItemsToString, textRunsToString, updateTextListItems, updateTextRuns } from "./text";

export type SchemaField = {
  decorative: boolean;
  elementType: string;
  id: string;
  label: string;
  type: "text" | "text-list" | "image" | "element";
  path: LayoutPath;
  value: string;
  minChars?: number;
  maxChars?: number;
};

function schemaLabelForElement(
  element: UnknownRecord,
  fallback: string,
  parentLabel?: string,
) {
  const label =
    readString(element.component_slot) ||
    readString(element.name) ||
    readString(element.component_description) ||
    parentLabel ||
    fallback;
  return humanize(label);
}

export function collectSchemaFields(layout: TemplateV2Layout) {
  const fields: SchemaField[] = [];

  const addElement = (
    element: unknown,
    path: LayoutPath,
    parentLabel?: string,
  ) => {
    if (!isRecord(element)) return;

    const type = readString(element.type);
    const decorative = element.decorative !== false;
    const label = schemaLabelForElement(
      element,
      `${type || "Field"} ${fields.length + 1}`,
      parentLabel,
    );

    if (type === "text") {
      const value = textRunsToString(element.runs);
      if (value || label) {
        fields.push({
          decorative,
          elementType: type,
          id: path.join("."),
          label,
          type: "text",
          path,
          value,
          minChars: readNumber(element.min_length),
          maxChars: readNumber(element.max_length),
        });
      }
    }

    if (type === "text-list") {
      const value = textListItemsToString(element.items);
      if (value || label) {
        fields.push({
          decorative,
          elementType: type,
          id: path.join("."),
          label,
          type: "text-list",
          path,
          value,
          minChars: readNumber(element.min_item_length),
          maxChars: readNumber(element.max_item_length),
        });
      }
    }

    if (type === "image") {
      fields.push({
        decorative,
        elementType: element.is_icon === true ? "icon" : type,
        id: path.join("."),
        label,
        type: "image",
        path,
        value: readString(element.data) || readString(element.prompt),
      });
    }

    if (type && type !== "text" && type !== "text-list" && type !== "image") {
      fields.push({
        decorative,
        elementType: type,
        id: path.join("."),
        label,
        type: "element",
        path,
        value: "",
      });
    }

    const childLabel =
      readString(element.component_slot) ||
      readString(element.name) ||
      parentLabel;

    if (isRecord(element.child)) {
      addElement(element.child, [...path, "child"], childLabel);
    }

    readArray(element.children).forEach((child, childIndex) => {
      addElement(child, [...path, "children", childIndex], childLabel);
    });

    readArray(element.elements).forEach((child, childIndex) => {
      addElement(child, [...path, "elements", childIndex], childLabel);
    });
  };

  const layoutRecord = layout as UnknownRecord;

  readArray(layoutRecord.elements).forEach((element, elementIndex) => {
    addElement(element, ["elements", elementIndex]);
  });

  readArray(layoutRecord.components).forEach((component, componentIndex) => {
    if (!isRecord(component)) return;
    const componentLabel =
      readString(component.component_slot) ||
      readString(component.id) ||
      readString(component.description);

    readArray(component.elements).forEach((element, elementIndex) => {
      addElement(
        element,
        ["components", componentIndex, "elements", elementIndex],
        componentLabel,
      );
    });
  });

  return fields;
}

function recordAtPath(root: unknown, path: LayoutPath) {
  let current: unknown = root;
  for (const segment of path) {
    if (typeof segment === "number") {
      if (!Array.isArray(current)) return null;
      current = current[segment];
      continue;
    }
    if (!isRecord(current)) return null;
    current = current[segment];
  }
  return isRecord(current) ? current : null;
}

export function updateLayoutSchemaField(
  layout: TemplateV2Layout,
  field: SchemaField,
  value: string,
) {
  if (field.decorative) return layout;

  const nextLayout = cloneLayout(layout);
  const element = recordAtPath(nextLayout, field.path);
  if (!element) return layout;

  if (field.type === "text") {
    updateTextRuns(element, value);
  } else if (field.type === "text-list") {
    updateTextListItems(element, value);
  } else if (field.type === "image") {
    element.data = value;
  } else {
    return layout;
  }

  return nextLayout;
}

export function updateLayoutSchemaConstraint(
  layout: TemplateV2Layout,
  field: SchemaField,
  constraint: "min" | "max",
  value: string,
) {
  if (field.decorative) return layout;

  const nextLayout = cloneLayout(layout);
  const element = recordAtPath(nextLayout, field.path);
  if (
    !element ||
    field.type === "image" ||
    field.type === "element"
  ) {
    return layout;
  }

  const numericValue = value.trim() === "" ? null : Number.parseInt(value, 10);
  const key =
    field.type === "text-list"
      ? constraint === "min"
        ? "min_item_length"
        : "max_item_length"
      : constraint === "min"
        ? "min_length"
        : "max_length";

  if (numericValue === null || !Number.isFinite(numericValue)) {
    delete element[key];
  } else {
    element[key] = Math.max(0, numericValue);
  }

  return nextLayout;
}

export function updateLayoutSchemaDecoration(
  layout: TemplateV2Layout,
  field: SchemaField,
  decorative: boolean,
) {
  const nextLayout = cloneLayout(layout);
  const element = recordAtPath(nextLayout, field.path);
  if (!element) return layout;

  element.decorative = decorative;
  return nextLayout;
}
