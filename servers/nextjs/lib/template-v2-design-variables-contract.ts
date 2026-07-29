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

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
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
