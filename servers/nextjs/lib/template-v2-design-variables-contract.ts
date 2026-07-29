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
    !hasExactKeys(variable, ["name", "type", "options", "effect"]) ||
    typeof variable.name !== "string" ||
    variable.name.length === 0 ||
    variable.type !== "color" ||
    !Array.isArray(variable.options) ||
    variable.options.length !== 2 ||
    !variable.options.every(isTemplateV2SafeColor) ||
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
    effect.path !== "runs.0.font.color" ||
    effect.effect !== "$"
  ) {
    return { activation: "retention-only" };
  }

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
  return {
    ...element,
    runs: [
      {
        ...run,
        font: {
          ...font,
          color: capability.options[selection.optionIndex],
        },
      },
    ],
  };
}
