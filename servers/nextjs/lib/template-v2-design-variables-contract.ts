import { isTemplateV2SafeColor } from "./template-v2-render-plan.mjs";

type JsonPrimitive = null | boolean | number | string;
type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type TemplateV2DesignVariableDeclaration = {
  path: Array<string | number>;
  bindings: JsonValue[];
};

export type TemplateV2DesignVariablesContract = {
  activation: "none" | "retention-only";
  declarations: TemplateV2DesignVariableDeclaration[];
  hasBindings: boolean;
  toolbarSafe: boolean;
};

export type TemplateV2DesignVariableEditCapability =
  | {
      activation: "none" | "retention-only";
    }
  | {
      activation: "direct-option";
      variableIndex: 0;
      name: string;
      options: [string, string];
      selectedOptionIndex: 0 | 1;
      targetPath: "runs.0.font.color";
    }
  | {
      activation: "direct-option";
      variableIndex: 0;
      name: string;
      options: [number, number];
      selectedOptionIndex: 0 | 1;
      targetPath: "runs.0.font.size";
    };

export type TemplateV2ComponentDesignVariableEditCapability =
  | {
      activation: "none" | "retention-only";
    }
  | {
      activation: "direct-option";
      variableIndex: 0;
      name: "scale_variant";
      optionIndex: 0;
      targets: [
        "elements.0.size.width",
        "elements.1.runs.0.font.size",
      ];
    };

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const keys = Reflect.ownKeys(value);
  return (
    keys.length === expected.length &&
    keys.every(
      (key) => typeof key === "string" && expected.includes(key),
    )
  );
}

function cloneJsonValue(
  value: unknown,
  path: string,
  ancestors = new WeakSet<object>(),
): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      throw new Error(`template_v2_design_variables_non_json:${path}`);
    }
    return value;
  }
  if (typeof value !== "object") {
    throw new Error(`template_v2_design_variables_non_json:${path}`);
  }

  if (ancestors.has(value)) {
    throw new Error(`template_v2_design_variables_non_json:${path}`);
  }
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      const clone: JsonValue[] = [];
      for (let index = 0; index < value.length; index += 1) {
        if (!Object.prototype.hasOwnProperty.call(value, index)) {
          throw new Error(
            `template_v2_design_variables_non_json:${path}[${index}]`,
          );
        }
        clone.push(cloneJsonValue(value[index], `${path}[${index}]`, ancestors));
      }
      return clone;
    }
    if (!isPlainRecord(value)) {
      throw new Error(`template_v2_design_variables_non_json:${path}`);
    }
    const clone: Record<string, JsonValue> = {};
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== "string") {
        throw new Error(`template_v2_design_variables_non_json:${path}`);
      }
      clone[key] = cloneJsonValue(value[key], `${path}.${key}`, ancestors);
    }
    return clone;
  } finally {
    ancestors.delete(value);
  }
}

function inspectElement(
  element: Record<string, unknown>,
  path: Array<string | number>,
  declarations: TemplateV2DesignVariableDeclaration[],
): void {
  if (
    Object.prototype.hasOwnProperty.call(element, "design_variables") &&
    element.design_variables !== null
  ) {
    if (!Array.isArray(element.design_variables)) {
      throw new Error(
        `template_v2_design_variables_array_required:${path.join(".")}`,
      );
    }
    declarations.push({
      path: [...path, "design_variables"],
      bindings: element.design_variables.map((binding, index) =>
        cloneJsonValue(
          binding,
          `${path.join(".")}.design_variables[${index}]`,
        ),
      ),
    });
  }

  if (
    Object.prototype.hasOwnProperty.call(element, "child") &&
    element.child !== null
  ) {
    if (!isPlainRecord(element.child)) {
      throw new Error(
        `template_v2_design_variables_invalid_child:${path.join(".")}`,
      );
    }
    inspectElement(element.child, [...path, "child"], declarations);
  }

  if (Object.prototype.hasOwnProperty.call(element, "children")) {
    if (!Array.isArray(element.children)) {
      throw new Error(
        `template_v2_design_variables_invalid_children:${path.join(".")}`,
      );
    }
    element.children.forEach((child, childIndex) => {
      if (!isPlainRecord(child)) {
        throw new Error(
          `template_v2_design_variables_invalid_child:${[
            ...path,
            "children",
            childIndex,
          ].join(".")}`,
        );
      }
      inspectElement(
        child,
        [...path, "children", childIndex],
        declarations,
      );
    });
  }
}

function inspectDeclarationOwner(
  owner: Record<string, unknown>,
  path: Array<string | number>,
  declarations: TemplateV2DesignVariableDeclaration[],
): void {
  if (
    !Object.prototype.hasOwnProperty.call(owner, "design_variables") ||
    owner.design_variables === null
  ) {
    return;
  }
  if (!Array.isArray(owner.design_variables)) {
    throw new Error(
      `template_v2_design_variables_array_required:${path.join(".")}`,
    );
  }
  declarations.push({
    path: [...path, "design_variables"],
    bindings: owner.design_variables.map((binding, index) =>
      cloneJsonValue(
        binding,
        `${path.join(".")}.design_variables[${index}]`,
      ),
    ),
  });
}

/**
 * Inventories design-variable JSON at the strict persisted `slide.ui`
 * boundary. This is deliberately retention-only: it does not interpret or
 * apply bindings, and non-empty declarations remain unsafe for toolbar edits.
 */
export function inspectTemplateV2DesignVariablesContract(
  slide: unknown,
): TemplateV2DesignVariablesContract {
  if (!isPlainRecord(slide) || !isPlainRecord(slide.ui)) {
    throw new Error("template_v2_design_variables_slide_ui_required");
  }
  const components = slide.ui.components;
  if (!Array.isArray(components)) {
    throw new Error("template_v2_design_variables_components_required");
  }

  const declarations: TemplateV2DesignVariableDeclaration[] = [];
  components.forEach((component, componentIndex) => {
    if (!isPlainRecord(component) || !Array.isArray(component.elements)) {
      throw new Error(
        `template_v2_design_variables_elements_required:${componentIndex}`,
      );
    }
    inspectDeclarationOwner(
      component,
      ["ui", "components", componentIndex],
      declarations,
    );
    component.elements.forEach((element, elementIndex) => {
      if (!isPlainRecord(element)) {
        throw new Error(
          `template_v2_design_variables_invalid_element:${componentIndex}.${elementIndex}`,
        );
      }
      inspectElement(
        element,
        ["ui", "components", componentIndex, "elements", elementIndex],
        declarations,
      );
    });
  });

  const hasBindings = declarations.some(
    (declaration) => declaration.bindings.length > 0,
  );
  return {
    activation: hasBindings ? "retention-only" : "none",
    declarations,
    hasBindings,
    toolbarSafe: !hasBindings,
  };
}

/**
 * Enables only the smallest upstream Design Variable slice proven by the
 * pinned round-trip fixture. Everything else remains opaque retention-only
 * metadata; this gate never evaluates expressions or resolves selectors.
 */
export function inspectTemplateV2DesignVariableEditCapability(
  element: unknown,
): TemplateV2DesignVariableEditCapability {
  if (!isPlainRecord(element)) {
    return { activation: "retention-only" };
  }
  if (
    !Object.prototype.hasOwnProperty.call(element, "design_variables") ||
    element.design_variables === null
  ) {
    return { activation: "none" };
  }
  if (
    !Array.isArray(element.design_variables) ||
    element.design_variables.length !== 1 ||
    element.type !== "text" ||
    !Array.isArray(element.runs) ||
    element.runs.length !== 1
  ) {
    return { activation: "retention-only" };
  }

  const variable = element.design_variables[0];
  const run = element.runs[0];
  const font = isPlainRecord(run) ? run.font : null;
  if (
    !isPlainRecord(variable) ||
    typeof variable.name !== "string" ||
    variable.name.length === 0 ||
    !Array.isArray(variable.options) ||
    variable.options.length !== 2 ||
    variable.options[0] === variable.options[1] ||
    !Array.isArray(variable.effect) ||
    variable.effect.length !== 1 ||
    !isPlainRecord(run) ||
    !isPlainRecord(font)
  ) {
    return { activation: "retention-only" };
  }

  const effect = variable.effect[0];
  if (
    !isPlainRecord(effect) ||
    !hasExactKeys(effect, ["path", "effect"]) ||
    effect.effect !== "$"
  ) {
    return { activation: "retention-only" };
  }

  if (
    hasExactKeys(variable, ["name", "type", "options", "effect"]) &&
    variable.type === "color" &&
    variable.options.every(isTemplateV2SafeColor) &&
    effect.path === "runs.0.font.color"
  ) {
    const selectedOptionIndex = variable.options.findIndex(
      (option) => option === font.color,
    );
    if (selectedOptionIndex !== 0 && selectedOptionIndex !== 1) {
      return { activation: "retention-only" };
    }

    return {
      activation: "direct-option",
      variableIndex: 0,
      name: variable.name,
      options: [variable.options[0], variable.options[1]],
      selectedOptionIndex,
      targetPath: "runs.0.font.color",
    };
  }

  if (
    hasExactKeys(variable, ["name", "options", "effect"]) &&
    variable.name === "title_size" &&
    variable.options[0] === 24 &&
    variable.options[1] === 36 &&
    effect.path === "runs.0.font.size" &&
    font.size === 24
  ) {
    return {
      activation: "direct-option",
      variableIndex: 0,
      name: variable.name,
      options: [variable.options[0], variable.options[1]],
      selectedOptionIndex: 0,
      targetPath: "runs.0.font.size",
    };
  }

  return { activation: "retention-only" };
}

/**
 * Recognizes only the exact source/target component contract exercised by the
 * pinned upstream backend unit witness. It deliberately does not expose the
 * general expression evaluator or target-path resolver.
 */
export function inspectTemplateV2ComponentDesignVariableEditCapability(
  component: unknown,
): TemplateV2ComponentDesignVariableEditCapability {
  if (!isPlainRecord(component)) {
    return { activation: "retention-only" };
  }
  if (
    !Object.prototype.hasOwnProperty.call(component, "design_variables") ||
    component.design_variables === null
  ) {
    return { activation: "none" };
  }
  if (
    !Array.isArray(component.design_variables) ||
    component.design_variables.length !== 1 ||
    !Array.isArray(component.elements) ||
    component.elements.length !== 2
  ) {
    return { activation: "retention-only" };
  }

  const componentPosition = component.position;
  const componentSize = component.size;
  const variable = component.design_variables[0];
  const container = component.elements[0];
  const text = component.elements[1];
  const containerPosition = isPlainRecord(container)
    ? container.position
    : null;
  const containerSize = isPlainRecord(container) ? container.size : null;
  const textPosition = isPlainRecord(text) ? text.position : null;
  const textSize = isPlainRecord(text) ? text.size : null;
  if (
    component.id !== "metric_card" ||
    component.description !==
      "Reusable metric card with scalable typography." ||
    !isPlainRecord(componentPosition) ||
    !hasExactKeys(componentPosition, ["x", "y"]) ||
    componentPosition.x !== 0 ||
    componentPosition.y !== 0 ||
    !isPlainRecord(componentSize) ||
    !hasExactKeys(componentSize, ["width", "height"]) ||
    componentSize.width !== 200 ||
    componentSize.height !== 100 ||
    !isPlainRecord(variable) ||
    !hasExactKeys(variable, ["name", "type", "options", "effect"]) ||
    variable.name !== "scale_variant" ||
    variable.type !== "object" ||
    !Array.isArray(variable.options) ||
    variable.options.length !== 1 ||
    !Array.isArray(variable.effect) ||
    variable.effect.length !== 2 ||
    !isPlainRecord(container) ||
    !hasExactKeys(container, ["type", "position", "size"]) ||
    container.type !== "container" ||
    !isPlainRecord(containerPosition) ||
    !hasExactKeys(containerPosition, ["x", "y"]) ||
    containerPosition.x !== 0 ||
    containerPosition.y !== 0 ||
    !isPlainRecord(containerSize) ||
    !hasExactKeys(containerSize, ["width", "height"]) ||
    containerSize.width !== 200 ||
    containerSize.height !== 100 ||
    !isPlainRecord(text) ||
    !hasExactKeys(text, [
      "type",
      "position",
      "size",
      "fixed",
      "name",
      "min_length",
      "max_length",
      "runs",
    ]) ||
    text.type !== "text" ||
    !isPlainRecord(textPosition) ||
    !hasExactKeys(textPosition, ["x", "y"]) ||
    textPosition.x !== 20 ||
    textPosition.y !== 20 ||
    !isPlainRecord(textSize) ||
    !hasExactKeys(textSize, ["width", "height"]) ||
    textSize.width !== 160 ||
    textSize.height !== 40 ||
    text.fixed !== false ||
    text.name !== "metric" ||
    text.min_length !== 10 ||
    text.max_length !== 20 ||
    !Array.isArray(text.runs) ||
    text.runs.length !== 1
  ) {
    return { activation: "retention-only" };
  }

  const option = variable.options[0];
  const widthEffect = variable.effect[0];
  const fontSizeEffect = variable.effect[1];
  const run = text.runs[0];
  const font = isPlainRecord(run) ? run.font : null;
  if (
    !isPlainRecord(option) ||
    !hasExactKeys(option, ["width", "font_size"]) ||
    option.width !== 240 ||
    option.font_size !== 28 ||
    !isPlainRecord(widthEffect) ||
    !hasExactKeys(widthEffect, ["source", "target"]) ||
    widthEffect.source !== "$.width" ||
    widthEffect.target !== "elements.0.size.width" ||
    !isPlainRecord(fontSizeEffect) ||
    !hasExactKeys(fontSizeEffect, ["source", "target"]) ||
    fontSizeEffect.source !== "$.font_size" ||
    fontSizeEffect.target !== "elements.1.runs.0.font.size" ||
    !isPlainRecord(run) ||
    !hasExactKeys(run, ["text", "font"]) ||
    run.text !== "Metric" ||
    !isPlainRecord(font) ||
    !hasExactKeys(font, ["size"]) ||
    font.size !== 20
  ) {
    return { activation: "retention-only" };
  }

  return {
    activation: "direct-option",
    variableIndex: 0,
    name: "scale_variant",
    optionIndex: 0,
    targets: [
      "elements.0.size.width",
      "elements.1.runs.0.font.size",
    ],
  };
}

/**
 * Applies a reviewed direct option without mutating the element or its
 * Design Variable declaration. No upstream expression language is executed.
 */
export function applyTemplateV2DirectDesignVariableOption(
  element: unknown,
  selection: {
    variableIndex: number;
    optionIndex: number;
  },
): Record<string, unknown> {
  const capability = inspectTemplateV2DesignVariableEditCapability(element);
  if (capability.activation !== "direct-option" || !isPlainRecord(element)) {
    throw new Error("template_v2_design_variables_unproven_edit");
  }
  if (
    !Number.isInteger(selection.variableIndex) ||
    selection.variableIndex !== 0
  ) {
    throw new Error("template_v2_design_variables_invalid_variable_index");
  }
  if (
    !Number.isInteger(selection.optionIndex) ||
    (selection.optionIndex !== 0 && selection.optionIndex !== 1)
  ) {
    throw new Error("template_v2_design_variables_invalid_option_index");
  }

  const run = (element.runs as Record<string, unknown>[])[0]!;
  const font = run.font as Record<string, unknown>;
  const selectedOption = capability.options[selection.optionIndex];
  return {
    ...element,
    runs: [
      {
        ...run,
        font: {
          ...font,
          ...(capability.targetPath === "runs.0.font.color"
            ? { color: selectedOption }
            : { size: selectedOption }),
        },
      },
    ],
  };
}

/**
 * Applies the exact component-level source/target witness immutably. Any
 * variation remains retention-only and is rejected before mutation.
 */
export function applyTemplateV2DirectComponentDesignVariableOption(
  component: unknown,
  selection: unknown,
): Record<string, unknown> {
  const capability =
    inspectTemplateV2ComponentDesignVariableEditCapability(component);
  if (capability.activation !== "direct-option" || !isPlainRecord(component)) {
    throw new Error("template_v2_design_variables_unproven_component_edit");
  }
  if (
    !isPlainRecord(selection) ||
    !hasExactKeys(selection, ["variableIndex", "optionIndex"]) ||
    !Number.isInteger(selection.variableIndex) ||
    !Number.isInteger(selection.optionIndex)
  ) {
    throw new Error("template_v2_design_variables_invalid_selection");
  }
  if (
    selection.variableIndex !== capability.variableIndex
  ) {
    throw new Error("template_v2_design_variables_invalid_variable_index");
  }
  if (
    selection.optionIndex !== capability.optionIndex
  ) {
    throw new Error("template_v2_design_variables_invalid_option_index");
  }

  const elements = component.elements as Record<string, unknown>[];
  const container = elements[0]!;
  const text = elements[1]!;
  const size = container.size as Record<string, unknown>;
  const run = (text.runs as Record<string, unknown>[])[0]!;
  const font = run.font as Record<string, unknown>;
  return {
    ...component,
    elements: [
      {
        ...container,
        size: {
          ...size,
          width: 240,
        },
      },
      {
        ...text,
        runs: [
          {
            ...run,
            font: {
              ...font,
              size: 28,
            },
          },
        ],
      },
    ],
  };
}
