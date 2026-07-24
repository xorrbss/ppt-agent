const CANVAS = Object.freeze({ width: 1280, height: 720 });

export const TEMPLATE_V2_PLAN_ELEMENT_TYPES = Object.freeze([
  "text",
  "container",
  "image",
  "text-list",
  "table",
  "vector",
  "chart",
  "infographic",
  "flex",
  "grid",
  "group",
]);

export const TEMPLATE_V2_PLAN_CHART_TYPES = Object.freeze([
  "area",
  "bar",
  "bubble",
  "donut",
  "horizontal_bar",
  "horizontal_stacked_bar",
  "line",
  "pie",
  "polar_area",
  "radar",
  "scatter",
  "stacked_bar",
]);

const CHART_TYPES = new Set(TEMPLATE_V2_PLAN_CHART_TYPES);
const ALIGNMENTS = new Set(["flex-start", "flex-end", "center", "stretch"]);
const INFOGRAPHIC_TYPES = new Set(["progress_bar", "gauge"]);
const HORIZONTAL_ALIGNMENTS = new Set(["left", "center", "right"]);
const VERTICAL_ALIGNMENTS = new Set(["top", "middle", "bottom"]);
const IMAGE_FITS = new Set(["contain", "cover", "fill"]);
const DATA_LABEL_POSITIONS = new Set(["base", "mid", "top", "outside"]);
const AXIS_CHART_TYPES = new Set([
  "area",
  "bar",
  "horizontal_bar",
  "horizontal_stacked_bar",
  "line",
  "stacked_bar",
]);

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function fail(code, path) {
  throw new Error(`${code}:${path}`);
}

function optionalFinite(value, code, path) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) fail(code, path);
  return value;
}

function optionalRecord(value, code, path) {
  if (value === undefined || value === null) return {};
  if (!isRecord(value)) fail(code, path);
  return value;
}

function optionalBoolean(value, code, path) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "boolean") fail(code, path);
  return value;
}

function optionalString(value, code, path) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") fail(code, path);
  return value;
}

function boundedFinite(value, minimum, maximum, code, path, fallback = null) {
  const resolved = optionalFinite(value, code, path);
  if (resolved === null) return fallback;
  if (resolved < minimum || resolved > maximum) fail(code, path);
  return resolved;
}

function safeColor(value, code, path, fallback = null) {
  const resolved = optionalString(value, code, path);
  if (resolved === null) return fallback;
  const colorPattern =
    /^(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([0-9.,%+\-\s]+\)|hsla?\([0-9a-zA-Z.,%+\-\s]+\))$/;
  if (resolved.length > 96 || !colorPattern.test(resolved)) fail(code, path);
  return resolved;
}

function planShadow(value, path) {
  if (value === undefined || value === null) return null;
  const shadow = optionalRecord(value, "template_v2_render_plan_invalid_shadow", path);
  const color = safeColor(
      shadow.color,
      "template_v2_render_plan_invalid_shadow_color",
      `${path}.color`
    );
  if (color === null) {
    fail("template_v2_render_plan_invalid_shadow_color", `${path}.color`);
  }
  return {
    color,
    blur: boundedFinite(
      shadow.blur,
      0,
      1000,
      "template_v2_render_plan_invalid_shadow_blur",
      `${path}.blur`,
      0
    ),
    opacity: boundedFinite(
      shadow.opacity,
      0,
      1,
      "template_v2_render_plan_invalid_shadow_opacity",
      `${path}.opacity`,
      1
    ),
    offsetX:
      optionalFinite(
        shadow.offset_x,
        "template_v2_render_plan_invalid_shadow_offset",
        `${path}.offset_x`
      ) ?? 0,
    offsetY:
      optionalFinite(
        shadow.offset_y,
        "template_v2_render_plan_invalid_shadow_offset",
        `${path}.offset_y`
      ) ?? 0,
  };
}

function planStroke(value, path) {
  if (value === undefined || value === null) return null;
  const stroke = optionalRecord(value, "template_v2_render_plan_invalid_stroke", path);
  if (stroke.dash !== undefined && stroke.dash !== null && stroke.dash.length !== 0) {
    fail("template_v2_render_plan_unsupported_stroke_dash", `${path}.dash`);
  }
  if (
    stroke.dash !== undefined &&
    stroke.dash !== null &&
    !Array.isArray(stroke.dash)
  ) {
    fail("template_v2_render_plan_invalid_stroke_dash", `${path}.dash`);
  }
  const color = safeColor(
      stroke.color,
      "template_v2_render_plan_invalid_stroke_color",
      `${path}.color`
    );
  if (color === null) {
    fail("template_v2_render_plan_invalid_stroke_color", `${path}.color`);
  }
  const width = boundedFinite(
      stroke.width,
      0,
      1000,
      "template_v2_render_plan_invalid_stroke_width",
      `${path}.width`
    );
  if (width === null) {
    fail("template_v2_render_plan_invalid_stroke_width", `${path}.width`);
  }
  return {
    color,
    width,
    opacity: boundedFinite(
      stroke.opacity,
      0,
      1,
      "template_v2_render_plan_invalid_stroke_opacity",
      `${path}.opacity`,
      1
    ),
  };
}

function planElementAlignment(value, path) {
  if (value === undefined || value === null) return null;
  const alignmentValue = optionalRecord(
    value,
    "template_v2_render_plan_invalid_element_alignment",
    path
  );
  const horizontal = alignmentValue.horizontal ?? null;
  const vertical = alignmentValue.vertical ?? null;
  if (horizontal !== null && !HORIZONTAL_ALIGNMENTS.has(horizontal)) {
    fail("template_v2_render_plan_invalid_element_alignment", `${path}.horizontal`);
  }
  if (vertical !== null && !VERTICAL_ALIGNMENTS.has(vertical)) {
    fail("template_v2_render_plan_invalid_element_alignment", `${path}.vertical`);
  }
  return { horizontal, vertical };
}

function normalizeClipPath(value, path) {
  const resolved = optionalString(
    value,
    "template_v2_render_plan_invalid_image_clip_path",
    path
  );
  if (resolved === null || resolved === "none") return resolved;
  if (
    resolved.length > 1024 ||
    /[";{}<>\\]/.test(resolved) ||
    !/^(?:polygon|circle|ellipse|inset)\([0-9a-zA-Z%.,+\-\s/]+\)$/.test(resolved)
  ) {
    fail("template_v2_render_plan_unsupported_image_clip_path", path);
  }
  return resolved;
}

function planText(element, path) {
  return {
    alignment: planElementAlignment(element.alignment, `${path}.alignment`),
    stroke: planStroke(element.stroke, `${path}.stroke`),
    shadow: planShadow(element.shadow, `${path}.shadow`),
  };
}

function planContainer(element, path) {
  return {
    alignment: planElementAlignment(element.alignment, `${path}.alignment`),
    stroke: planStroke(element.stroke, `${path}.stroke`),
    shadow: planShadow(element.shadow, `${path}.shadow`),
  };
}

function planImage(element, path) {
  const fit = element.fit ?? "fill";
  if (!IMAGE_FITS.has(fit)) {
    fail("template_v2_render_plan_invalid_image_fit", `${path}.fit`);
  }
  const isIcon =
    optionalBoolean(
      element.is_icon,
      "template_v2_render_plan_invalid_image_icon",
      `${path}.is_icon`
    ) ?? false;
  const color = safeColor(
    element.color,
    "template_v2_render_plan_invalid_image_color",
    `${path}.color`
  );
  if (color !== null && !isIcon) {
    fail("template_v2_render_plan_unsupported_image_color", `${path}.color`);
  }
  const cropScale = boundedFinite(
    element.crop_scale,
    1,
    6,
    "template_v2_render_plan_invalid_image_crop_scale",
    `${path}.crop_scale`,
    1
  );
  if (color !== null && cropScale !== 1) {
    fail("template_v2_render_plan_unsupported_icon_crop", `${path}.crop_scale`);
  }
  return {
    fit,
    flipH:
      optionalBoolean(
        element.flip_h,
        "template_v2_render_plan_invalid_image_flip",
        `${path}.flip_h`
      ) ?? false,
    flipV:
      optionalBoolean(
        element.flip_v,
        "template_v2_render_plan_invalid_image_flip",
        `${path}.flip_v`
      ) ?? false,
    opacity: boundedFinite(
      element.opacity,
      0,
      1,
      "template_v2_render_plan_invalid_image_opacity",
      `${path}.opacity`,
      1
    ),
    focusX: boundedFinite(
      element.focus_x,
      0,
      100,
      "template_v2_render_plan_invalid_image_focus",
      `${path}.focus_x`,
      50
    ),
    focusY: boundedFinite(
      element.focus_y,
      0,
      100,
      "template_v2_render_plan_invalid_image_focus",
      `${path}.focus_y`,
      50
    ),
    cropScale,
    clipPath: normalizeClipPath(element.clip_path, `${path}.clip_path`),
    color,
    isIcon,
  };
}

function point(value, path) {
  if (!isRecord(value)) fail("template_v2_render_plan_invalid_vector_point", path);
  const x = optionalFinite(value.x, "template_v2_render_plan_invalid_vector_point", `${path}.x`);
  const y = optionalFinite(value.y, "template_v2_render_plan_invalid_vector_point", `${path}.y`);
  if (x === null || y === null) fail("template_v2_render_plan_invalid_vector_point", path);
  return { x, y };
}

function bounds(points) {
  const xs = points.map(({ x }) => x);
  const ys = points.map(({ y }) => y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const maxX = Math.max(...xs);
  const maxY = Math.max(...ys);
  return {
    x: minX,
    y: minY,
    width: Math.max(1, maxX - minX),
    height: Math.max(1, maxY - minY),
  };
}

function samePoint(left, right) {
  return left.x === right.x && left.y === right.y;
}

export function sampleTemplateV2SmoothPoints(
  inputPoints,
  { closed = false, tension = 0.5, segments = 16, path = "vector" } = {}
) {
  if (!Array.isArray(inputPoints) || inputPoints.length < 2) {
    fail("template_v2_render_plan_vector_points_required", path);
  }
  const points = inputPoints.map((value, index) => point(value, `${path}.points.${index}`));
  const resolvedTension = optionalFinite(
    tension,
    "template_v2_render_plan_invalid_vector_tension",
    `${path}.curve.tension`
  );
  if (resolvedTension === null || resolvedTension < 0 || resolvedTension > 1) {
    fail("template_v2_render_plan_invalid_vector_tension", `${path}.curve.tension`);
  }
  if (!Number.isInteger(segments) || segments < 1 || segments > 96) {
    fail("template_v2_render_plan_invalid_vector_segments", `${path}.curve.segments`);
  }
  if (points.length < 3) return points;

  const sampled = [];
  const edgeCount = closed ? points.length : points.length - 1;
  for (let index = 0; index < edgeCount; index += 1) {
    const p1 = points[index];
    const p2 = points[(index + 1) % points.length];
    const p0 = closed
      ? points[(index - 1 + points.length) % points.length]
      : points[Math.max(0, index - 1)];
    const p3 = closed
      ? points[(index + 2) % points.length]
      : points[Math.min(points.length - 1, index + 2)];
    const m1 = {
      x: ((p2.x - p0.x) * resolvedTension) / 2,
      y: ((p2.y - p0.y) * resolvedTension) / 2,
    };
    const m2 = {
      x: ((p3.x - p1.x) * resolvedTension) / 2,
      y: ((p3.y - p1.y) * resolvedTension) / 2,
    };

    for (let step = 0; step < segments; step += 1) {
      const t = step / segments;
      const t2 = t * t;
      const t3 = t2 * t;
      sampled.push({
        x:
          (2 * t3 - 3 * t2 + 1) * p1.x +
          (t3 - 2 * t2 + t) * m1.x +
          (-2 * t3 + 3 * t2) * p2.x +
          (t3 - t2) * m2.x,
        y:
          (2 * t3 - 3 * t2 + 1) * p1.y +
          (t3 - 2 * t2 + t) * m1.y +
          (-2 * t3 + 3 * t2) * p2.y +
          (t3 - t2) * m2.y,
      });
    }
  }
  sampled.push(closed ? { ...sampled[0] } : { ...points.at(-1) });
  return sampled;
}

function planVector(element, path) {
  if (!Array.isArray(element.points) || element.points.length < 2) {
    fail("template_v2_render_plan_vector_points_required", path);
  }
  const rawPoints = element.points.map((value, index) => point(value, `${path}.points.${index}`));
  if (element.shape !== undefined && !["polygon", "ellipse"].includes(element.shape)) {
    fail("template_v2_render_plan_invalid_vector_shape", `${path}.shape`);
  }
  if (element.shape === "ellipse" && rawPoints.length !== 2) {
    fail("template_v2_render_plan_ellipse_bounding_pair_required", path);
  }
  const closed = element.closed ?? element.shape === "polygon";
  const curve = optionalRecord(
    element.curve,
    "template_v2_render_plan_invalid_vector_curve",
    `${path}.curve`
  );
  if (element.curve !== undefined && curve.type !== "smooth") {
    fail("template_v2_render_plan_invalid_vector_curve", `${path}.curve.type`);
  }
  const tension = curve.tension ?? 0.5;
  const segments = curve.segments ?? 16;
  const sampledPoints = element.curve
    ? sampleTemplateV2SmoothPoints(rawPoints, { closed, tension, segments, path })
    : rawPoints;
  const geometryBounds = bounds(sampledPoints);
  return {
    shape: element.shape ?? null,
    closed: Boolean(closed),
    tension: element.curve ? tension : null,
    segments: element.curve ? segments : null,
    frame: geometryBounds,
    points: sampledPoints.map(({ x, y }) => ({
      x: x - geometryBounds.x,
      y: y - geometryBounds.y,
    })),
  };
}

function stringArray(value, code, path) {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    fail(code, path);
  }
  return [...value];
}

function planChart(element, path) {
  if (typeof element.chart_type !== "string" || !CHART_TYPES.has(element.chart_type)) {
    fail("template_v2_render_plan_unsupported_chart_type", `${path}.chart_type`);
  }
  const categories = stringArray(
    element.categories,
    "template_v2_render_plan_invalid_chart_categories",
    `${path}.categories`
  );
  const colors =
    stringArray(
      element.colors,
      "template_v2_render_plan_invalid_chart_colors",
      `${path}.colors`
    ) ?? [];
  if (element.series !== undefined && element.series !== null && !Array.isArray(element.series)) {
    fail("template_v2_render_plan_chart_series_required", `${path}.series`);
  }
  const series = (element.series ?? []).map((value, index) => {
    const seriesPath = `${path}.series.${index}`;
    if (!isRecord(value) || typeof value.name !== "string" || !Array.isArray(value.values)) {
      fail("template_v2_render_plan_invalid_chart_series", seriesPath);
    }
    const values = value.values.map((item, valueIndex) => {
      const resolved = optionalFinite(
        item,
        "template_v2_render_plan_invalid_chart_value",
        `${seriesPath}.values.${valueIndex}`
      );
      if (resolved === null) {
        fail("template_v2_render_plan_invalid_chart_value", `${seriesPath}.values.${valueIndex}`);
      }
      return resolved;
    });
    if (categories && values.length !== categories.length) {
      fail("template_v2_render_plan_chart_category_mismatch", seriesPath);
    }
    return { name: value.name, values };
  });
  if (["pie", "donut"].includes(element.chart_type) && series.length > 1) {
    fail("template_v2_render_plan_chart_series_limit", path);
  }
  const dataLabels = optionalString(
    element.data_labels,
    "template_v2_render_plan_invalid_chart_data_labels",
    `${path}.data_labels`
  );
  if (dataLabels !== null && !DATA_LABEL_POSITIONS.has(dataLabels)) {
    fail("template_v2_render_plan_invalid_chart_data_labels", `${path}.data_labels`);
  }
  const hasAxisOptions = [
    "x_axis",
    "y_axis",
    "x_axis_title",
    "y_axis_title",
    "axis_color",
    "x_axis_grid",
    "y_axis_grid",
    "grid_color",
  ].some((key) => element[key] !== undefined && element[key] !== null);
  if (hasAxisOptions && !AXIS_CHART_TYPES.has(element.chart_type)) {
    fail("template_v2_render_plan_unsupported_chart_axes", path);
  }
  if (dataLabels !== null && !AXIS_CHART_TYPES.has(element.chart_type)) {
    fail("template_v2_render_plan_unsupported_chart_data_labels", `${path}.data_labels`);
  }
  if (
    dataLabels !== null &&
    ["line", "area"].includes(element.chart_type) &&
    !["top", "outside"].includes(dataLabels)
  ) {
    fail("template_v2_render_plan_unsupported_chart_data_labels", `${path}.data_labels`);
  }
  const horizontal = ["horizontal_bar", "horizontal_stacked_bar"].includes(
    element.chart_type
  );
  const stacked = ["stacked_bar", "horizontal_stacked_bar"].includes(
    element.chart_type
  );
  return {
    type: element.chart_type,
    categories: categories ?? [],
    series,
    colors: colors.map((color, index) =>
      safeColor(
        color,
        "template_v2_render_plan_invalid_chart_color",
        `${path}.colors.${index}`
      )
    ),
    title: optionalString(
      element.title,
      "template_v2_render_plan_invalid_chart_title",
      `${path}.title`
    ),
    titleColor: safeColor(
      element.title_color,
      "template_v2_render_plan_invalid_chart_color",
      `${path}.title_color`,
      "#111827"
    ),
    legendColor: safeColor(
      element.legend_color,
      "template_v2_render_plan_invalid_chart_color",
      `${path}.legend_color`,
      "#374151"
    ),
    axisColor: safeColor(
      element.axis_color,
      "template_v2_render_plan_invalid_chart_color",
      `${path}.axis_color`,
      "#6b7280"
    ),
    gridColor: safeColor(
      element.grid_color,
      "template_v2_render_plan_invalid_chart_color",
      `${path}.grid_color`,
      "#e5e7eb"
    ),
    xAxis:
      optionalBoolean(
        element.x_axis,
        "template_v2_render_plan_invalid_chart_axis",
        `${path}.x_axis`
      ) ?? false,
    yAxis:
      optionalBoolean(
        element.y_axis,
        "template_v2_render_plan_invalid_chart_axis",
        `${path}.y_axis`
      ) ?? false,
    xAxisTitle: optionalString(
      element.x_axis_title,
      "template_v2_render_plan_invalid_chart_axis_title",
      `${path}.x_axis_title`
    ),
    yAxisTitle: optionalString(
      element.y_axis_title,
      "template_v2_render_plan_invalid_chart_axis_title",
      `${path}.y_axis_title`
    ),
    xAxisGrid:
      optionalBoolean(
        element.x_axis_grid,
        "template_v2_render_plan_invalid_chart_grid",
        `${path}.x_axis_grid`
      ) ?? false,
    yAxisGrid:
      optionalBoolean(
        element.y_axis_grid,
        "template_v2_render_plan_invalid_chart_grid",
        `${path}.y_axis_grid`
      ) ?? false,
    dataLabels,
    legend:
      optionalBoolean(
        element.legend,
        "template_v2_render_plan_invalid_chart_legend",
        `${path}.legend`
      ) ?? false,
    source: optionalString(
      element.source,
      "template_v2_render_plan_invalid_chart_source",
      `${path}.source`
    ),
    horizontal,
    stacked,
  };
}

function planInfographic(element, path) {
  if (!isRecord(element.data) || !INFOGRAPHIC_TYPES.has(element.data.type)) {
    fail("template_v2_render_plan_unsupported_infographic", `${path}.data`);
  }
  const minimum = optionalFinite(
    element.data.min_value,
    "template_v2_render_plan_invalid_infographic_range",
    `${path}.data.min_value`
  );
  const maximum = optionalFinite(
    element.data.max_value,
    "template_v2_render_plan_invalid_infographic_range",
    `${path}.data.max_value`
  );
  const value = optionalFinite(
    element.data.value,
    "template_v2_render_plan_invalid_infographic_value",
    `${path}.data.value`
  );
  if (minimum === null || maximum === null || minimum >= maximum) {
    fail("template_v2_render_plan_invalid_infographic_range", `${path}.data`);
  }
  if (value === null || value < minimum || value > maximum) {
    fail("template_v2_render_plan_invalid_infographic_value", `${path}.data.value`);
  }
  const colors =
    stringArray(
      element.colors,
      "template_v2_render_plan_invalid_infographic_colors",
      `${path}.colors`
    ) ?? [];
  return {
    type: element.data.type,
    minimum,
    maximum,
    value,
    ratio: (value - minimum) / (maximum - minimum),
    colors,
  };
}

function baseFrame(element, path, override, vector) {
  if (override) return { ...override };
  if (vector) return { ...vector.frame };
  const position = optionalRecord(
    element.position,
    "template_v2_render_plan_invalid_position",
    `${path}.position`
  );
  const size = optionalRecord(
    element.size,
    "template_v2_render_plan_invalid_size",
    `${path}.size`
  );
  return {
    x: optionalFinite(position.x, "template_v2_render_plan_invalid_position", `${path}.position.x`) ?? 0,
    y: optionalFinite(position.y, "template_v2_render_plan_invalid_position", `${path}.position.y`) ?? 0,
    width: optionalFinite(size.width, "template_v2_render_plan_invalid_size", `${path}.size.width`),
    height: optionalFinite(size.height, "template_v2_render_plan_invalid_size", `${path}.size.height`),
  };
}

function rawSize(element, path) {
  if (element.type === "vector") {
    const geometry = planVector(element, path);
    return { width: geometry.frame.width, height: geometry.frame.height };
  }
  const size = optionalRecord(
    element.size,
    "template_v2_render_plan_invalid_size",
    `${path}.size`
  );
  return {
    width: optionalFinite(size.width, "template_v2_render_plan_invalid_size", `${path}.size.width`),
    height: optionalFinite(size.height, "template_v2_render_plan_invalid_size", `${path}.size.height`),
  };
}

function layoutGap(element, key, path) {
  const value = optionalFinite(
    element[key],
    "template_v2_render_plan_invalid_layout_gap",
    `${path}.${key}`
  );
  if (value !== null && value < 0) {
    fail("template_v2_render_plan_invalid_layout_gap", `${path}.${key}`);
  }
  return value;
}

function alignment(value, fallback, path) {
  if (value === undefined || value === null) return fallback;
  if (!ALIGNMENTS.has(value)) fail("template_v2_render_plan_invalid_alignment", path);
  return value;
}

function alignedOffset(alignmentValue, available) {
  if (alignmentValue === "flex-end") return available;
  if (alignmentValue === "center") return available / 2;
  return 0;
}

function flexPlacements(element, frame, path) {
  if (!Array.isArray(element.children)) {
    fail("template_v2_render_plan_flex_children_required", `${path}.children`);
  }
  if (!["row", "column"].includes(element.direction)) {
    fail("template_v2_render_plan_invalid_flex_direction", `${path}.direction`);
  }
  const row = element.direction === "row";
  const gap = layoutGap(element, "gap", path) ?? 0;
  const mainGap = layoutGap(element, row ? "column_gap" : "row_gap", path) ?? gap;
  const crossGap = layoutGap(element, row ? "row_gap" : "column_gap", path) ?? gap;
  const alignItems = alignment(element.align_items, "stretch", `${path}.align_items`);
  const justifyContent = alignment(
    element.justify_content,
    "flex-start",
    `${path}.justify_content`
  );
  const items = element.children.map((child, index) => {
    const size = rawSize(child, `${path}.children.${index}`);
    return {
      index,
      main: (row ? size.width : size.height) ?? 0,
      cross: row ? size.height : size.width,
    };
  });
  const explicitMain = row ? frame.width : frame.height;
  const lines = [];
  let line = [];
  let usedMain = 0;
  for (const item of items) {
    const next = line.length ? usedMain + mainGap + item.main : item.main;
    if (element.wrap && explicitMain !== null && line.length && next > explicitMain) {
      lines.push(line);
      line = [item];
      usedMain = item.main;
    } else {
      line.push(item);
      usedMain = next;
    }
  }
  if (line.length || !lines.length) lines.push(line);

  const naturalLineMain = lines.map((itemsInLine) =>
    itemsInLine.reduce((sum, item) => sum + item.main, 0) +
      Math.max(0, itemsInLine.length - 1) * mainGap
  );
  const naturalLineCross = lines.map((itemsInLine) =>
    Math.max(0, ...itemsInLine.map((item) => item.cross ?? 0))
  );
  const containerMain = explicitMain ?? Math.max(0, ...naturalLineMain);
  const explicitCross = row ? frame.height : frame.width;
  const naturalCross =
    naturalLineCross.reduce((sum, value) => sum + value, 0) +
    Math.max(0, lines.length - 1) * crossGap;
  const containerCross = explicitCross ?? naturalCross;
  const stretchPerLine = Math.max(0, containerCross - naturalCross) / Math.max(1, lines.length);

  const placements = [];
  let crossCursor = 0;
  lines.forEach((itemsInLine, lineIndex) => {
    const lineCross = naturalLineCross[lineIndex] + stretchPerLine;
    const freeMain = Math.max(0, containerMain - naturalLineMain[lineIndex]);
    let mainCursor = alignedOffset(justifyContent, freeMain);
    itemsInLine.forEach((item) => {
      const itemCross =
        item.cross === null && alignItems === "stretch" ? lineCross : item.cross ?? 0;
      const crossOffset = alignedOffset(alignItems, Math.max(0, lineCross - itemCross));
      placements[item.index] = row
        ? { x: mainCursor, y: crossCursor + crossOffset, width: item.main, height: itemCross }
        : { x: crossCursor + crossOffset, y: mainCursor, width: itemCross, height: item.main };
      mainCursor += item.main + mainGap;
    });
    crossCursor += lineCross + crossGap;
  });
  return {
    frame: {
      ...frame,
      width: frame.width ?? (row ? containerMain : containerCross),
      height: frame.height ?? (row ? containerCross : containerMain),
    },
    placements,
  };
}

function gridPlacements(element, frame, path) {
  if (!Array.isArray(element.children) || !Number.isInteger(element.columns) || element.columns < 1) {
    fail("template_v2_render_plan_grid_contract_required", path);
  }
  if (element.rows !== undefined && (!Number.isInteger(element.rows) || element.rows < 1)) {
    fail("template_v2_render_plan_grid_contract_required", `${path}.rows`);
  }
  const rows = element.rows ?? Math.max(1, Math.ceil(element.children.length / element.columns));
  if (element.children.length > element.columns * rows) {
    fail("template_v2_render_plan_grid_capacity_exceeded", path);
  }
  const gap = layoutGap(element, "gap", path) ?? 0;
  const columnGap = layoutGap(element, "column_gap", path) ?? gap;
  const rowGap = layoutGap(element, "row_gap", path) ?? gap;
  const alignItems = alignment(element.align_items, "stretch", `${path}.align_items`);
  const justifyItems = alignment(element.justify_items, "stretch", `${path}.justify_items`);
  const sizes = element.children.map((child, index) => rawSize(child, `${path}.children.${index}`));
  const naturalColumn = Math.max(0, ...sizes.map((size) => size.width ?? 0));
  const naturalRow = Math.max(0, ...sizes.map((size) => size.height ?? 0));
  const width = frame.width ?? naturalColumn * element.columns + columnGap * (element.columns - 1);
  const height = frame.height ?? naturalRow * rows + rowGap * (rows - 1);
  const cellWidth = Math.max(0, width - columnGap * (element.columns - 1)) / element.columns;
  const cellHeight = Math.max(0, height - rowGap * (rows - 1)) / rows;
  const placements = sizes.map((size, index) => {
    const column = index % element.columns;
    const row = Math.floor(index / element.columns);
    const childWidth = size.width === null && justifyItems === "stretch" ? cellWidth : size.width ?? 0;
    const childHeight = size.height === null && alignItems === "stretch" ? cellHeight : size.height ?? 0;
    return {
      x: column * (cellWidth + columnGap) +
        alignedOffset(justifyItems, Math.max(0, cellWidth - childWidth)),
      y: row * (cellHeight + rowGap) +
        alignedOffset(alignItems, Math.max(0, cellHeight - childHeight)),
      width: childWidth,
      height: childHeight,
    };
  });
  return { frame: { ...frame, width, height }, placements };
}

function planElement(element, path, parentAbsolute, frameOverride = null) {
  if (!isRecord(element) || typeof element.type !== "string") {
    fail("template_v2_render_plan_invalid_element", path);
  }
  if (!TEMPLATE_V2_PLAN_ELEMENT_TYPES.includes(element.type)) {
    fail("template_v2_render_plan_unsupported_element", `${path}.${element.type}`);
  }
  const vector = element.type === "vector" ? planVector(element, path) : null;
  let frame = baseFrame(element, path, frameOverride, vector);
  let placements = null;
  if (element.type === "flex") {
    ({ frame, placements } = flexPlacements(element, frame, path));
  } else if (element.type === "grid") {
    ({ frame, placements } = gridPlacements(element, frame, path));
  }
  const absoluteFrame = {
    x: parentAbsolute.x + frame.x,
    y: parentAbsolute.y + frame.y,
    width: frame.width,
    height: frame.height,
  };
  const node = {
    type: element.type,
    path,
    frame,
    absoluteFrame,
    rotation:
      optionalFinite(
        element.rotation,
        "template_v2_render_plan_invalid_rotation",
        `${path}.rotation`
      ) ?? 0,
    children: [],
  };
  if (vector) node.vector = vector;
  if (element.type === "text") node.text = planText(element, path);
  if (element.type === "container") node.container = planContainer(element, path);
  if (element.type === "image") node.image = planImage(element, path);
  if (element.type === "chart") {
    if (
      element.size !== undefined &&
      (frame.width === null || frame.height === null || frame.width < 80 || frame.height < 60)
    ) {
      fail("template_v2_render_plan_invalid_chart_size", `${path}.size`);
    }
    node.chart = planChart(element, path);
  }
  if (element.type === "infographic") node.infographic = planInfographic(element, path);

  if (element.type === "flex" || element.type === "grid") {
    node.children = element.children.map((child, index) =>
      planElement(
        child,
        `${path}.children.${index}`,
        absoluteFrame,
        placements[index]
      )
    );
  } else if (element.type === "group") {
    if (!Array.isArray(element.children)) {
      fail("template_v2_render_plan_group_children_required", `${path}.children`);
    }
    node.children = element.children.map((child, index) =>
      planElement(child, `${path}.children.${index}`, absoluteFrame)
    );
  } else if (element.type === "container" && element.child !== undefined && element.child !== null) {
    const padding = optionalRecord(
      element.padding,
      "template_v2_render_plan_invalid_padding",
      `${path}.padding`
    );
    const childX =
      optionalFinite(
        padding.left,
        "template_v2_render_plan_invalid_padding",
        `${path}.padding.left`
      ) ?? 0;
    const childY =
      optionalFinite(
        padding.top,
        "template_v2_render_plan_invalid_padding",
        `${path}.padding.top`
      ) ?? 0;
    const childSize = rawSize(element.child, `${path}.child`);
    node.children = [
      planElement(element.child, `${path}.child`, absoluteFrame, {
        x: childX,
        y: childY,
        width: childSize.width,
        height: childSize.height,
      }),
    ];
  }
  return node;
}

export function createTemplateV2SlideRenderPlan(slide, { pathPrefix = "0" } = {}) {
  if (!isRecord(slide) || !isRecord(slide.ui) || !Array.isArray(slide.ui.components)) {
    fail("template_v2_render_plan_ui_required", pathPrefix);
  }
  return {
    canvas: { ...CANVAS },
    components: slide.ui.components.map((component, componentIndex) => {
      const path = `${pathPrefix}.${componentIndex}`;
      if (!isRecord(component) || !Array.isArray(component.elements)) {
        fail("template_v2_render_plan_invalid_component", path);
      }
      const position = optionalRecord(
        component.position,
        "template_v2_render_plan_invalid_component_position",
        `${path}.position`
      );
      const frame = {
        x:
          optionalFinite(
            position.x,
            "template_v2_render_plan_invalid_component_position",
            `${path}.position.x`
          ) ?? 0,
        y:
          optionalFinite(
            position.y,
            "template_v2_render_plan_invalid_component_position",
            `${path}.position.y`
          ) ?? 0,
        width: CANVAS.width,
        height: CANVAS.height,
      };
      return {
        id: typeof component.id === "string" ? component.id : String(componentIndex),
        path,
        frame,
        elements: component.elements.map((element, elementIndex) =>
          planElement(element, `${path}.${elementIndex}`, frame)
        ),
      };
    }),
  };
}

export function assertTemplateV2PlanClosedVector(vectorPlan) {
  return Boolean(
    vectorPlan?.closed &&
      Array.isArray(vectorPlan.points) &&
      vectorPlan.points.length > 1 &&
      samePoint(vectorPlan.points[0], vectorPlan.points.at(-1))
  );
}
