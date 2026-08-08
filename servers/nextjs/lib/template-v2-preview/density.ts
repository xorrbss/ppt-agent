import type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2-import";
import { cloneLayout, isRecord, readArray, readNumber, readString, type UnknownRecord } from "./core";
import { cellWithText, sentenceCase, setElementRunsText, setRunsTextOnValue } from "./text";

export type Density = "" | "Low" | "Medium" | "High";

type ContentDensity = Exclude<Density, "">;

const MAX_PREVIEW_ITEMS = 24;
const MAX_PREVIEW_TEXT_LENGTH = 10_000;
const DENSE_CONTENT_SENTENCES = [
  "Strategic teams need clear context, measurable progress, and practical next steps for every decision.",
  "This expanded sample copy demonstrates how the layout behaves when a field receives dense presentation content.",
  "Use this mock content to inspect wrapping, alignment, overflow, spacing, and visual balance across the slide.",
  "Each sentence adds realistic business language so longer text blocks feel close to generated presentation output.",
  "The preview fills the schema allowance to expose layout pressure before export.",
];

/**
 * Build a density preview without changing the layout that will be saved.
 * Low and High mirror the reference editor's min/max schema previews, while
 * Medium uses the midpoint between the configured bounds.
 */
export function applyTemplateContentDensity(
  layout: TemplateV2Layout,
  density: Density,
) {
  if (!density) return layout;

  const nextLayout = cloneLayout(layout);
  const layoutRecord = nextLayout as UnknownRecord;

  applyDensityToElements(layoutRecord.elements, density);
  readArray(layoutRecord.components).forEach((component) => {
    if (isRecord(component)) {
      applyDensityToElements(component.elements, density);
    }
  });

  return nextLayout;
}

function applyDensityToElements(elements: unknown, density: ContentDensity) {
  readArray(elements).forEach((element) => {
    if (isRecord(element)) applyDensityToElement(element, density);
  });
}

function applyDensityToElement(
  element: UnknownRecord,
  density: ContentDensity,
) {
  const type = readString(element.type);
  const name = readString(element.name).trim();
  const isEditableContent = element.decorative === false && Boolean(name);

  if (isEditableContent && type === "text") {
    const targetLength = densityTextLength(element, density);
    setElementRunsText(element, exactDensityText(name, targetLength, density));
  } else if (isEditableContent && type === "text-list") {
    applyTextListDensity(element, name, density);
  } else if (isEditableContent && type === "image") {
    const promptLabel = element.is_icon === true ? `${name} icon` : `${name} image`;
    element.prompt = exactDensityText(
      promptLabel,
      densityTextLength({}, density),
      density,
    );
  } else if (isEditableContent && type === "table") {
    applyTableDensity(element, name, density);
  }

  resizeRepeatedChildren(element, density);

  if (isRecord(element.child)) {
    applyDensityToElement(element.child, density);
  }
  applyDensityToElements(element.children, density);
  applyDensityToElements(element.elements, density);
}

function applyTextListDensity(
  element: UnknownRecord,
  name: string,
  density: ContentDensity,
) {
  const itemCount = densityCount(
    element.min_items,
    element.max_items,
    density,
  );
  const currentItems = readArray(element.items);
  const itemLength = densityLength(
    element.min_item_length,
    element.max_item_length,
    density,
  );

  element.items = Array.from({ length: itemCount }, (_, index) => {
    const source =
      currentItems[index] ??
      currentItems[currentItems.length - 1] ??
      currentItems[0] ??
      [{ text: "" }];
    const nextItem = cloneLayout(source);
    setRunsTextOnValue(
      nextItem,
      exactDensityText(`${name} ${index + 1}`, itemLength, density),
    );
    return nextItem;
  });
}

function applyTableDensity(
  element: UnknownRecord,
  name: string,
  density: ContentDensity,
) {
  const columnCount = densityCount(
    element.min_columns,
    element.max_columns,
    density,
  );
  const rowCount = densityCount(
    element.min_rows,
    element.max_rows,
    density,
  );
  const cellTextLength = densityTextLength({}, density);
  const currentColumns = readArray(element.columns);
  const currentRows = readArray(element.rows);

  element.columns = Array.from({ length: columnCount }, (_, columnIndex) =>
    cellWithText(
      currentColumns[columnIndex] ??
        currentColumns[currentColumns.length - 1],
      exactDensityText(
        `${name} column ${columnIndex + 1}`,
        cellTextLength,
        density,
      ),
    ),
  );
  element.rows = Array.from({ length: rowCount }, (_, rowIndex) => {
    const sourceRow = readArray(
      currentRows[rowIndex] ?? currentRows[currentRows.length - 1],
    );
    return Array.from({ length: columnCount }, (_, columnIndex) =>
      cellWithText(
        sourceRow[columnIndex] ??
          sourceRow[sourceRow.length - 1] ??
          currentColumns[columnIndex],
        exactDensityText(
          `${name} row ${rowIndex + 1} column ${columnIndex + 1}`,
          cellTextLength,
          density,
        ),
      ),
    );
  });
}

function resizeRepeatedChildren(
  element: UnknownRecord,
  density: ContentDensity,
) {
  const type = readString(element.type);
  if (type !== "flex" && type !== "grid") return;

  const children = readArray(element.children);
  const minChildren = readNumber(element.min_children);
  const maxChildren = readNumber(element.max_children);
  if (minChildren === undefined && maxChildren === undefined) return;

  const targetCount = densityCount(minChildren, maxChildren, density);
  if (targetCount === children.length) return;
  if (!childrenCanRepeat(children, maxChildren)) return;

  const nextChildren = children.slice(0, targetCount).map(cloneLayout);
  while (nextChildren.length < targetCount) {
    const index = nextChildren.length;
    const source =
      children[index] ??
      children[children.length - 1] ??
      children[0] ??
      { type: "group", children: [] };
    const nextChild = cloneLayout(source);
    normalizeRepeatedNames(nextChild, index);
    nextChildren.push(nextChild);
  }
  element.children = nextChildren;
}

function childrenCanRepeat(children: unknown[], maxChildren?: number) {
  if (children.length === 0) return false;
  if (children.length === 1) {
    return maxChildren !== undefined && maxChildren > 1;
  }

  return (["none", "numeric"] as const).some((strategy) => {
    const signatures = children.map((child) =>
      JSON.stringify(contentShape(child, strategy)),
    );
    return (
      signatures[0] !== "null" &&
      signatures.every((signature) => signature === signatures[0])
    );
  });
}

function contentShape(
  value: unknown,
  nameStrategy: "none" | "numeric",
): unknown {
  if (!isRecord(value)) return null;

  const type = readString(value.type);
  const name = normalizedContentName(readString(value.name), nameStrategy);
  if (
    value.decorative === false &&
    name &&
    ["text", "image", "text-list", "table", "chart", "infographic"].includes(
      type,
    )
  ) {
    return {
      type,
      name,
      min_length: value.min_length,
      max_length: value.max_length,
      min_items: value.min_items,
      max_items: value.max_items,
      min_item_length: value.min_item_length,
      max_item_length: value.max_item_length,
    };
  }

  const childShape = contentShape(value.child, nameStrategy);
  const childrenShapes = readArray(value.children)
    .map((child) => contentShape(child, nameStrategy))
    .filter((shape) => shape !== null);
  const elementShapes = readArray(value.elements)
    .map((child) => contentShape(child, nameStrategy))
    .filter((shape) => shape !== null);

  if (!childShape && childrenShapes.length === 0 && elementShapes.length === 0) {
    return null;
  }
  return {
    type,
    name: name || undefined,
    child: childShape || undefined,
    children: childrenShapes.length ? childrenShapes : undefined,
    elements: elementShapes.length ? elementShapes : undefined,
  };
}

function normalizedContentName(
  name: string,
  strategy: "none" | "numeric",
) {
  const trimmedName = name.trim();
  if (strategy === "none") return trimmedName;
  return trimmedName.replace(/_\d+(?=_|$)/, "").replace(/_\d+$/, "");
}

function normalizeRepeatedNames(value: unknown, index: number): void {
  if (Array.isArray(value)) {
    value.forEach((item) => normalizeRepeatedNames(item, index));
    return;
  }
  if (!isRecord(value)) return;

  if (typeof value.name === "string") {
    value.name = value.name
      .replace(/_\d+(?=_|$)/, `_${index + 1}`)
      .replace(/_\d+$/, `_${index + 1}`);
  }
  Object.values(value).forEach((item) => normalizeRepeatedNames(item, index));
}

function densityTextLength(
  schema: UnknownRecord,
  density: ContentDensity,
) {
  return densityLength(schema.min_length, schema.max_length, density);
}

function densityLength(
  minValue: unknown,
  maxValue: unknown,
  density: ContentDensity,
) {
  const minLength = nonNegativeInteger(minValue, 8);
  const maxLength = Math.max(
    minLength,
    nonNegativeInteger(maxValue, Math.max(160, minLength)),
  );
  return Math.min(
    MAX_PREVIEW_TEXT_LENGTH,
    densityValue(minLength, maxLength, density),
  );
}

function densityCount(
  minValue: unknown,
  maxValue: unknown,
  density: ContentDensity,
) {
  const minCount = nonNegativeInteger(minValue, 1);
  const maxCount = Math.max(
    minCount,
    nonNegativeInteger(maxValue, Math.max(2, minCount)),
  );
  return Math.min(
    MAX_PREVIEW_ITEMS,
    densityValue(minCount, maxCount, density),
  );
}

function densityValue(
  minValue: number,
  maxValue: number,
  density: ContentDensity,
) {
  if (density === "Low") return minValue;
  if (density === "High") return maxValue;
  return Math.round((minValue + maxValue) / 2);
}

function nonNegativeInteger(value: unknown, fallback: number) {
  const number = readNumber(value);
  return Math.max(0, Math.floor(number ?? fallback));
}

function exactDensityText(
  label: string,
  targetLength: number,
  density: ContentDensity,
) {
  if (targetLength <= 0) return "";

  const title = sentenceCase(
    label.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim() || "content",
  );
  const candidates =
    density === "Low"
      ? [
          `${title}.`,
          `${title} is ready.`,
          `${title} is clear.`,
          "Clear next step.",
          "Teams have a clear next step.",
          "The plan is ready for review.",
          "This section has enough content to validate spacing.",
        ]
      : [
          `${title} summary shows steady progress and a clear next step.`,
          ...DENSE_CONTENT_SENTENCES,
          "Stakeholders can compare options, spot risks, and align on the strongest path forward.",
          "The final wording stays deterministic while still resembling natural presentation copy.",
        ];
  const seed = candidates.join(" ").replace(/\s+/g, " ").trim();
  if (seed.length >= targetLength) return seed.slice(0, targetLength);

  const filler = ` ${DENSE_CONTENT_SENTENCES.join(" ")} ${seed}`;
  let text = seed;
  while (text.length < targetLength) text += filler;
  return text.slice(0, targetLength);
}
