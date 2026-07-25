import {
  createTemplateV2SlideRenderPlan,
  isTemplateV2SafeColor,
} from "./template-v2-render-plan.mjs";

const CANVAS = Object.freeze({ width: 1280, height: 720 });

export const TEMPLATE_V2_RENDERED_ELEMENT_TYPES = Object.freeze([
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

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finite(value, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// `fill.color` / `cell.color` are bare strings on the element and never pass through the
// render plan, so they reach a double-quoted style attribute unfiltered: `#fff" onmouseover=`
// closed the attribute outright, and escaping alone would not help because `;` and `:`
// survive it and smuggle whole extra declarations. Validating with the plan's colour policy
// stops both. Unrepresentable colours fall back instead of throwing -- this renderer is the
// PDF/PPTX export path, and one odd colour must degrade one element, not fail the deck.
function cssColor(fill, fallback = "transparent") {
  return isRecord(fill) && isTemplateV2SafeColor(fill.color) ? fill.color : fallback;
}

function positionStyle(element, layout = "absolute", plan = null) {
  const position = isRecord(element.position) ? element.position : {};
  const size = isRecord(element.size) ? element.size : {};
  const plannedFrame = layout === "flow" ? null : plan?.frame;
  const x = plannedFrame?.x ?? finite(position.x);
  const y = plannedFrame?.y ?? finite(position.y);
  const width = plannedFrame?.width ?? size.width;
  const height = plannedFrame?.height ?? size.height;
  const transforms = [];
  if (element.rotation) transforms.push(`rotate(${finite(element.rotation)}deg)`);
  if (plan?.image?.flipH) transforms.push("scaleX(-1)");
  if (plan?.image?.flipV) transforms.push("scaleY(-1)");
  return [
    `position:${layout === "flow" ? "relative" : "absolute"}`,
    layout === "flow" ? "" : `left:${finite(x)}px`,
    layout === "flow" ? "" : `top:${finite(y)}px`,
    Number.isFinite(width) ? `width:${finite(width)}px` : "",
    Number.isFinite(height) ? `height:${finite(height)}px` : "",
    transforms.length ? `transform:${transforms.join(" ")}` : "",
    `transform-origin:${plan?.image ? "center center" : "top left"}`,
    "box-sizing:border-box",
  ].filter(Boolean);
}

function colorWithOpacity(color, opacity) {
  if (opacity >= 1) return color;
  const shortHex = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(color);
  const longHex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(color);
  const parts = longHex?.slice(1) ?? shortHex?.slice(1).map((part) => `${part}${part}`);
  return parts
    ? `rgba(${Number.parseInt(parts[0], 16)},${Number.parseInt(parts[1], 16)},${Number.parseInt(parts[2], 16)},${opacity})`
    : `color-mix(in srgb,${color} ${opacity * 100}%,transparent)`;
}

function shadowStyle(shadow, property) {
  if (!shadow) return "";
  return `${property}:${shadow.offsetX}px ${shadow.offsetY}px ${shadow.blur}px ${colorWithOpacity(shadow.color, shadow.opacity)}`;
}

function fontStyle(font) {
  if (!isRecord(font)) return [];
  const lineHeight = finite(font.line_height);
  return [
    // Escaped because these land inside a double-quoted style attribute: the quotes
    // JSON.stringify adds would otherwise close it and drop every later declaration
    // -- size, colour and weight included. Same pattern as the mask-image URL below.
    typeof font.family === "string"
      ? `font-family:${escapeHtml(JSON.stringify(font.family))}`
      : "",
    font.size ? `font-size:${finite(font.size)}px` : "",
    // Validated rather than escaped: escaping stopped the quote but left `;` and `:`
    // intact, so `red;background:url(...)` still added declarations of its own. A colour
    // this validator accepts cannot contain a quote either, which is why escapeHtml is
    // gone from here and still present on font.family above.
    isTemplateV2SafeColor(font.color) ? `color:${font.color}` : "",
    font.bold ? "font-weight:700" : "",
    font.italic ? "font-style:italic" : "",
    font.underline ? "text-decoration:underline" : "",
    lineHeight ? `line-height:${lineHeight > 4 ? `${lineHeight}px` : lineHeight}` : "",
    font.letter_spacing ? `letter-spacing:${finite(font.letter_spacing)}px` : "",
    font.opacity !== undefined ? `opacity:${finite(font.opacity, 1)}` : "",
  ].filter(Boolean);
}

function renderRuns(runs, path, baseFont) {
  if (!Array.isArray(runs)) {
    throw new Error(`template_v2_renderer_text_runs_required:${path}`);
  }
  return runs.map((run, index) => {
    if (!isRecord(run) || typeof run.text !== "string") {
      throw new Error(`template_v2_renderer_invalid_text_run:${path}.${index}`);
    }
    const style = fontStyle(run.font ?? baseFont);
    return style.length
      ? `<span style="${style.join(";")}">${escapeHtml(run.text)}</span>`
      : escapeHtml(run.text);
  }).join("");
}

function renderText(element, path, layout, plan) {
  const alignment = plan?.text?.alignment ?? {};
  const vertical = alignment.vertical === "middle" ? "center" : alignment.vertical === "bottom" ? "flex-end" : "flex-start";
  const style = [
    ...positionStyle(element, layout, plan),
    ...fontStyle(element.font),
    `background:${cssColor(element.fill)}`,
    `text-align:${alignment.horizontal ?? "left"}`,
    "display:flex",
    `justify-content:${vertical}`,
    "align-items:stretch",
    "overflow:hidden",
    plan?.text?.stroke
      ? `-webkit-text-stroke:${plan.text.stroke.width}px ${colorWithOpacity(plan.text.stroke.color, plan.text.stroke.opacity)}`
      : "",
    plan?.text?.stroke ? "paint-order:stroke fill" : "",
    shadowStyle(plan?.text?.shadow, "text-shadow"),
  ].filter(Boolean);
  return `<div data-template-v2-element="text" data-element-name="${escapeHtml(element.name ?? path)}" style="${style.join(";")}"><div data-template-v2-text-runs style="white-space:pre-wrap">${renderRuns(element.runs, path, element.font)}</div></div>`;
}

function radiusStyle(radius) {
  return isRecord(radius)
    ? `${finite(radius.tl)}px ${finite(radius.tr)}px ${finite(radius.br)}px ${finite(radius.bl)}px`
    : "";
}

function renderContainer(element, path, layout, plan) {
  const stroke = plan?.container?.stroke;
  const padding = isRecord(element.padding) ? element.padding : {};
  const alignment = plan?.container?.alignment;
  const horizontal = alignment?.horizontal === "center"
    ? "center"
    : alignment?.horizontal === "right"
      ? "flex-end"
      : "flex-start";
  const vertical = alignment?.vertical === "middle"
    ? "center"
    : alignment?.vertical === "bottom"
      ? "flex-end"
      : "flex-start";
  const style = [
    ...positionStyle(element, layout, plan),
    `background:${cssColor(element.fill)}`,
    stroke?.width
      ? `border:${stroke.width}px solid ${colorWithOpacity(stroke.color, stroke.opacity)}`
      : "",
    isRecord(element.border_radius) ? `border-radius:${radiusStyle(element.border_radius)}` : "",
    isRecord(element.padding)
      ? `padding:${finite(padding.top)}px ${finite(padding.right)}px ${finite(padding.bottom)}px ${finite(padding.left)}px`
      : "",
    alignment ? "display:flex" : "",
    alignment ? `align-items:${horizontal}` : "",
    alignment ? `justify-content:${vertical}` : "",
    shadowStyle(plan?.container?.shadow, "box-shadow"),
    "overflow:hidden",
  ].filter(Boolean);
  const child = isRecord(element.child)
    ? renderElement(element.child, `${path}.child`, "flow", plan?.children[0])
    : "";
  return `<div data-template-v2-element="container" style="${style.join(";")}">${child}</div>`;
}

function renderImage(element, path, layout, plan) {
  if (typeof element.data !== "string") {
    throw new Error(`template_v2_renderer_image_data_required:${path}`);
  }
  const image = plan?.image;
  if (!image) throw new Error(`template_v2_renderer_image_plan_required:${path}`);
  const outerStyle = [
    ...positionStyle(element, layout, plan),
    isRecord(element.border_radius) ? `border-radius:${radiusStyle(element.border_radius)}` : "",
    `opacity:${image.opacity}`,
    image.clipPath ? `clip-path:${image.clipPath}` : "",
    "overflow:hidden",
  ].filter(Boolean);
  const innerStyle = [
    "display:block",
    "width:100%",
    "height:100%",
    `object-fit:${image.fit}`,
    `object-position:${image.focusX}% ${image.focusY}%`,
    image.cropScale !== 1 ? `transform:scale(${image.cropScale})` : "",
    `transform-origin:${image.focusX}% ${image.focusY}%`,
  ].filter(Boolean);
  const graphic = image.color
    ? `<div data-template-v2-image-mask style="${[
      "width:100%",
      "height:100%",
      `background:${image.color}`,
      `mask-image:url(${escapeHtml(JSON.stringify(element.data))})`,
      `-webkit-mask-image:url(${escapeHtml(JSON.stringify(element.data))})`,
      "mask-size:contain",
      "-webkit-mask-size:contain",
      "mask-position:center",
      "-webkit-mask-position:center",
      "mask-repeat:no-repeat",
      "-webkit-mask-repeat:no-repeat",
    ].join(";")}"></div>`
    : `<img alt="" src="${escapeHtml(element.data)}" style="${innerStyle.join(";")}">`;
  return `<div data-template-v2-element="image" data-image-fit="${image.fit}" data-image-crop-scale="${image.cropScale}" style="${outerStyle.join(";")}">${graphic}</div>`;
}

function renderTextList(element, path, layout, plan) {
  if (!Array.isArray(element.items)) {
    throw new Error(`template_v2_renderer_text_list_items_required:${path}`);
  }
  const marker = element.marker === "number" ? "decimal" : element.marker === "none" ? "none" : "disc";
  const tag = element.marker === "number" ? "ol" : "ul";
  const items = element.items.map((runs, index) =>
    `<li data-template-v2-list-item="${index}">${renderRuns(runs, `${path}.${index}`, element.font)}</li>`
  ).join("");
  const style = [
    ...positionStyle(element, layout, plan),
    ...fontStyle(element.font),
    `list-style-type:${marker}`,
    marker === "none" ? "padding-left:0" : "padding-left:1.4em",
    "margin:0",
    "overflow:hidden",
  ];
  return `<${tag} data-template-v2-element="text-list" data-element-name="${escapeHtml(element.name ?? path)}" style="${style.join(";")}">${items}</${tag}>`;
}

function renderTableCell(cell, path, header) {
  if (!isRecord(cell)) throw new Error(`template_v2_renderer_invalid_table_cell:${path}`);
  const tag = header ? "th" : "td";
  const style = [
    `background:${cssColor(cell.color)}`,
    ...fontStyle(cell.font),
    `text-align:${cell.alignment ?? "left"}`,
    "border:1px solid #d1d5db",
    "padding:6px",
    "overflow:hidden",
    "vertical-align:middle",
  ];
  return `<${tag} style="${style.join(";")}">${renderRuns(cell.runs, path, cell.font)}</${tag}>`;
}

function renderTable(element, path, layout, plan) {
  if (!Array.isArray(element.columns) || !Array.isArray(element.rows)) {
    throw new Error(`template_v2_renderer_table_data_required:${path}`);
  }
  const head = element.columns.length
    ? `<thead><tr>${element.columns.map((cell, index) => renderTableCell(cell, `${path}.columns.${index}`, true)).join("")}</tr></thead>`
    : "";
  const body = `<tbody>${element.rows.map((row, rowIndex) => {
    if (!Array.isArray(row)) throw new Error(`template_v2_renderer_invalid_table_row:${path}.${rowIndex}`);
    return `<tr>${row.map((cell, cellIndex) => renderTableCell(cell, `${path}.rows.${rowIndex}.${cellIndex}`, false)).join("")}</tr>`;
  }).join("")}</tbody>`;
  const style = [...positionStyle(element, layout, plan), "border-collapse:collapse", "table-layout:fixed"];
  return `<table data-template-v2-element="table" data-element-name="${escapeHtml(element.name ?? path)}" style="${style.join(";")}">${head}${body}</table>`;
}

function renderVector(element, path, plan) {
  if (!plan?.vector) {
    throw new Error(`template_v2_renderer_vector_plan_required:${path}`);
  }
  const geometry = plan.vector;
  const frame = plan.frame;
  const fill = geometry.closed ? cssColor(element.fill, "none") : "none";
  const stroke = isRecord(element.stroke) ? element.stroke : {};
  const strokeColor = typeof stroke.color === "string" ? stroke.color : "transparent";
  const strokeWidth = finite(stroke.width, 0);
  const opacity = element.opacity === undefined ? 1 : finite(element.opacity, 1);
  const outerStyle = `position:absolute;left:${frame.x}px;top:${frame.y}px;width:${frame.width}px;height:${frame.height}px;overflow:visible`;
  if (element.shape === "ellipse") {
    return `<svg data-template-v2-element="vector" viewBox="0 0 ${geometry.frame.width} ${geometry.frame.height}" style="${outerStyle}" opacity="${opacity}"><ellipse cx="${geometry.frame.width / 2}" cy="${geometry.frame.height / 2}" rx="${geometry.frame.width / 2}" ry="${geometry.frame.height / 2}" fill="${escapeHtml(fill)}" stroke="${escapeHtml(strokeColor)}" stroke-width="${strokeWidth}"/></svg>`;
  }
  const tag = geometry.closed ? "polygon" : "polyline";
  const points = geometry.points.map((point) => `${point.x},${point.y}`).join(" ");
  return `<svg data-template-v2-element="vector" viewBox="0 0 ${geometry.frame.width} ${geometry.frame.height}" style="${outerStyle}" opacity="${opacity}"><${tag} points="${points}" fill="${escapeHtml(fill)}" stroke="${escapeHtml(strokeColor)}" stroke-width="${strokeWidth}"/></svg>`;
}

function pieSegments(values, colors) {
  const positive = values.map((value) => Math.max(0, value));
  const total = positive.reduce((sum, value) => sum + value, 0) || 1;
  let cursor = 0;
  return positive.map((value, index) => {
    const start = cursor;
    cursor += (value / total) * 100;
    return `${colors[index % colors.length]} ${start}% ${cursor}%`;
  }).join(",");
}

function renderPlannedChart(element, path, layout, plan) {
  const chart = plan?.chart;
  if (!chart) throw new Error(`template_v2_renderer_chart_plan_required:${path}`);
  const series = chart.series;
  const colors = chart.colors.length
    ? chart.colors
    : ["#2563eb", "#f59e0b", "#10b981", "#ef4444"];
  const title = chart.title
    ? `<div data-chart-title style="height:24px;color:${escapeHtml(chart.titleColor)};font-weight:700">${escapeHtml(chart.title)}</div>`
    : "";
  const legend = chart.legend && series.length
    ? `<div data-chart-legend style="position:absolute;right:4px;top:4px;color:${escapeHtml(chart.legendColor)};font-size:12px">${series.map((item, index) => `<span style="margin-left:8px"><span aria-hidden="true" style="color:${escapeHtml(colors[index % colors.length])}">●</span> ${escapeHtml(item.name)}</span>`).join("")}</div>`
    : "";
  const source = chart.source
    ? `<div data-chart-source style="position:absolute;right:4px;bottom:2px;color:${escapeHtml(chart.legendColor)};font-size:10px">${escapeHtml(chart.source)}</div>`
    : "";
  const outerStyle = [...positionStyle(element, layout, plan), "overflow:hidden"].join(";");
  const firstValues = series[0]?.values ?? [];
  if (["pie", "donut", "polar_area"].includes(chart.type)) {
    const hole = chart.type === "donut"
      ? "mask:radial-gradient(circle at center,transparent 0 36%,#000 37%)"
      : "";
    const graphic = `<div data-chart-graphic="${escapeHtml(chart.type)}" style="width:100%;height:calc(100% - ${chart.title ? 24 : 0}px);background:conic-gradient(${pieSegments(firstValues, colors)});border-radius:50%;${hole}"></div>`;
    return `<div data-template-v2-element="chart" data-chart-type="${escapeHtml(chart.type)}" data-element-name="${escapeHtml(element.name ?? path)}" style="${outerStyle}">${title}${graphic}${legend}${source}</div>`;
  }

  const width = 640;
  const height = 300;
  const left = 58;
  const right = 18;
  const top = 18;
  const bottom = 44;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const itemCount = Math.max(1, chart.categories.length, ...series.map((item) => item.values.length));
  const all = series.flatMap((item) => item.values);
  const positiveStacks = Array.from({ length: itemCount }, (_, index) =>
    series.reduce((sum, item) => sum + Math.max(0, item.values[index] ?? 0), 0)
  );
  const negativeStacks = Array.from({ length: itemCount }, (_, index) =>
    series.reduce((sum, item) => sum + Math.min(0, item.values[index] ?? 0), 0)
  );
  const maximum = Math.max(1, ...(chart.stacked ? positiveStacks : all), 0);
  const minimum = Math.min(0, ...(chart.stacked ? negativeStacks : all));
  const span = maximum - minimum || 1;
  const xForValue = (value) => left + ((value - minimum) / span) * plotWidth;
  const yForValue = (value) => top + ((maximum - value) / span) * plotHeight;
  const categoryBand = (chart.horizontal ? plotHeight : plotWidth) / itemCount;
  const editableText = [];
  const htmlLabel = (attribute, value, x, y, anchor = "middle", transform = "") => {
    const translateX = anchor === "start" ? "0" : anchor === "end" ? "-100%" : "-50%";
    const rotation = transform ? ` ${transform}` : "";
    editableText.push(
      `<div ${attribute} style="position:absolute;left:${x / width * 100}%;top:${y / height * 100}%;transform:translate(${translateX},-50%)${rotation};transform-origin:center;white-space:nowrap;font-size:10px;line-height:12px;color:${escapeHtml(chart.axisColor)}">${escapeHtml(value)}</div>`
    );
    return "";
  };
  const label = (value, x, y, anchor = "middle") => chart.dataLabels
    ? htmlLabel(`data-chart-data-label="${chart.dataLabels}"`, value, x, y, anchor)
    : "";
  const barLabel = (value, startCoordinate, endCoordinate, crossCoordinate) => {
    if (!chart.dataLabels) return "";
    const positive = value >= 0;
    if (chart.horizontal) {
      if (chart.dataLabels === "base") {
        return label(value, startCoordinate + (positive ? 4 : -4), crossCoordinate, positive ? "start" : "end");
      }
      if (chart.dataLabels === "mid") {
        return label(value, (startCoordinate + endCoordinate) / 2, crossCoordinate);
      }
      if (chart.dataLabels === "top") {
        return label(value, endCoordinate + (positive ? -4 : 4), crossCoordinate, positive ? "end" : "start");
      }
      return label(value, endCoordinate + (positive ? 4 : -4), crossCoordinate, positive ? "start" : "end");
    }
    if (chart.dataLabels === "base") {
      return label(value, crossCoordinate, startCoordinate + (positive ? -3 : 11));
    }
    if (chart.dataLabels === "mid") {
      return label(value, crossCoordinate, (startCoordinate + endCoordinate) / 2 + 4);
    }
    if (chart.dataLabels === "top") {
      return label(value, crossCoordinate, endCoordinate + (positive ? 11 : -3));
    }
    return label(value, crossCoordinate, endCoordinate + (positive ? -3 : 11));
  };
  const grid = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    return `${chart.xAxisGrid ? `<line data-chart-grid="x" x1="${left + ratio * plotWidth}" y1="${top}" x2="${left + ratio * plotWidth}" y2="${height - bottom}" stroke="${escapeHtml(chart.gridColor)}"/>` : ""}${chart.yAxisGrid ? `<line data-chart-grid="y" x1="${left}" y1="${top + ratio * plotHeight}" x2="${width - right}" y2="${top + ratio * plotHeight}" stroke="${escapeHtml(chart.gridColor)}"/>` : ""}`;
  }).join("");
  const axes = [
    chart.xAxis
      ? `<line data-chart-axis="x" x1="${left}" y1="${yForValue(0)}" x2="${width - right}" y2="${yForValue(0)}" stroke="${escapeHtml(chart.axisColor)}"/>`
      : "",
    chart.yAxis
      ? `<line data-chart-axis="y" x1="${xForValue(0)}" y1="${top}" x2="${xForValue(0)}" y2="${height - bottom}" stroke="${escapeHtml(chart.axisColor)}"/>`
      : "",
    chart.xAxisTitle
      ? htmlLabel('data-chart-axis-title="x"', chart.xAxisTitle, left + plotWidth / 2, height - 5)
      : "",
    chart.yAxisTitle
      ? htmlLabel('data-chart-axis-title="y"', chart.yAxisTitle, 12, top + plotHeight / 2, "middle", "rotate(-90deg)")
      : "",
  ].join("");
  const categoryLabels = chart.categories.map((category, index) => chart.horizontal
    ? htmlLabel(`data-chart-category="${index}"`, category, left - 5, top + (index + 0.55) * categoryBand, "end")
    : htmlLabel(`data-chart-category="${index}"`, category, left + (index + 0.5) * categoryBand, height - bottom + 14)
  ).join("");

  const lineLike = ["line", "area", "radar", "scatter", "bubble"].includes(chart.type);
  let shapes;
  if (lineLike) {
    shapes = series.map((item, seriesIndex) => {
      const points = item.values.map((value, valueIndex) =>
        `${left + (valueIndex + 0.5) * categoryBand},${yForValue(value)}`
      ).join(" ");
      const circles = item.values.map((value, valueIndex) => {
        const x = left + (valueIndex + 0.5) * categoryBand;
        const y = yForValue(value);
        const radius = chart.type === "bubble" ? clamp(Math.abs(value) / maximum * 18, 4, 18) : 4;
        const labelOffset = chart.dataLabels === "outside" ? 12 : 7;
        return `<circle cx="${x}" cy="${y}" r="${radius}" fill="${escapeHtml(colors[seriesIndex % colors.length])}"/>${label(value, x, y - labelOffset)}`;
      }).join("");
      return `<polyline points="${points}" fill="${chart.type === "area" ? escapeHtml(colors[seriesIndex % colors.length]) : "none"}" fill-opacity="${chart.type === "area" ? "0.2" : "0"}" stroke="${escapeHtml(colors[seriesIndex % colors.length])}" stroke-width="3"/>${circles}`;
    }).join("");
  } else if (chart.stacked) {
    const positive = Array(itemCount).fill(0);
    const negative = Array(itemCount).fill(0);
    shapes = series.map((item, seriesIndex) => item.values.map((value, valueIndex) => {
      const start = value >= 0 ? positive[valueIndex] : negative[valueIndex];
      const end = start + value;
      if (value >= 0) positive[valueIndex] = end;
      else negative[valueIndex] = end;
      if (chart.horizontal) {
        const x1 = xForValue(Math.min(start, end));
        const x2 = xForValue(Math.max(start, end));
        const y = top + valueIndex * categoryBand + categoryBand * 0.15;
        return `<rect data-chart-series="${seriesIndex}" data-chart-category-index="${valueIndex}" data-stack-start="${start}" data-stack-end="${end}" x="${x1}" y="${y}" width="${x2 - x1}" height="${categoryBand * 0.7}" fill="${escapeHtml(colors[seriesIndex % colors.length])}"/>${barLabel(value, xForValue(start), xForValue(end), y + categoryBand * 0.42)}`;
      }
      const y1 = yForValue(Math.max(start, end));
      const y2 = yForValue(Math.min(start, end));
      const x = left + valueIndex * categoryBand + categoryBand * 0.15;
      return `<rect data-chart-series="${seriesIndex}" data-chart-category-index="${valueIndex}" data-stack-start="${start}" data-stack-end="${end}" x="${x}" y="${y1}" width="${categoryBand * 0.7}" height="${y2 - y1}" fill="${escapeHtml(colors[seriesIndex % colors.length])}"/>${barLabel(value, yForValue(start), yForValue(end), x + categoryBand * 0.35)}`;
    }).join("")).join("");
  } else {
    shapes = series.map((item, seriesIndex) => item.values.map((value, valueIndex) => {
      const seriesBand = categoryBand / Math.max(1, series.length);
      if (chart.horizontal) {
        const x1 = xForValue(Math.min(0, value));
        const x2 = xForValue(Math.max(0, value));
        const y = top + valueIndex * categoryBand + seriesIndex * seriesBand + 2;
        return `<rect data-chart-series="${seriesIndex}" data-chart-category-index="${valueIndex}" x="${x1}" y="${y}" width="${x2 - x1}" height="${Math.max(2, seriesBand - 4)}" fill="${escapeHtml(colors[seriesIndex % colors.length])}"/>${barLabel(value, xForValue(0), xForValue(value), y + seriesBand / 2)}`;
      }
      const y1 = yForValue(Math.max(0, value));
      const y2 = yForValue(Math.min(0, value));
      const x = left + valueIndex * categoryBand + seriesIndex * seriesBand + 2;
      return `<rect data-chart-series="${seriesIndex}" data-chart-category-index="${valueIndex}" x="${x}" y="${y1}" width="${Math.max(2, seriesBand - 4)}" height="${y2 - y1}" fill="${escapeHtml(colors[seriesIndex % colors.length])}"/>${barLabel(value, yForValue(0), yForValue(value), x + seriesBand / 2)}`;
    }).join("")).join("");
  }
  const graphicHeight = `calc(100% - ${chart.title ? 24 : 0}px)`;
  const textLayer = `<div data-chart-editable-text-layer style="position:absolute;inset:0;pointer-events:none;overflow:visible">${editableText.join("")}</div>`;
  const graphic = `<div data-chart-graphic="${escapeHtml(chart.type)}" style="position:relative;width:100%;height:${graphicHeight}"><svg data-chart-stacked="${chart.stacked}" data-chart-horizontal="${chart.horizontal}" viewBox="0 0 ${width} ${height}" style="position:absolute;inset:0;width:100%;height:100%">${grid}${axes}${categoryLabels}${shapes}</svg>${textLayer}</div>`;
  return `<div data-template-v2-element="chart" data-chart-type="${escapeHtml(chart.type)}" data-element-name="${escapeHtml(element.name ?? path)}" style="${outerStyle}">${title}${graphic}${legend}${source}</div>`;
}

function renderInfographic(element, path, layout, plan) {
  if (!isRecord(element.data) || !["progress_bar", "gauge"].includes(element.data.type)) {
    throw new Error(`template_v2_renderer_infographic_data_required:${path}`);
  }
  const ratio = plan?.infographic?.ratio ??
    clamp(
      (finite(element.data.value) - finite(element.data.min_value)) /
        (finite(element.data.max_value) - finite(element.data.min_value)),
      0,
      1
    );
  // `plan.infographic.colors` comes from `stringArray`, which checks the type and not the
  // value, so these are the same unfiltered sink as `fill.color` -- and a gradient argument
  // is worse, because a `)` escapes the function as well as the declaration.
  const planned = plan?.infographic?.colors ?? [];
  const colors = [
    isTemplateV2SafeColor(planned[0]) ? planned[0] : "#2563eb",
    isTemplateV2SafeColor(planned[1]) ? planned[1] : "#e5e7eb",
  ];
  const label = `${Math.round(ratio * 100)}%`;
  const graphic = element.data.type === "gauge"
    ? `<div style="position:relative;width:100%;height:100%;border-radius:50%;background:conic-gradient(${colors[0]} 0 ${ratio * 100}%,${colors[1]} ${ratio * 100}% 100%)"><div style="position:absolute;inset:24%;border-radius:50%;background:white;display:flex;align-items:center;justify-content:center;font-weight:700">${label}</div></div>`
    : `<div style="position:relative;width:100%;height:100%;min-height:24px;background:${colors[1]};border-radius:999px;overflow:hidden"><div style="width:${ratio * 100}%;height:100%;background:${colors[0]}"></div><div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:700">${label}</div></div>`;
  return `<div data-template-v2-element="infographic" data-infographic-type="${escapeHtml(element.data.type)}" data-element-name="${escapeHtml(element.name ?? path)}" style="${positionStyle(element, layout, plan).join(";")}">${graphic}</div>`;
}

function renderFlex(element, path, layout, plan) {
  if (!Array.isArray(element.children)) throw new Error(`template_v2_renderer_flex_children_required:${path}`);
  const style = [
    ...positionStyle(element, layout, plan),
    "display:block",
  ].filter(Boolean);
  return `<div data-template-v2-element="flex" data-template-v2-layout="absolute-plan" data-element-name="${escapeHtml(element.name ?? path)}" style="${style.join(";")}">${element.children.map((child, index) => renderElement(child, `${path}.${index}`, "absolute", plan?.children[index])).join("")}</div>`;
}

function renderGrid(element, path, layout, plan) {
  if (!Array.isArray(element.children) || !Number.isInteger(element.columns) || element.columns < 1) {
    throw new Error(`template_v2_renderer_grid_contract_required:${path}`);
  }
  const style = [
    ...positionStyle(element, layout, plan),
    "display:block",
  ].filter(Boolean);
  return `<div data-template-v2-element="grid" data-template-v2-layout="absolute-plan" data-element-name="${escapeHtml(element.name ?? path)}" style="${style.join(";")}">${element.children.map((child, index) => renderElement(child, `${path}.${index}`, "absolute", plan?.children[index])).join("")}</div>`;
}

function renderGroup(element, path, layout, plan) {
  if (!Array.isArray(element.children)) throw new Error(`template_v2_renderer_group_children_required:${path}`);
  return `<div data-template-v2-element="group" data-element-name="${escapeHtml(element.name ?? path)}" style="${positionStyle(element, layout, plan).join(";")}">${element.children.map((child, index) => renderElement(child, `${path}.${index}`, "absolute", plan?.children[index])).join("")}</div>`;
}

function renderElement(element, path, layout = "absolute", plan = null) {
  if (!isRecord(element) || typeof element.type !== "string") {
    throw new Error(`template_v2_renderer_invalid_element:${path}`);
  }
  if (element.type === "text") return renderText(element, path, layout, plan);
  if (element.type === "container") return renderContainer(element, path, layout, plan);
  if (element.type === "image") return renderImage(element, path, layout, plan);
  if (element.type === "text-list") return renderTextList(element, path, layout, plan);
  if (element.type === "table") return renderTable(element, path, layout, plan);
  if (element.type === "vector") return renderVector(element, path, plan);
  if (element.type === "chart") return renderPlannedChart(element, path, layout, plan);
  if (element.type === "infographic") return renderInfographic(element, path, layout, plan);
  if (element.type === "flex") return renderFlex(element, path, layout, plan);
  if (element.type === "grid") return renderGrid(element, path, layout, plan);
  if (element.type === "group") return renderGroup(element, path, layout, plan);
  throw new Error(`template_v2_renderer_unsupported_element:${element.type}`);
}

function createGeneralRenderPlan(slide, index) {
  try {
    return createTemplateV2SlideRenderPlan(slide, { pathPrefix: String(index) });
  } catch (error) {
    if (!(error instanceof Error) || !error.message.startsWith("template_v2_render_plan_")) {
      throw error;
    }
    if (error.message.startsWith("template_v2_render_plan_unsupported_element:")) {
      const elementType = error.message.slice(error.message.lastIndexOf(".") + 1);
      throw new Error(`template_v2_renderer_unsupported_element:${elementType}`);
    }
    throw new Error(error.message.replace("template_v2_render_plan_", "template_v2_renderer_"));
  }
}

function renderSlideCanvasContents(slide, index) {
  const renderPlan = createGeneralRenderPlan(slide, index);
  return slide.ui.components.map((component, componentIndex) => {
    const componentPlan = renderPlan.components[componentIndex];
    const style = `position:absolute;left:${componentPlan.frame.x}px;top:${componentPlan.frame.y}px;width:${componentPlan.frame.width}px;height:${componentPlan.frame.height}px`;
    return `<div data-template-v2-component="${escapeHtml(component.id ?? componentIndex)}" style="${style}">${component.elements.map((element, elementIndex) => renderElement(element, `${index}.${componentIndex}.${elementIndex}`, "absolute", componentPlan.elements[elementIndex])).join("")}</div>`;
  }).join("");
}

export function renderTemplateV2GeneralSlideCanvasHtml(slide) {
  return renderSlideCanvasContents(slide, 0);
}

function renderSlide(slide, index) {
  const elements = renderSlideCanvasContents(slide, index);
  const id = typeof slide.id === "string" ? slide.id : `slide-${index + 1}`;
  return `<div id="slide-${index + 1}" class="main-slide" data-template-slide-id="${escapeHtml(id)}"><div class="slide-export-inner" data-layout="template-v2-general"><div class="slide-scale-frame"><div class="slide-canvas">${elements}</div></div></div></div>`;
}

const CSS = `*{box-sizing:border-box}html,body,#presentation-slides-wrapper,.main-slide,.slide-export-inner,.slide-scale-frame,.slide-canvas{width:1280px;height:720px;margin:0;overflow:hidden}.slide-canvas{position:relative;font-family:Arial,sans-serif}`;

export function renderTemplateV2GeneralPresentationHtml(presentation) {
  if (!isRecord(presentation) || presentation.version !== "v2-standard" || presentation.mode !== "template") {
    throw new Error("template_v2_renderer_identity_required");
  }
  if (!Array.isArray(presentation.slides) || presentation.slides.length === 0) {
    throw new Error("template_v2_renderer_slides_required");
  }
  const slides = presentation.slides.map(renderSlide).join("");
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=${CANVAS.width},initial-scale=1"><style>${CSS}</style></head><body><div id="presentation-slides-wrapper"><div class="slides-export-stack">${slides}</div></div></body></html>`;
}

export { CANVAS as TEMPLATE_V2_EXPORT_CANVAS };
