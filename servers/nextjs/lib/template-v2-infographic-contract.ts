type JsonPrimitive = null | boolean | number | string;
type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export type TemplateV2InfographicMetrics = {
  type: "progress_bar" | "gauge";
  minimum: number;
  maximum: number;
  value: number;
  ratio: number;
  colors: string[];
};

export type TemplateV2InfographicContract = {
  json: Record<string, JsonValue>;
  metrics: TemplateV2InfographicMetrics;
};

export type TemplateV2InfographicValuePatch = {
  path: ["data", "value"];
  value: number;
};

export type TemplateV2InfographicValuePatchResult =
  | { ok: true; value: TemplateV2InfographicValuePatch[] }
  | { ok: false; reason: string };

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
      throw new Error(`template_v2_infographic_non_json:${path}`);
    }
    return value;
  }
  if (typeof value !== "object") {
    throw new Error(`template_v2_infographic_non_json:${path}`);
  }

  if (ancestors.has(value)) {
    throw new Error(`template_v2_infographic_non_json:${path}`);
  }
  ancestors.add(value);
  try {
    if (Array.isArray(value)) {
      const clone: JsonValue[] = [];
      for (let index = 0; index < value.length; index += 1) {
        if (!Object.prototype.hasOwnProperty.call(value, index)) {
          throw new Error(
            `template_v2_infographic_non_json:${path}[${index}]`,
          );
        }
        clone.push(cloneJsonValue(value[index], `${path}[${index}]`, ancestors));
      }
      return clone;
    }
    if (!isPlainRecord(value)) {
      throw new Error(`template_v2_infographic_non_json:${path}`);
    }
    const clone: Record<string, JsonValue> = {};
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== "string") {
        throw new Error(`template_v2_infographic_non_json:${path}`);
      }
      clone[key] = cloneJsonValue(value[key], `${path}.${key}`, ancestors);
    }
    return clone;
  } finally {
    ancestors.delete(value);
  }
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/**
 * Validates the serialization and renderable numeric domain without assigning
 * palette semantics to the positional `colors` array. Unknown JSON is retained
 * exactly in the returned clone.
 */
export function validateTemplateV2InfographicContract(
  element: unknown,
): TemplateV2InfographicContract {
  if (!isPlainRecord(element)) {
    throw new Error("template_v2_infographic_object_required");
  }
  const cloned = cloneJsonValue(element, "element");
  if (!isPlainRecord(cloned) || cloned.type !== "infographic") {
    throw new Error("template_v2_infographic_type_required");
  }
  if (!isPlainRecord(cloned.data)) {
    throw new Error("template_v2_infographic_data_required");
  }

  const kind = cloned.data.type;
  if (kind !== "progress_bar" && kind !== "gauge") {
    throw new Error("template_v2_infographic_unsupported_type");
  }
  const minimum = cloned.data.min_value;
  const maximum = cloned.data.max_value;
  const value = cloned.data.value;
  if (!finiteNumber(minimum) || !finiteNumber(maximum)) {
    throw new Error("template_v2_infographic_invalid_range");
  }
  if (!(minimum < maximum)) {
    throw new Error("template_v2_infographic_invalid_range");
  }
  if (!finiteNumber(value) || value < minimum || value > maximum) {
    throw new Error("template_v2_infographic_invalid_value");
  }

  let colors: string[] = [];
  if (cloned.colors !== undefined && cloned.colors !== null) {
    if (
      !Array.isArray(cloned.colors) ||
      !cloned.colors.every((color) => typeof color === "string")
    ) {
      throw new Error("template_v2_infographic_invalid_colors");
    }
    colors = [...cloned.colors];
  }

  return {
    json: cloned,
    metrics: {
      type: kind,
      minimum,
      maximum,
      value,
      ratio: (value - minimum) / (maximum - minimum),
      colors,
    },
  };
}

/**
 * Builds the only currently editable infographic operation. The edit is
 * deliberately leaf-only so positional palette semantics and all unknown JSON
 * beside `data.value` remain untouched.
 */
export function buildTemplateV2InfographicValuePatches(
  element: unknown,
  value: unknown,
): TemplateV2InfographicValuePatchResult {
  let contract: TemplateV2InfographicContract;
  try {
    contract = validateTemplateV2InfographicContract(element);
  } catch (error) {
    return {
      ok: false,
      reason:
        error instanceof Error
          ? error.message
          : "template_v2_infographic_invalid_contract",
    };
  }

  if (!finiteNumber(value)) {
    return {
      ok: false,
      reason: "template_v2_infographic_invalid_value_edit",
    };
  }
  if (value < contract.metrics.minimum || value > contract.metrics.maximum) {
    return {
      ok: false,
      reason: "template_v2_infographic_value_edit_out_of_range",
    };
  }
  if (Object.is(value, contract.metrics.value)) {
    return { ok: true, value: [] };
  }

  const candidate = contract.json;
  if (!isPlainRecord(candidate.data)) {
    return {
      ok: false,
      reason: "template_v2_infographic_invalid_contract",
    };
  }
  candidate.data.value = value;
  try {
    validateTemplateV2InfographicContract(candidate);
  } catch (error) {
    return {
      ok: false,
      reason:
        error instanceof Error
          ? error.message
          : "template_v2_infographic_invalid_contract",
    };
  }
  return {
    ok: true,
    value: [{ path: ["data", "value"], value }],
  };
}

export function validateTemplateV2SlideUiInfographicContract(
  slide: unknown,
  componentIndex: number,
  elementIndex: number,
): TemplateV2InfographicContract {
  if (!isPlainRecord(slide) || !isPlainRecord(slide.ui)) {
    throw new Error("template_v2_infographic_slide_ui_required");
  }
  const components = slide.ui.components;
  if (!Array.isArray(components)) {
    throw new Error("template_v2_infographic_components_required");
  }
  const component = components[componentIndex];
  if (!isPlainRecord(component) || !Array.isArray(component.elements)) {
    throw new Error("template_v2_infographic_component_not_found");
  }
  const element = component.elements[elementIndex];
  if (!isPlainRecord(element)) {
    throw new Error("template_v2_infographic_element_not_found");
  }
  return validateTemplateV2InfographicContract(element);
}
