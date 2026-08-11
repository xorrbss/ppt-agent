import type {
  CanonicalTemplateAst,
  RenderPlanRepeat,
  RepeatNode,
  TemplateRenderPlan,
} from "./template-v2-ast.generated.ts";

export class TemplateV2CompilerError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "TemplateV2CompilerError";
    this.code = code;
  }
}

type JsonObject = Record<string, unknown>;

function clone<T>(value: T): T {
  return structuredClone(value);
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nameCandidates(name: string): string[] {
  const withoutNumber = name.replace(/_\d+(?=_|$)/g, "");
  const separator = withoutNumber.indexOf("_");
  const withoutPrefix = separator >= 0
    ? withoutNumber.slice(separator + 1)
    : withoutNumber;
  return [...new Set([name, withoutNumber, withoutPrefix])];
}

function scalarText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (isObject(value)) return scalarText(value.text);
  return null;
}

function firstStyledRun(element: JsonObject, text: string): JsonObject[] {
  const runs = Array.isArray(element.runs) ? element.runs : [];
  const first = isObject(runs[0]) ? clone(runs[0]) : {};
  first.text = text;
  if (first.font === undefined && isObject(element.font)) {
    first.font = clone(element.font);
  }
  return [first];
}

function tableCell(previous: unknown, value: unknown): JsonObject {
  const cell = isObject(previous) ? clone(previous) : {};
  const runs = Array.isArray(cell.runs) ? cell.runs : [];
  const first = isObject(runs[0]) ? clone(runs[0]) : {};
  first.text = scalarText(value) ?? "";
  if (first.font === undefined && isObject(cell.font)) first.font = clone(cell.font);
  cell.runs = [first];
  return cell;
}

function tableShape(columns: unknown, rows: unknown): string | null {
  if (!Array.isArray(columns) || !Array.isArray(rows)) return null;
  return `${columns.length}:${rows.map((row) => Array.isArray(row) ? row.length : -1).join(",")}`;
}

function applyTableValue(element: JsonObject, value: JsonObject): void {
  const previousColumns = Array.isArray(element.columns) ? element.columns : [];
  const previousRows = Array.isArray(element.rows) ? element.rows : [];
  const before = tableShape(previousColumns, previousRows);
  if (Array.isArray(value.columns)) {
    const fallback = previousColumns.at(-1);
    element.columns = value.columns.map((item, index) =>
      tableCell(previousColumns[index] ?? fallback, item)
    );
  }
  if (Array.isArray(value.rows)) {
    const fallbackRow = previousRows.at(-1);
    element.rows = value.rows.filter(Array.isArray).map((row, rowIndex) => {
      const previous = Array.isArray(previousRows[rowIndex])
        ? previousRows[rowIndex]
        : Array.isArray(fallbackRow) ? fallbackRow : [];
      const fallback = previous.at(-1);
      return row.map((item, columnIndex) =>
        tableCell(previous[columnIndex] ?? fallback, item)
      );
    });
  }
  if (before !== tableShape(element.columns, element.rows)) {
    for (const row of [element.columns, ...(Array.isArray(element.rows) ? element.rows : [])]) {
      if (!Array.isArray(row)) continue;
      for (const cell of row) {
        if (!isObject(cell)) continue;
        delete cell.rowspan;
        delete cell.colspan;
        delete cell.covered_by;
      }
    }
  }
}

function applyValue(element: JsonObject, value: unknown): void {
  switch (element.type) {
    case "text": {
      const text = scalarText(value);
      if (text !== null) element.runs = firstStyledRun(element, text);
      return;
    }
    case "math": {
      let latex = scalarText(value)?.trim() ?? "";
      if (latex.startsWith("$$") && latex.endsWith("$$") && latex.length > 4) {
        latex = latex.slice(2, -2).trim();
      } else if (latex.startsWith("\\[") && latex.endsWith("\\]") && latex.length > 4) {
        latex = latex.slice(2, -2).trim();
      }
      if (latex) element.latex = latex.slice(0, 4000);
      return;
    }
    case "text-list":
      if (Array.isArray(value)) {
        const previous = Array.isArray(element.items) ? element.items : [];
        element.items = value.map((item, index) => {
          const candidate = previous[index];
          const run = Array.isArray(candidate) && isObject(candidate[0])
            ? clone(candidate[0])
            : {};
          run.text = scalarText(item) ?? "";
          if (run.font === undefined && isObject(element.font)) run.font = clone(element.font);
          return [run];
        });
      }
      return;
    case "image":
      if (typeof value === "string") element.data = value;
      else if (isObject(value)) {
        const url = ["data", "url", "image_url", "icon_url", "__image_url__", "__icon_url__"]
          .map((key) => value[key])
          .find((candidate) => typeof candidate === "string" && candidate.trim());
        if (typeof url === "string") element.data = url.trim();
      }
      return;
    case "table":
      if (isObject(value)) applyTableValue(element, value);
      return;
    case "chart":
      if (isObject(value)) {
        for (const key of [
          "chart_type", "title", "title_color", "legend_color", "colors",
          "x_axis", "y_axis", "x_axis_title", "y_axis_title", "axis_color",
          "categories", "series", "data_labels", "legend", "x_axis_grid",
          "y_axis_grid", "grid_color", "source",
        ]) if (value[key] !== undefined) element[key] = clone(value[key]);
      }
      return;
    case "infographic":
      if (isObject(value)) {
        if (isObject(value.data)) element.data = clone(value.data);
        if (Array.isArray(value.colors)) element.colors = clone(value.colors);
      }
  }
}

function applyElements(
  elements: unknown,
  content: unknown,
  occurrences = new Map<string, number>(),
  direct = false,
): void {
  if (!Array.isArray(elements)) return;
  const values = isObject(content) ? content : {};
  for (const candidate of elements) {
    if (!isObject(candidate)) continue;
    const name = typeof candidate.name === "string" ? candidate.name : null;
    let found = false;
    let value: unknown;
    if (name !== null) {
      const occurrence = occurrences.get(name) ?? 0;
      occurrences.set(name, occurrence + 1);
      const keys = occurrence > 0
        ? [`${name}_${occurrence + 1}`, ...nameCandidates(name)]
        : nameCandidates(name);
      const key = keys.find((item) => Object.hasOwn(values, item));
      if (key !== undefined) {
        found = true;
        value = values[key];
      }
    }
    const generated = [
      "text", "math", "image", "text-list", "table", "chart", "infographic",
    ].includes(String(candidate.type));
    if (candidate.decorative === false && generated && (found || direct)) {
      applyValue(candidate, found ? value : content);
      continue;
    }
    const nested = found && isObject(value) ? value : values;
    if (isObject(candidate.child)) {
      applyElements([candidate.child], nested, new Map(), direct && !found);
    }
    if (Array.isArray(candidate.children)) {
      const children = candidate.children;
      if (found && Array.isArray(value) && children.length > 0) {
        candidate.children = value.map((item, index) => {
          const source = clone(children[Math.min(index, children.length - 1)]);
          applyElements([source], item, new Map(), true);
          return source;
        });
      } else {
        applyElements(candidate.children, nested, occurrences, direct && !found);
      }
    }
  }
}

function hydrateLayoutContent(layout: JsonObject, content: JsonObject): void {
  if (!Array.isArray(layout.components)) return;
  const totals = new Map<string, number>();
  for (const component of layout.components) {
    if (!isObject(component)) continue;
    const id = String(component.id ?? "");
    totals.set(id, (totals.get(id) ?? 0) + 1);
  }
  const seen = new Map<string, number>();
  const used = new Set<string>();
  for (const component of layout.components) {
    if (!isObject(component)) continue;
    const id = String(component.id ?? "");
    const occurrence = seen.get(id) ?? 0;
    seen.set(id, occurrence + 1);
    const keys = (totals.get(id) ?? 0) > 1 ? [`${id}_${occurrence}`, id] : [id];
    const key = keys.find((candidate) => !used.has(candidate) && isObject(content[candidate]));
    if (key === undefined) continue;
    used.add(key);
    applyElements(component.elements, content[key]);
  }
}

function selectedSources(repeat: RepeatNode, count: number): JsonObject[] {
  if (count < repeat.min_items || count > repeat.max_items) {
    throw new TemplateV2CompilerError(
      `template_repeat_count_out_of_bounds:${repeat.repeat_id}:${count}:${repeat.min_items}:${repeat.max_items}`,
    );
  }
  if (repeat.selection === "prototype") {
    if (!isObject(repeat.prototype)) {
      throw new TemplateV2CompilerError("template_repeat_prototype_missing");
    }
    return Array.from({ length: count }, () => clone(repeat.prototype!));
  }
  return (repeat.anchors ?? []).slice(0, count).map((anchor) => ({
    ...clone(anchor.source),
    __canonical_source_index: anchor.source_index,
  }));
}

export function compileTemplateRenderPlan(
  ast: CanonicalTemplateAst,
  layoutId: string,
  content: JsonObject,
): TemplateRenderPlan {
  if (
    ast.schema_version !== "presenton.template-ast/v1" ||
    ast.capabilities.review_required === true ||
    ast.capabilities.hydration !== true
  ) {
    throw new TemplateV2CompilerError("template_ast_review_required");
  }
  const layout = ast.layouts.find((candidate) => candidate.layout_id === layoutId);
  if (!layout) {
    throw new TemplateV2CompilerError(`template_ast_layout_not_found:${layoutId}`);
  }
  const repeats: RenderPlanRepeat[] = [];
  for (const component of layout.components) {
    const candidate = content[component.component_id];
    const componentContent = isObject(candidate) ? candidate : {};
    for (const repeat of component.repeats ?? []) {
      const values = componentContent[repeat.content_field];
      const count = Array.isArray(values) ? values.length : repeat.max_items;
      repeats.push({
        repeat_id: repeat.repeat_id,
        component_id: component.component_id,
        source_path: repeat.source_path,
        content_field: repeat.content_field,
        arrangement: repeat.arrangement,
        selected_count: count,
        source_indices: [...repeat.source_indices],
        selected_sources: selectedSources(repeat, count),
      });
    }
  }
  return {
    schema_version: "presenton.template-render-plan/v1",
    ast_schema_version: "presenton.template-ast/v1",
    layout_id: layout.layout_id,
    source_layout: clone(layout.source),
    content: clone(content),
    repeats,
    capabilities: clone(ast.capabilities),
  };
}

function componentFor(layout: JsonObject, componentId: string): JsonObject {
  const components = layout.components;
  if (Array.isArray(components)) {
    const component = components.find(
      (candidate) => isObject(candidate) && String(candidate.id) === componentId,
    );
    if (isObject(component)) return component;
  }
  throw new TemplateV2CompilerError(
    `template_render_component_not_found:${componentId}`,
  );
}

function relativePath(sourcePath: string): string[] {
  const marker = ".components.";
  const markerIndex = sourcePath.indexOf(marker);
  if (markerIndex < 0) {
    throw new TemplateV2CompilerError(`template_render_path_invalid:${sourcePath}`);
  }
  return sourcePath.slice(markerIndex + marker.length).split(".").slice(1);
}

function parentFor(root: JsonObject, path: string[]): [JsonObject, string] {
  if (path.length === 0) {
    throw new TemplateV2CompilerError("template_render_path_empty");
  }
  let current: unknown = root;
  for (const segment of path.slice(0, -1)) {
    if (isObject(current)) current = current[segment];
    else if (Array.isArray(current) && /^\d+$/.test(segment)) {
      current = current[Number(segment)];
    } else current = undefined;
    if (current === undefined) {
      throw new TemplateV2CompilerError("template_render_path_unresolvable");
    }
  }
  if (!isObject(current)) {
    throw new TemplateV2CompilerError("template_render_parent_invalid");
  }
  return [current, path.at(-1)!];
}

export function materializeTemplateRenderPlan(
  plan: TemplateRenderPlan,
): JsonObject {
  const layout = clone(plan.source_layout);
  for (const repeat of plan.repeats) {
    const component = componentFor(layout, repeat.component_id);
    const [parent, key] = parentFor(component, relativePath(repeat.source_path));
    const current = parent[key];
    if (!Array.isArray(current)) {
      throw new TemplateV2CompilerError(
        `template_render_plan_target_invalid:${repeat.repeat_id}`,
      );
    }
    const componentCandidate: unknown = plan.content[repeat.component_id];
    const componentContent: JsonObject = isObject(componentCandidate)
      ? componentCandidate
      : {};
    const repeatValues = componentContent[repeat.content_field];
    if (repeat.arrangement !== "positioned") {
      parent[key] = repeat.selected_sources.map((selectedSource, index) => {
        const source = clone(selectedSource);
        if (Array.isArray(repeatValues) && index < repeatValues.length) {
          applyElements([source], repeatValues[index], new Map(), true);
        }
        return source;
      });
      continue;
    }
    const selected = new Map<number, JsonObject>();
    for (const source of repeat.selected_sources) {
      const sourceIndex = source.__canonical_source_index;
      if (typeof sourceIndex === "number") selected.set(sourceIndex, source);
    }
    const allIndexes = new Set(repeat.source_indices);
    const replacement: unknown[] = [];
    let selectedIndex = 0;
    current.forEach((item, index) => {
      if (!allIndexes.has(index)) {
        replacement.push(item);
        return;
      }
      const chosen = selected.get(index);
      if (chosen) {
        const clean = clone(chosen);
        delete clean.__canonical_source_index;
        if (Array.isArray(repeatValues) && selectedIndex < repeatValues.length) {
          applyElements(
            [clean],
            repeatValues[selectedIndex],
            new Map(),
            true,
          );
        }
        replacement.push(clean);
        selectedIndex += 1;
      }
    });
    parent[key] = replacement;
  }
  hydrateLayoutContent(layout, plan.content);
  return layout;
}
