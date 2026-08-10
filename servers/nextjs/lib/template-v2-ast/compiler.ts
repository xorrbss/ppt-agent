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
    if (repeat.arrangement !== "positioned") {
      parent[key] = clone(repeat.selected_sources);
      continue;
    }
    const selected = new Map<number, JsonObject>();
    for (const source of repeat.selected_sources) {
      const sourceIndex = source.__canonical_source_index;
      if (typeof sourceIndex === "number") selected.set(sourceIndex, source);
    }
    const allIndexes = new Set(repeat.source_indices);
    const replacement: unknown[] = [];
    current.forEach((item, index) => {
      if (!allIndexes.has(index)) {
        replacement.push(item);
        return;
      }
      const chosen = selected.get(index);
      if (chosen) {
        const clean = clone(chosen);
        delete clean.__canonical_source_index;
        replacement.push(clean);
      }
    });
    parent[key] = replacement;
  }
  return layout;
}
