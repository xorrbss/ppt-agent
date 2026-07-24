/**
 * Plain JavaScript injected into a temporary authored HTML copy. Keep this
 * source self-contained: Chrome executes it directly, without a bundler or a
 * dependency on the generated presentation-export runtime.
 */
export const AUTHORED_HYBRID_RESULT_MARKER_ID =
  "__presenton_authored_hybrid_result_v1__";

export const AUTHORED_HYBRID_ELEMENT_ATTRIBUTE =
  "data-presenton-authored-hybrid-id";

export const AUTHORED_HYBRID_BROWSER_SOURCE = String.raw`
(async function () {
  "use strict";

  var MARKER_ID = "__presenton_authored_hybrid_result_v1__";
  var ELEMENT_ATTRIBUTE = "data-presenton-authored-hybrid-id";
  var WIDTH = 1280;
  var HEIGHT = 720;
  var config = window.__PRESENTON_AUTHORED_HYBRID_CONFIG__ || {};
  var promotedElements = Array.isArray(config.promotedElements)
    ? config.promotedElements
    : [];

  function round(value) {
    return Math.round(value * 1000000) / 1000000;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function number(value, fallback) {
    var parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function hasPositiveCssComponent(value) {
    return (String(value || "").match(/-?(?:\d+(?:\.\d+)?|\.\d+)/g) || [])
      .some(function (component) { return number(component, 0) > 0.001; });
  }

  function pixelLength(value) {
    var match = String(value || "").trim().match(
      /^(-?(?:\d+(?:\.\d+)?|\.\d+))px$/
    );
    return match ? number(match[1], Number.NaN) : Number.NaN;
  }

  function rectValue(rect) {
    return {
      x: round(rect.left),
      y: round(rect.top),
      width: round(rect.width),
      height: round(rect.height),
    };
  }

  function unique(values) {
    return Array.from(new Set(values));
  }

  function parseColor(value) {
    if (!value || value === "transparent") {
      return { hex: "000000", alpha: 0 };
    }
    var match = value.match(
      /^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$/i
    );
    if (!match) return null;
    var red = clamp(Math.round(number(match[1], 0)), 0, 255);
    var green = clamp(Math.round(number(match[2], 0)), 0, 255);
    var blue = clamp(Math.round(number(match[3], 0)), 0, 255);
    var alpha = 1;
    if (match[4]) {
      alpha = match[4].endsWith("%")
        ? number(match[4], 100) / 100
        : number(match[4], 1);
    }
    var hex = [red, green, blue]
      .map(function (channel) {
        return channel.toString(16).padStart(2, "0").toUpperCase();
      })
      .join("");
    return { hex: hex, alpha: round(clamp(alpha, 0, 1)) };
  }

  function splitCssArguments(value) {
    var parts = [];
    var depth = 0;
    var start = 0;
    for (var index = 0; index < value.length; index += 1) {
      var character = value[index];
      if (character === "(") depth += 1;
      if (character === ")") depth = Math.max(0, depth - 1);
      if (character === "," && depth === 0) {
        parts.push(value.slice(start, index).trim());
        start = index + 1;
      }
    }
    parts.push(value.slice(start).trim());
    return parts.filter(Boolean);
  }

  function cssGradientAngle(value) {
    var normalized = String(value || "").trim().toLowerCase();
    var degrees = normalized.match(/^(-?(?:\d+(?:\.\d+)?|\.\d+))deg$/);
    if (degrees) return number(degrees[1], 180);
    var directions = {
      "to top": 0,
      "to top right": 45,
      "to right top": 45,
      "to right": 90,
      "to bottom right": 135,
      "to right bottom": 135,
      "to bottom": 180,
      "to bottom left": 225,
      "to left bottom": 225,
      "to left": 270,
      "to top left": 315,
      "to left top": 315,
    };
    return Object.prototype.hasOwnProperty.call(directions, normalized)
      ? directions[normalized]
      : null;
  }

  function parseLinearGradient(value, bounds) {
    var match = String(value || "").trim().match(/^linear-gradient\((.*)\)$/i);
    if (!match) return null;
    var parts = splitCssArguments(match[1]);
    if (parts.length < 2) return null;
    var angle = cssGradientAngle(parts[0]);
    if (angle !== null) parts.shift();
    else angle = 180;
    var radians = angle * Math.PI / 180;
    var axisLength = Math.max(
      0.01,
      Math.abs(Math.sin(radians)) * bounds.width +
        Math.abs(Math.cos(radians)) * bounds.height
    );
    var stops = parts.map(function (part) {
      var stop = part.match(/^(rgba?\([^)]*\)|#[0-9a-f]{3,8})(?:\s+(.+))?$/i);
      if (!stop) return null;
      var color = parseColor(stop[1]);
      if (!color) return null;
      var position = null;
      if (stop[2]) {
        var positionValue = stop[2].trim().split(/\s+/)[0];
        if (/%$/.test(positionValue)) {
          position = number(positionValue, Number.NaN) / 100;
        } else if (/px$/.test(positionValue)) {
          position = number(positionValue, Number.NaN) / axisLength;
        } else if (/^0(?:\.0+)?$/.test(positionValue)) {
          position = 0;
        }
        if (position !== null && !Number.isFinite(position)) return null;
      }
      return { color: color, position: position };
    });
    if (stops.length < 2 || stops.some(function (stop) { return !stop; })) return null;
    if (stops[0].position === null) stops[0].position = 0;
    if (stops[stops.length - 1].position === null) stops[stops.length - 1].position = 1;
    var cursor = 0;
    while (cursor < stops.length) {
      if (stops[cursor].position !== null) {
        cursor += 1;
        continue;
      }
      var runStart = cursor;
      while (cursor < stops.length && stops[cursor].position === null) cursor += 1;
      var previous = stops[runStart - 1].position;
      var next = stops[cursor].position;
      var count = cursor - runStart + 1;
      for (var offset = 0; offset < cursor - runStart; offset += 1) {
        stops[runStart + offset].position = previous + (next - previous) * (offset + 1) / count;
      }
    }
    var last = 0;
    stops.forEach(function (stop) {
      stop.position = round(clamp(Math.max(last, stop.position), 0, 1));
      last = stop.position;
    });
    return { angleDeg: round(angle), stops: stops };
  }

  function fontFamilies(value) {
    return value
      .split(",")
      .map(function (family) {
        return family.trim().replace(/^['\"]|['\"]$/g, "");
      })
      .filter(Boolean);
  }

  function cjkFallbacks(text, families) {
    if (!/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/u.test(text)) return [];
    var fallback = [
      "Noto Sans KR",
      "Malgun Gothic",
      "Apple SD Gothic Neo",
      "Arial Unicode MS",
      "sans-serif",
    ];
    var knownCjk = new Set(
      fallback.map(function (family) {
        return family.toLowerCase();
      })
    );
    var preferred = families.filter(function (family) {
      var lower = family.toLowerCase();
      return lower !== "sans-serif" && knownCjk.has(lower);
    });
    var preferredNames = new Set(
      preferred.map(function (family) {
        return family.toLowerCase();
      })
    );
    return preferred.concat(
      fallback.filter(function (family) {
        return !preferredNames.has(family.toLowerCase());
      })
    );
  }

  function textStyle(element, text) {
    var style = getComputedStyle(element);
    var families = fontFamilies(style.fontFamily || "sans-serif");
    var fontSizePx = number(style.fontSize, 16);
    var lineHeightSource = style.lineHeight === "normal" ? "normal" : "computed";
    var lineHeightPx =
      lineHeightSource === "normal"
        ? fontSizePx * 1.2
        : number(style.lineHeight, fontSizePx * 1.2);
    var fontWeight = Number.parseInt(style.fontWeight, 10);
    if (!Number.isFinite(fontWeight)) {
      fontWeight = style.fontWeight === "bold" ? 700 : 400;
    }
    var decoration = style.textDecorationLine || "none";
    var alignment = style.textAlign;
    if (alignment === "start") alignment = style.direction === "rtl" ? "right" : "left";
    if (alignment === "end") alignment = style.direction === "rtl" ? "left" : "right";
    if (!["left", "center", "right", "justify"].includes(alignment)) {
      alignment = "left";
    }
    var fontSizePt = Math.max(9, round(fontSizePx * 0.75));
    var lineHeightMultiple = round(lineHeightPx / Math.max(fontSizePx, 0.01));
    // Which alignment property maps to the vertical (block) axis depends on the
    // layout: a flex ROW centers vertically via align-items (cross axis) while its
    // justify-content controls the HORIZONTAL axis; a flex COLUMN is the reverse;
    // grid maps align-items to the vertical axis. Treating a row's justify-content
    // as a vertical anchor (the old bug) shifted centered text within its box.
    var vertical = "top";
    var display = style.display || "";
    if (display === "flex" || display === "inline-flex") {
      var flexDirection = style.flexDirection || "row";
      var isColumn =
        flexDirection === "column" || flexDirection === "column-reverse";
      var isReversed =
        flexDirection === "row-reverse" || flexDirection === "column-reverse";
      var verticalAlign = isColumn ? style.justifyContent : style.alignItems;
      var bottomValues =
        isColumn && isReversed ? ["flex-start", "start"] : ["flex-end", "end"];
      if (verticalAlign === "center") vertical = "middle";
      else if (bottomValues.indexOf(verticalAlign) !== -1) vertical = "bottom";
    } else if (display === "grid" || display === "inline-grid") {
      if (style.alignItems === "center") vertical = "middle";
      else if (style.alignItems === "flex-end" || style.alignItems === "end")
        vertical = "bottom";
    }
    return {
      fontFamily: families[0] || "sans-serif",
      fontFamilies: families.length ? families : ["sans-serif"],
      cjkFallbackFamilies: cjkFallbacks(text, families),
      fontSizePt: fontSizePt,
      fontWeight: fontWeight,
      bold: fontWeight >= 600,
      italic: style.fontStyle === "italic" || style.fontStyle === "oblique",
      underline: decoration.includes("underline"),
      strike: decoration.includes("line-through"),
      color: parseColor(style.color) || { hex: "000000", alpha: 1 },
      letterSpacingPt:
        style.letterSpacing === "normal" ? 0 : round(number(style.letterSpacing, 0) * 0.75),
      lineHeight: {
        points: Math.max(
          round(lineHeightPx * 0.75),
          round(fontSizePt * lineHeightMultiple)
        ),
        multiple: lineHeightMultiple,
        source: lineHeightSource,
      },
      horizontalAlignment: alignment,
      verticalAlignment: vertical,
      direction: style.direction === "rtl" ? "rtl" : "ltr",
      wrapMode:
        style.whiteSpace === "nowrap" || style.whiteSpace === "pre"
          ? "no-wrap"
          : "wrap",
    };
  }

  function effectiveOpacity(element) {
    var opacity = 1;
    var current = element;
    while (current) {
      opacity *= clamp(number(getComputedStyle(current).opacity, 1), 0, 1);
      if (current === document.documentElement) break;
      current = current.parentElement;
    }
    return round(clamp(opacity, 0, 1));
  }

  function domPath(element) {
    var parts = [];
    var current = element;
    while (current && current !== document.body) {
      var tag = current.tagName.toLowerCase();
      var index = 1;
      var sibling = current;
      while ((sibling = sibling.previousElementSibling)) {
        if (sibling.tagName === current.tagName) index += 1;
      }
      parts.unshift(tag + ":nth-of-type(" + index + ")");
      current = current.parentElement;
    }
    return "body" + (parts.length ? " > " + parts.join(" > ") : "");
  }

  function hasIndividualTransform(style) {
    return [style.rotate, style.translate, style.scale].some(function (value) {
      return value && value !== "none";
    });
  }

  function analyzeTransform(element) {
    var style = getComputedStyle(element);
    var transform = style.transform;
    if (hasIndividualTransform(style)) {
      // Individual CSS translation only changes the final position already
      // reported by getBoundingClientRect(). It does not distort geometry and
      // is therefore safe to reproduce at those measured coordinates.
      var translateOnly = style.translate && style.translate !== "none" &&
        (!style.rotate || style.rotate === "none") &&
        (!style.scale || style.scale === "none");
      return { safe: Boolean(translateOnly), rotationDeg: 0, matrix: null };
    }
    if (!transform || transform === "none") {
      return { safe: true, rotationDeg: 0, matrix: null };
    }
    var match = transform.match(/^matrix\(([^)]+)\)$/);
    if (!match) return { safe: false, rotationDeg: 0, matrix: null };
    var values = match[1].split(",").map(function (part) {
      return number(part, Number.NaN);
    });
    if (values.length !== 6 || values.some(function (value) { return !Number.isFinite(value); })) {
      return { safe: false, rotationDeg: 0, matrix: null };
    }
    var a = values[0];
    var b = values[1];
    var c = values[2];
    var d = values[3];
    var e = values[4];
    var f = values[5];
    var scaleX = Math.hypot(a, b);
    var scaleY = Math.hypot(c, d);
    var dot = a * c + b * d;
    var determinant = a * d - b * c;
    var origin = (style.transformOrigin || "")
      .trim()
      .split(/\s+/)
      .map(function (part) { return number(part, Number.NaN); });
    var centeredOrigin =
      origin.length >= 2 &&
      Number.isFinite(origin[0]) &&
      Number.isFinite(origin[1]) &&
      Math.abs(origin[0] - element.offsetWidth / 2) < 0.01 &&
      Math.abs(origin[1] - element.offsetHeight / 2) < 0.01 &&
      (origin.length < 3 || (Number.isFinite(origin[2]) && Math.abs(origin[2]) < 0.01));
    var rotationDeg = round((Math.atan2(b, a) * 180) / Math.PI);
    var safe =
      Math.abs(scaleX - 1) < 0.001 &&
      Math.abs(scaleY - 1) < 0.001 &&
      Math.abs(dot) < 0.001 &&
      determinant > 0.999 &&
      Number.isFinite(e) &&
      Number.isFinite(f) &&
      (Math.abs(rotationDeg) < 0.001 || centeredOrigin);
    return {
      safe: safe,
      rotationDeg: safe ? rotationDeg : 0,
      matrix: { a: a, b: b, c: c, d: d, e: e, f: f },
    };
  }

  function affineRectanglePoints(transform, sourceWidth, sourceHeight) {
    if (!transform || !transform.matrix) return null;
    var matrix = transform.matrix;
    var sourceWidthPx = Math.max(0.0001, number(sourceWidth, 1));
    var sourceHeightPx = Math.max(0.0001, number(sourceHeight, 1));
    var corners = [
      { x: 0, y: 0 },
      { x: sourceWidthPx, y: 0 },
      { x: sourceWidthPx, y: sourceHeightPx },
      { x: 0, y: sourceHeightPx },
    ].map(function (point) {
      return {
        x: matrix.a * point.x + matrix.c * point.y,
        y: matrix.b * point.x + matrix.d * point.y,
      };
    });
    var minimumX = Math.min.apply(null, corners.map(function (point) { return point.x; }));
    var maximumX = Math.max.apply(null, corners.map(function (point) { return point.x; }));
    var minimumY = Math.min.apply(null, corners.map(function (point) { return point.y; }));
    var maximumY = Math.max.apply(null, corners.map(function (point) { return point.y; }));
    var width = maximumX - minimumX;
    var height = maximumY - minimumY;
    if (width <= 0.0001 || height <= 0.0001) return null;
    return corners.map(function (point) {
      return {
        x: round(clamp((point.x - minimumX) / width, 0, 1)),
        y: round(clamp((point.y - minimumY) / height, 0, 1)),
      };
    });
  }

  function unrotatedRect(element, rotationDeg) {
    var rect = element.getBoundingClientRect();
    if (Math.abs(rotationDeg) < 0.001) return rectValue(rect);
    var width = element.offsetWidth || rect.width;
    var height = element.offsetHeight || rect.height;
    return {
      x: round(rect.left + (rect.width - width) / 2),
      y: round(rect.top + (rect.height - height) / 2),
      width: round(width),
      height: round(height),
    };
  }

  function pseudoPainted(element, pseudo) {
    var style;
    try {
      style = getComputedStyle(element, pseudo);
    } catch (_error) {
      return false;
    }
    if (style.display === "none" || style.visibility === "hidden") return false;
    var content = style.content;
    return Boolean(
      (content && content !== "none" && content !== "normal" && content !== "\"\"") ||
        paintedBackground(style) ||
        hasBorder(style) ||
        hasExternalPaint(style)
    );
  }

  function hasBorder(style) {
    return (
      number(style.borderTopWidth, 0) > 0 ||
      number(style.borderRightWidth, 0) > 0 ||
      number(style.borderBottomWidth, 0) > 0 ||
      number(style.borderLeftWidth, 0) > 0
    );
  }

  function paintedBackground(style) {
    var color = parseColor(style.backgroundColor);
    return Boolean(
      (style.backgroundColor && style.backgroundColor !== "transparent" &&
        (!color || color.alpha > 0)) ||
        (style.backgroundImage && style.backgroundImage !== "none")
    );
  }

  function hasExternalPaint(style) {
    return Boolean(
      (style.boxShadow && style.boxShadow !== "none") ||
        (style.textShadow && style.textShadow !== "none") ||
        (style.outlineStyle &&
          style.outlineStyle !== "none" &&
          hasPositiveCssComponent(style.outlineWidth))
    );
  }

  function pseudoSuppressionAttribute(pseudo) {
    return pseudo === "::before"
      ? "data-presenton-authored-hybrid-suppress-before"
      : "data-presenton-authored-hybrid-suppress-after";
  }

  function ensurePseudoSuppressionStyle() {
    var styleId = "__presenton_authored_hybrid_pseudo_suppression__";
    if (document.getElementById(styleId)) return;
    var style = document.createElement("style");
    style.id = styleId;
    style.textContent =
      "[data-presenton-authored-hybrid-suppress-pseudo='true']::before," +
      "[data-presenton-authored-hybrid-suppress-pseudo='true']::after," +
      "[data-presenton-authored-hybrid-suppress-before='true']::before," +
      "[data-presenton-authored-hybrid-suppress-after='true']::after{" +
      "content:none!important;display:none!important;background:none!important;" +
      "border:0!important;outline:0!important;box-shadow:none!important;}";
    document.head.appendChild(style);
  }

  function emptyPseudoContent(style) {
    var content = String(style.content || "").trim();
    return content === "" || content === "none" || content === "normal" ||
      content === "\"\"" || content === "''";
  }

  function pseudoTextContent(style) {
    var content = String(style.content || "").trim();
    if (emptyPseudoContent(style)) return "";
    if (!/^(["']).*\1$/.test(content)) return "";
    var inner = content.slice(1, -1);
    return inner
      .replace(/\\([0-9a-fA-F]{1,6})\s?/g, function (_match, hex) {
        try { return String.fromCodePoint(Number.parseInt(hex, 16)); }
        catch (_error) { return ""; }
      })
      .replace(/\\(.)/g, "$1");
  }

  function pseudoBorderTrianglePayload(style) {
    var sides = ["Top", "Right", "Bottom", "Left"].map(function (side) {
      return {
        side: side.toLowerCase(),
        width: number(style["border" + side + "Width"], 0),
        color: parseColor(style["border" + side + "Color"]),
      };
    });
    var painted = sides.filter(function (side) {
      return side.width > 0 && side.color && side.color.alpha > 0;
    });
    if (painted.length !== 1) return null;
    var transparent = sides.filter(function (side) {
      return side.width > 0 && (!side.color || side.color.alpha <= 0);
    });
    var paintedSide = painted[0];
    var adjacent = paintedSide.side === "left" || paintedSide.side === "right"
      ? ["top", "bottom"]
      : ["left", "right"];
    if (!adjacent.every(function (side) {
      return transparent.some(function (candidate) { return candidate.side === side; });
    })) return null;
    var points = paintedSide.side === "left"
      ? [{ x: 0, y: 0 }, { x: 1, y: 0.5 }, { x: 0, y: 1 }]
      : paintedSide.side === "right"
        ? [{ x: 1, y: 0 }, { x: 0, y: 0.5 }, { x: 1, y: 1 }]
        : paintedSide.side === "top"
          ? [{ x: 0, y: 0 }, { x: 0.5, y: 1 }, { x: 1, y: 0 }]
          : [{ x: 0, y: 1 }, { x: 0.5, y: 0 }, { x: 1, y: 1 }];
    return {
      shape: "freeform",
      fill: paintedSide.color,
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      points: points,
      closed: true,
      preserveContents: false,
    };
  }

  function materializedPseudoShape(element, pseudo, sourceIndex, id) {
    var pseudoStyle;
    try {
      pseudoStyle = getComputedStyle(element, pseudo);
    } catch (_error) {
      return null;
    }
    var pseudoText = pseudoTextContent(pseudoStyle);
    var hasGeometry = paintedBackground(pseudoStyle) || hasBorder(pseudoStyle);
    if (
      pseudoStyle.display === "none" || pseudoStyle.visibility === "hidden" ||
      (!pseudoText && !hasGeometry)
    ) return null;

    ensurePseudoSuppressionStyle();
    var clone = document.createElement("span");
    clone.setAttribute("data-presenton-authored-pseudo-clone", pseudo.slice(2));
    for (var styleIndex = 0; styleIndex < pseudoStyle.length; styleIndex += 1) {
      var property = pseudoStyle.item(styleIndex);
      clone.style.setProperty(
        property,
        pseudoStyle.getPropertyValue(property),
        "important"
      );
    }
    clone.style.setProperty("content", "normal", "important");
    clone.style.setProperty("pointer-events", "none", "important");
    if (pseudoText) clone.textContent = pseudoText;

    var attribute = pseudoSuppressionAttribute(pseudo);
    var previousAttribute = element.getAttribute(attribute);
    if (pseudo === "::before") element.insertBefore(clone, element.firstChild);
    else element.appendChild(clone);
    element.setAttribute(attribute, "true");

    try {
      var transform = analyzeTransform(clone);
      var reasons = ["pseudo-element"];
      var affinePoints = !pseudoText && !transform.safe
        ? affineRectanglePoints(transform, clone.offsetWidth, clone.offsetHeight)
        : null;
      if (!transform.safe && !affinePoints) reasons.push("complex-transform");
      var bounds = unrotatedRect(clone, transform.rotationDeg);
      if (bounds.width <= 0 || bounds.height <= 0 || !Object.values(bounds).every(Number.isFinite)) {
        return null;
      }
      if (
        bounds.x < 0 || bounds.y < 0 ||
        bounds.x + bounds.width > WIDTH || bounds.y + bounds.height > HEIGHT
      ) reasons.push("outside-slide");
      var zIndexText = pseudoStyle.zIndex;
      var zIndex = /^-?\d+$/.test(zIndexText) ? Number.parseInt(zIndexText, 10) : null;
      var observation = {
        id: id,
        domPath: domPath(element) + pseudo,
        tagName: "pseudo",
        sourceIndex: sourceIndex,
        cssZIndex: zIndex,
        boundsPx: bounds,
        rotationDeg: transform.rotationDeg,
        opacity: round(effectiveOpacity(element) * number(pseudoStyle.opacity, 1)),
        candidateKind: pseudoText ? "text" : "shape",
        fallbackReasons: reasons,
      };
      if (pseudoText) {
        var extractedRuns = textRuns(clone);
        var renderedPlainText = extractedRuns.runs.map(function (run) { return run.text; }).join("");
        var plainText = renderedPlainText || pseudoText;
        observation.text = {
          role: inferTextRole(clone, plainText),
          plainText: plainText,
          paragraphs: [plainText],
          style: textStyle(clone, plainText),
          runs: extractedRuns.runs,
        };
        var containerShape = textContainerShape(clone, bounds);
        if (containerShape) observation.text.containerShape = containerShape;
        observation.boundsPx = textContentRect(clone, bounds, transform.rotationDeg);
      } else {
        var pseudoTriangle = pseudoBorderTrianglePayload(getComputedStyle(clone));
        observation.shape = pseudoTriangle || shapePayload(clone, reasons, bounds, false);
        if (affinePoints && observation.shape) {
          observation.shape.shape = "freeform";
          observation.shape.points = affinePoints;
          observation.shape.closed = true;
          observation.shape.radiusPt = 0;
        }
      }
      observation.fallbackReasons = unique(reasons);
      return { element: element, observation: observation, pseudo: pseudo };
    } catch (_pseudoError) {
      return null;
    } finally {
      clone.remove();
      if (previousAttribute === null) element.removeAttribute(attribute);
      else element.setAttribute(attribute, previousAttribute);
    }
  }

  function textContentRect(element, bounds, rotationDeg) {
    // PowerPoint text boxes place glyphs inside their own content area. CSS
    // getBoundingClientRect(), however, includes padding and borders. Starting
    // editable text at that outer edge visibly shifts padded callouts and can
    // make the last line run outside the authored box.
    if (Math.abs(rotationDeg) >= 0.001) return bounds;
    var style = getComputedStyle(element);
    var left = number(style.borderLeftWidth, 0) + number(style.paddingLeft, 0);
    var right = number(style.borderRightWidth, 0) + number(style.paddingRight, 0);
    var top = number(style.borderTopWidth, 0) + number(style.paddingTop, 0);
    var bottom = number(style.borderBottomWidth, 0) + number(style.paddingBottom, 0);
    return {
      x: round(bounds.x + left),
      y: round(bounds.y + top),
      width: round(Math.max(0.5, bounds.width - left - right)),
      height: round(Math.max(0.5, bounds.height - top - bottom)),
    };
  }

  function overflowClips(value) {
    return Boolean(value && value !== "visible");
  }

  function hasScrollableOverflow(element, style) {
    var epsilon = 1;
    return Boolean(
      (overflowClips(style.overflowX) && element.scrollWidth > element.clientWidth + epsilon) ||
        (overflowClips(style.overflowY) && element.scrollHeight > element.clientHeight + epsilon)
    );
  }

  function clippedByAncestor(element, ancestor, style) {
    var epsilon = 0.75;
    var rect = element.getBoundingClientRect();
    var ancestorRect = ancestor.getBoundingClientRect();
    var clipLeft = ancestorRect.left + ancestor.clientLeft;
    var clipTop = ancestorRect.top + ancestor.clientTop;
    var clipRight = clipLeft + ancestor.clientWidth;
    var clipBottom = clipTop + ancestor.clientHeight;
    return Boolean(
      (overflowClips(style.overflowX) &&
        (rect.left < clipLeft - epsilon || rect.right > clipRight + epsilon)) ||
        (overflowClips(style.overflowY) &&
          (rect.top < clipTop - epsilon || rect.bottom > clipBottom + epsilon))
    );
  }

  function isDecomposableSvgDropShadow(element, styledElement, filterValue) {
    if (
      !element || element.namespaceURI !== "http://www.w3.org/2000/svg" ||
      !SVG_SHAPE_TAGS.has(element.tagName.toUpperCase()) ||
      !styledElement || styledElement.namespaceURI !== "http://www.w3.org/2000/svg"
    ) return false;
    var reference = String(filterValue || "").match(
      /url\(\s*["']?(?:[^#"')]*#)?([^"')\s]+)["']?\s*\)/i
    );
    if (!reference) return false;
    var root = element.closest("svg");
    if (!root) return false;
    var filter = root.querySelector("filter#" + CSS.escape(reference[1]));
    if (!filter) return false;
    var primitives = Array.from(filter.children);
    // feDropShadow affects only appearance, not the editable source geometry.
    // PowerPoint may omit this soft shadow, but retaining every primitive as a
    // native shape is preferable to baking the complete illustration into the
    // full-slide residual bitmap. More structural SVG filters stay raster.
    return primitives.length > 0 && primitives.every(function (primitive) {
      return primitive.tagName.toUpperCase() === "FEDROPSHADOW";
    });
  }

  function safetyReasons(element, kind, transform) {
    var reasons = [];
    var current = element;
    while (current) {
      var style = getComputedStyle(current);
      var own = current === element;
      var documentRoot = current === document.body || current === document.documentElement;
      if (style.clipPath && style.clipPath !== "none") reasons.push("clip-path");
      var maskImage = style.maskImage || style.webkitMaskImage;
      if (maskImage && maskImage !== "none") reasons.push("mask");
      if (
        style.filter && style.filter !== "none" &&
        !isDecomposableSvgDropShadow(element, current, style.filter)
      ) reasons.push("filter");
      var backdrop = style.backdropFilter || style.webkitBackdropFilter;
      if (backdrop && backdrop !== "none") reasons.push("backdrop-filter");
      if (style.mixBlendMode && style.mixBlendMode !== "normal") {
        reasons.push("mix-blend-mode");
      }
      if (
        !own &&
        ((style.transform && style.transform !== "none") ||
          hasIndividualTransform(style))
      ) {
        var ancestorTransform = analyzeTransform(current);
        // A pure translation is already included in every descendant's
        // viewport bounds. Rotated/scaled/skewed ancestry still changes the
        // descendant geometry and remains a raster fallback.
        if (!ancestorTransform.safe || Math.abs(ancestorTransform.rotationDeg) >= 0.001) {
          reasons.push("transformed-ancestor");
          reasons.push("unknown-z-order");
        }
      }
      // An authored slide root commonly uses overflow:hidden to define the
      // canvas. Treat it as clipping only when this candidate actually crosses
      // that clip boundary. Replaced images also expose UA overflow:clip, so
      // their own crop safety is handled by object-fit/object-position instead.
      // HTML diagram/timeline wrappers contribute raster paint, but their
      // descendant labels must still be traversed and promoted as editable
      // text. Only intrinsically atomic graphics suppress their descendants.
      if (
        !documentRoot && (!own || kind !== "image") &&
        (own
          ? hasScrollableOverflow(current, style)
          : clippedByAncestor(element, current, style))
      ) {
        reasons.push("overflow-clipped");
      }
      if (style.columnCount !== "auto" && number(style.columnCount, 1) !== 1) {
        reasons.push("css-columns");
      }
      if (style.writingMode && style.writingMode !== "horizontal-tb") {
        reasons.push("vertical-writing");
      }
      if (style.animationName && style.animationName !== "none") {
        reasons.push("animated");
      }
      if (style.zIndex && style.zIndex !== "auto") reasons.push("unknown-z-order");
      if (style.position === "fixed" || style.position === "sticky") {
        reasons.push("unknown-z-order");
      }
      if (
        style.isolation === "isolate" ||
        (style.perspective && style.perspective !== "none") ||
        (style.contain && /(?:paint|layout|strict|content)/.test(style.contain)) ||
        (style.willChange && style.willChange !== "auto" && style.willChange !== "")
      ) {
        reasons.push("unknown-z-order");
      }
      if (!own && number(style.opacity, 1) !== 1) {
        reasons.push("unsupported-opacity");
        reasons.push("unknown-z-order");
      }
      if (current === document.documentElement) break;
      current = current.parentElement;
    }
    if (!transform.safe) reasons.push("complex-transform");
    var ownStyle = getComputedStyle(element);
    var relatedElements = [element].concat(Array.from(element.querySelectorAll("*")));
    var ancestor = element.parentElement;
    while (ancestor) {
      relatedElements.push(ancestor);
      if (ancestor === document.documentElement) break;
      ancestor = ancestor.parentElement;
    }
    relatedElements.forEach(function (target) {
      var targetStyle = getComputedStyle(target);
      if (
        pseudoPainted(target, "::before") ||
        pseudoPainted(target, "::after") ||
        pseudoPainted(target, "::marker")
      ) {
        reasons.push("pseudo-element");
      }
      if (hasExternalPaint(targetStyle)) reasons.push("external-paint");
    });
    if (kind === "text") {
      if (
        ownStyle.display === "list-item" &&
        ownStyle.listStyleType &&
        ownStyle.listStyleType !== "none"
      ) {
        reasons.push("pseudo-element");
      }
      [element].concat(Array.from(element.querySelectorAll("*"))).forEach(function (target) {
        var targetStyle = getComputedStyle(target);
        if (target !== element && targetStyle.clipPath && targetStyle.clipPath !== "none") {
          reasons.push("clip-path");
        }
        var targetMask = targetStyle.maskImage || targetStyle.webkitMaskImage;
        if (target !== element && targetMask && targetMask !== "none") reasons.push("mask");
        if (target !== element && targetStyle.filter && targetStyle.filter !== "none") {
          reasons.push("filter");
        }
        var targetBackdrop = targetStyle.backdropFilter || targetStyle.webkitBackdropFilter;
        if (target !== element && targetBackdrop && targetBackdrop !== "none") {
          reasons.push("backdrop-filter");
        }
        if (targetStyle.mixBlendMode && targetStyle.mixBlendMode !== "normal") {
          reasons.push("mix-blend-mode");
        }
        if (
          target !== element &&
          ((targetStyle.transform && targetStyle.transform !== "none") ||
            hasIndividualTransform(targetStyle))
        ) {
          reasons.push("complex-transform");
        }
        if (
          target !== element &&
          hasScrollableOverflow(target, targetStyle)
        ) {
          reasons.push("overflow-clipped");
        }
        if (target !== element && number(targetStyle.opacity, 1) !== 1) {
          reasons.push("unsupported-opacity");
        }
        if (
          target !== element &&
          targetStyle.columnCount !== "auto" &&
          number(targetStyle.columnCount, 1) !== 1
        ) {
          reasons.push("css-columns");
        }
        if (
          target !== element &&
          targetStyle.writingMode &&
          targetStyle.writingMode !== "horizontal-tb"
        ) {
          reasons.push("vertical-writing");
        }
        if (
          target !== element &&
          targetStyle.animationName &&
          targetStyle.animationName !== "none"
        ) {
          reasons.push("animated");
        }
        if (
          target !== element &&
          ((targetStyle.zIndex && targetStyle.zIndex !== "auto") ||
            targetStyle.position === "fixed" ||
            targetStyle.position === "sticky" ||
            targetStyle.isolation === "isolate" ||
            (targetStyle.perspective && targetStyle.perspective !== "none") ||
            (targetStyle.contain &&
              /(?:paint|layout|strict|content)/.test(targetStyle.contain)) ||
            (targetStyle.willChange &&
              targetStyle.willChange !== "auto" &&
              targetStyle.willChange !== ""))
        ) {
          reasons.push("unknown-z-order");
        }
        if (
          pseudoPainted(target, "::before") ||
          pseudoPainted(target, "::after") ||
          pseudoPainted(target, "::marker")
        ) {
          reasons.push("pseudo-element");
        }
        if (
          paintedBackground(targetStyle) ||
          hasBorder(targetStyle) ||
          targetStyle.boxShadow !== "none"
        ) {
          reasons.push("decorated-text");
        }
        if (targetStyle.textShadow && targetStyle.textShadow !== "none") {
          reasons.push("text-shadow");
        }
        if (
          targetStyle.backgroundClip === "text" ||
          targetStyle.webkitBackgroundClip === "text" ||
          number(targetStyle.webkitTextStrokeWidth, 0) > 0
        ) {
          reasons.push("background-clip-text");
        }
        if (!parseColor(targetStyle.color)) reasons.push("unsupported-color");
      });
    } else if (
      pseudoPainted(element, "::before") ||
      pseudoPainted(element, "::after") ||
      pseudoPainted(element, "::marker")
    ) {
      reasons.push("pseudo-element");
    }
    if (kind === "image") {
      var decoratedImage =
        paintedBackground(ownStyle) ||
        hasBorder(ownStyle) ||
        ownStyle.boxShadow !== "none" ||
        [
          ownStyle.paddingTop,
          ownStyle.paddingRight,
          ownStyle.paddingBottom,
          ownStyle.paddingLeft,
        ].some(function (value) { return number(value, 0) > 0; }) ||
        (ownStyle.outlineStyle !== "none" && number(ownStyle.outlineWidth, 0) > 0);
      if (decoratedImage) {
        reasons.push("decorated-image");
      }
      if (
        [
          ownStyle.borderTopLeftRadius,
          ownStyle.borderTopRightRadius,
          ownStyle.borderBottomRightRadius,
          ownStyle.borderBottomLeftRadius,
        ].some(hasPositiveCssComponent)
      ) {
        reasons.push("rounded-image");
      }
    }
    if (kind === "shape") {
      if (
        ownStyle.boxShadow !== "none" ||
        (ownStyle.outlineStyle !== "none" &&
          hasPositiveCssComponent(ownStyle.outlineWidth))
      ) {
        reasons.push("unsupported-shape");
      }
    }
    return unique(reasons);
  }

  function visibleElement(element) {
    var style = getComputedStyle(element);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.visibility === "collapse" ||
      number(style.opacity, 1) <= 0
    ) {
      return false;
    }
    var rect = element.getBoundingClientRect();
    if (svgShapeElement(element) && typeof element.getTotalLength === "function") {
      try {
        return element.getTotalLength() > 0 && (parseColor(style.stroke) || parseColor(style.fill));
      } catch (_svgLengthError) {
        return false;
      }
    }
    return rect.width >= 0.5 && rect.height >= 0.5;
  }

  var INLINE_TAGS = new Set([
    "A", "ABBR", "B", "BDI", "BDO", "BR", "CITE", "CODE", "DEL", "EM",
    "I", "INS", "KBD", "MARK", "Q", "S", "SAMP", "SMALL", "SPAN", "STRONG",
    "SUB", "SUP", "TIME", "U", "VAR", "WBR", "TSPAN"
  ]);
  var TEXT_ROOT_TAGS = new Set([
    "H1", "H2", "H3", "H4", "H5", "H6", "P", "LI", "FIGCAPTION",
    "BLOCKQUOTE", "LABEL", "SMALL", "TEXT", "TEXTPATH"
  ]);

  function directText(element) {
    return Array.from(element.childNodes).some(function (node) {
      return node.nodeType === Node.TEXT_NODE && Boolean(node.textContent.trim());
    });
  }

  function textRoot(element) {
    var tag = element.tagName.toUpperCase();
    // SVGTextElement does not expose innerText consistently in Chromium.
    // Its textContent is nevertheless rendered text and must be promoted just
    // like an HTML label so it cannot disappear from an editable export.
    var renderedText = element.innerText ||
      (["TEXT", "TEXTPATH"].includes(tag) ? element.textContent : "") || "";
    if (!renderedText.trim()) return false;
    return TEXT_ROOT_TAGS.has(tag) ||
      (directText(element) && !nestedBlockContent(element));
  }

  function nestedBlockContent(element) {
    return Array.from(element.querySelectorAll("*")).some(function (child) {
      return !INLINE_TAGS.has(child.tagName.toUpperCase());
    });
  }

  function hasDecoratedDescendant(element) {
    return Array.from(element.querySelectorAll("*")).some(function (child) {
      var childStyle = getComputedStyle(child);
      return paintedBackground(childStyle) || hasBorder(childStyle) ||
        hasExternalPaint(childStyle) || pseudoPainted(child, "::before") ||
        pseudoPainted(child, "::after");
    });
  }

  function complexKind(element) {
    // SVG DOM tagName values are lowercase in Chromium, unlike HTML tagName.
    var tag = element.tagName.toUpperCase();
    // HTML tables are decomposed into editable table/cell rectangles and text.
    // Canvas and SVG still need a fidelity fallback because their authored
    // primitives are not represented by ordinary DOM boxes.
    if (tag === "SVG") {
      if (decomposableSvgRoot(element)) return null;
      return element.querySelector("text,textPath,foreignObject") ? "svg-text" : "complex-diagram";
    }
    if (tag === "CANVAS") return "complex-chart";
    var role = (element.getAttribute("data-ppt-role") || "").toLowerCase();
    var classes = typeof element.className === "string" ? element.className.toLowerCase() : "";
    if (role === "chart" || /(^|[\s_-])chart([\s_-]|$)/.test(classes)) {
      return "complex-chart";
    }
    if (
      role === "diagram" ||
      /(^|[\s_-])(diagram|flowchart|timeline|mindmap)([\s_-]|$)/.test(classes)
    ) {
      return "complex-diagram";
    }
    // Some authored templates use a semantic .visual wrapper for a large,
    // CSS-built hero illustration instead of an SVG. Its nested browser/card/
    // connector primitives cannot be reconstructed reliably as independent
    // PowerPoint shapes, but the complete illustration should still be a
    // selectable object rather than remain fused into the slide backplate.
    // Require an accessible label and substantial geometry so small helpers
    // such as .visual-title are not accidentally collapsed into pictures.
    if (/(^|\s)visual(\s|$)/.test(classes)) {
      var visualRect = element.getBoundingClientRect();
      if (
        (element.getAttribute("aria-label") || "").trim() &&
        visualRect.width >= 240 && visualRect.height >= 180 &&
        element.querySelectorAll("*").length >= 12
      ) {
        return "complex-diagram";
      }
    }
    return null;
  }

  function inferTextRole(element, plainText) {
    var hint = (element.getAttribute("data-ppt-role") || "").trim().toLowerCase();
    if (hint) {
      if (["title", "body", "numeric", "caption"].includes(hint)) return hint;
      return "unsupported";
    }
    var style = getComputedStyle(element);
    var fontSize = number(style.fontSize, 16);
    if (["H1", "H2"].includes(element.tagName) || (fontSize >= 30 && plainText.length <= 180)) {
      return "title";
    }
    if (/^[\s+\-()%.,:\/\d$\u20ac\u00a3\u00a5\u20a9\u20b9]+$/u.test(plainText) && /\d/.test(plainText)) {
      return "numeric";
    }
    if (element.tagName === "FIGCAPTION" || element.tagName === "SMALL" || fontSize <= 13) {
      return "caption";
    }
    return "body";
  }

  function normalizeWhitespace(value) {
    return value.replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  }

  function textRuns(element) {
    var runs = [];
    var failed = false;
    var activeLineBand = null;

    function rectBand(rect) {
      return {
        top: rect.top,
        bottom: rect.bottom,
        height: Math.max(1, rect.height),
      };
    }

    function sharesVisualLine(rect, band) {
      if (!band) return false;
      var overlap = Math.min(rect.bottom, band.bottom) - Math.max(rect.top, band.top);
      var minimumHeight = Math.min(Math.max(1, rect.height), band.height);
      return overlap >= minimumHeight * 0.25;
    }

    function extendLineBand(band, rect) {
      if (!band) return rectBand(rect);
      // Retain the common vertical band instead of growing a union forever.
      // A union can be stretched by the zero-width space at a browser wrap
      // point until it overlaps the next visual line, losing every subsequent
      // soft break. The intersection remains stable across mixed-size inline
      // runs while separating genuinely different baselines.
      var top = Math.max(band.top, rect.top);
      var bottom = Math.min(band.bottom, rect.bottom);
      if (bottom <= top) return rectBand(rect);
      return { top: top, bottom: bottom, height: Math.max(1, bottom - top) };
    }

    function pushLineBreak(parent, rect, softLineBreak) {
      if (!runs.length || runs[runs.length - 1].text.endsWith("\n")) return;
      runs.push({
        text: "\n",
        boundsPx: { x: round(rect.left), y: round(rect.top), width: 0, height: 0 },
        fragmentRectsPx: [],
        style: textStyle(parent, "\n"),
        softLineBreak: Boolean(softLineBreak),
      });
    }

    function pushTextSlice(node, parent, start, end) {
      if (end <= start) return;
      var text = (node.textContent || "").slice(start, end);
      if (!text) return;
      var range = document.createRange();
      range.setStart(node, start);
      range.setEnd(node, end);
      var fragments = Array.from(range.getClientRects())
        .filter(function (rect) { return rect.width > 0 || rect.height > 0; })
        .map(rectValue);
      runs.push({
        text: text,
        boundsPx: rectValue(range.getBoundingClientRect()),
        fragmentRectsPx: fragments,
        style: textStyle(parent, text),
      });
    }

    function normalizeCollapsibleRunWhitespace() {
      var whiteSpace = getComputedStyle(element).whiteSpace;
      if (["pre", "pre-wrap", "break-spaces"].includes(whiteSpace)) return;
      runs.forEach(function (run) {
        // Newlines used only to pretty-print authored HTML are ordinary
        // collapsible whitespace. Keep the standalone newline runs that we
        // created for browser visual lines, but never turn indentation between
        // inline spans into a PowerPoint paragraph break.
        if (run.text !== "\n") run.text = run.text.replace(/[\t\f\v\r\n ]+/g, " ");
      });
      for (var index = 0; index < runs.length; index += 1) {
        if (runs[index].text !== "\n") continue;
        if (index > 0) runs[index - 1].text = runs[index - 1].text.replace(/ +$/g, "");
        if (index + 1 < runs.length) runs[index + 1].text = runs[index + 1].text.replace(/^ +/g, "");
      }
      for (var adjacent = 1; adjacent < runs.length; adjacent += 1) {
        if (runs[adjacent - 1].text.endsWith(" ") && runs[adjacent].text.startsWith(" ")) {
          runs[adjacent].text = runs[adjacent].text.replace(/^ +/g, "");
        }
      }
      if (runs.length) {
        runs[0].text = runs[0].text.replace(/^ +/g, "");
        runs[runs.length - 1].text = runs[runs.length - 1].text.replace(/ +$/g, "");
      }
      runs = runs.filter(function (run) { return run.text.length > 0; });
    }

    function createsTextLineBoundary(node) {
      if (node === element) return false;
      var parent = node.parentElement;
      if (parent) {
        var parentStyle = getComputedStyle(parent);
        var parentDisplay = parentStyle.display;
        // Flex/grid items are blockified by the browser even when the author
        // used inline spans. A row of labels and arrows must therefore stay on
        // one editable line. Character geometry still detects real wrapping.
        if (
          parentDisplay === "flex" ||
          parentDisplay === "inline-flex" ||
          parentDisplay === "grid" ||
          parentDisplay === "inline-grid"
        ) {
          return false;
        }
      }
      var display = getComputedStyle(node).display;
      return [
        "block",
        "flow-root",
        "flex",
        "grid",
        "list-item",
        "table",
      ].includes(display);
    }

    function hasFollowingTextContent(node) {
      var sibling = node.nextSibling;
      while (sibling) {
        if (
          sibling.nodeType === Node.TEXT_NODE &&
          Boolean((sibling.textContent || "").trim())
        ) {
          return true;
        }
        if (
          sibling.nodeType === Node.ELEMENT_NODE &&
          sibling.tagName !== "BR" &&
          visibleElement(sibling) &&
          Boolean((sibling.innerText || sibling.textContent || "").trim())
        ) {
          return true;
        }
        if (sibling.nodeType === Node.ELEMENT_NODE && sibling.tagName === "BR") {
          return false;
        }
        sibling = sibling.nextSibling;
      }
      return false;
    }

    function visit(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        var text = node.textContent || "";
        if (!text) return;
        try {
          var parent = node.parentElement || element;
          var whiteSpace = getComputedStyle(parent).whiteSpace;
          var preservesSourceLineBreaks = [
            "pre",
            "pre-wrap",
            "break-spaces",
          ].includes(whiteSpace);
          var sliceStart = 0;
          for (var index = 0; index < text.length; index += 1) {
            if (
              preservesSourceLineBreaks &&
              (text[index] === "\n" || text[index] === "\r")
            ) {
              pushTextSlice(node, parent, sliceStart, index);
              pushLineBreak(parent, parent.getBoundingClientRect(), false);
              activeLineBand = null;
              sliceStart = index + 1;
              continue;
            }
            var characterRange = document.createRange();
            characterRange.setStart(node, index);
            characterRange.setEnd(node, index + 1);
            var characterRects = Array.from(characterRange.getClientRects()).filter(function (rect) {
              return rect.width > 0 || rect.height > 0;
            });
            if (!characterRects.length) continue;
            var characterRect = characterRects[0];
            if (activeLineBand && !sharesVisualLine(characterRect, activeLineBand)) {
              pushTextSlice(node, parent, sliceStart, index);
              pushLineBreak(parent, characterRect, true);
              sliceStart = index;
              activeLineBand = rectBand(characterRect);
            } else {
              activeLineBand = extendLineBand(activeLineBand, characterRect);
            }
          }
          pushTextSlice(node, parent, sliceStart, text.length);
        } catch (_error) {
          failed = true;
        }
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      if (node.tagName === "BR") {
        var parent = node.parentElement || element;
        var rect = parent.getBoundingClientRect();
        runs.push({
          text: "\n",
          boundsPx: { x: round(rect.left), y: round(rect.top), width: 0, height: 0 },
          fragmentRectsPx: [],
          style: textStyle(parent, "\n"),
        });
        activeLineBand = null;
        return;
      }
      var lineBoundary = createsTextLineBoundary(node);
      if (lineBoundary && runs.some(function (run) {
        return run.text !== "\n" && Boolean(run.text.trim());
      })) {
        pushLineBreak(node, node.getBoundingClientRect(), false);
        activeLineBand = null;
      }
      Array.from(node.childNodes).forEach(visit);
      if (lineBoundary && hasFollowingTextContent(node)) {
        pushLineBreak(node, node.getBoundingClientRect(), false);
        activeLineBand = null;
      }
    }

    Array.from(element.childNodes).forEach(visit);
    normalizeCollapsibleRunWhitespace();
    var identityText = runs.map(function (run) {
      return run.softLineBreak ? "" : run.text;
    }).join("");
    return { runs: runs, failed: failed, identityText: identityText };
  }

  function imagePayload(element, reasons, bounds) {
    var style = getComputedStyle(element);
    var src = element.currentSrc || element.src || "";
    if (!element.complete || element.naturalWidth <= 0 || element.naturalHeight <= 0) {
      reasons.push("extraction-error");
    }
    if (/\.svg(?:[?#]|$)/i.test(src) || /^data:image\/svg\+xml/i.test(src)) {
      reasons.push("unsupported-image-format");
    }
    var objectFit = style.objectFit || "fill";
    if (!["contain", "cover", "fill", "none", "scale-down"].includes(objectFit)) {
      reasons.push("unsupported-object-fit");
      objectFit = "fill";
    }
    var objectPosition = (style.objectPosition || "50% 50%").trim();
    if (!/^(?:50%|center)(?:\s+(?:50%|center))?$/i.test(objectPosition)) {
      reasons.push("unsupported-object-position");
    }
    var crop = { left: 0, top: 0, right: 0, bottom: 0 };
    if (objectFit === "cover" && bounds.width > 0 && bounds.height > 0 && element.naturalWidth > 0) {
      var boxRatio = bounds.width / bounds.height;
      var imageRatio = element.naturalWidth / element.naturalHeight;
      if (imageRatio > boxRatio) {
        var visibleWidth = boxRatio / imageRatio;
        crop.left = crop.right = round((1 - visibleWidth) / 2);
      } else if (imageRatio < boxRatio) {
        var visibleHeight = imageRatio / boxRatio;
        crop.top = crop.bottom = round((1 - visibleHeight) / 2);
      }
    }
    return {
      src: src,
      alt: element.alt || "",
      naturalWidth: element.naturalWidth || 0,
      naturalHeight: element.naturalHeight || 0,
      objectFit: objectFit,
      objectPosition: objectPosition,
      crop: crop,
    };
  }

  function svgSerializationMarkup(element, bounds) {
    var clone = element.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.setAttribute("width", String(Math.max(1, Math.round(bounds.width))));
    clone.setAttribute("height", String(Math.max(1, Math.round(bounds.height))));
    if (!clone.getAttribute("viewBox")) {
      clone.setAttribute("viewBox", "0 0 " + bounds.width + " " + bounds.height);
    }
    var originals = [element].concat(Array.from(element.querySelectorAll("*")));
    var copies = [clone].concat(Array.from(clone.querySelectorAll("*")));
    var inheritedPaint = [
      "color", "fill", "fill-opacity", "stroke", "stroke-opacity", "stroke-width",
      "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
      "font-family", "font-size", "font-style", "font-weight", "letter-spacing",
      "text-anchor", "dominant-baseline", "paint-order", "vector-effect",
    ];
    originals.forEach(function (source, index) {
      var target = copies[index];
      if (!target) return;
      var style = getComputedStyle(source);
      inheritedPaint.forEach(function (property) {
        var value = style.getPropertyValue(property);
        if (value) target.style.setProperty(property, value);
      });
    });
    return new XMLSerializer().serializeToString(clone);
  }

  async function svgImagePayload(element, reasons, bounds) {
    try {
      var markup = svgSerializationMarkup(element, bounds);
      var source = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
      var image = new Image();
      image.decoding = "sync";
      image.src = source;
      await image.decode();
      var scale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
      var width = Math.max(1, Math.round(bounds.width * scale));
      var height = Math.max(1, Math.round(bounds.height * scale));
      var canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      var context = canvas.getContext("2d");
      if (!context) throw new Error("2d canvas unavailable");
      context.clearRect(0, 0, width, height);
      context.drawImage(image, 0, 0, width, height);
      return {
        src: canvas.toDataURL("image/png"),
        alt: element.getAttribute("aria-label") || element.getAttribute("title") || "",
        naturalWidth: width,
        naturalHeight: height,
        objectFit: "fill",
        objectPosition: "50% 50%",
        crop: { left: 0, top: 0, right: 0, bottom: 0 },
      };
    } catch (_svgImageError) {
      reasons.push("extraction-error");
      return null;
    }
  }

  function htmlDiagramSerializationMarkup(element, bounds) {
    var clone = element.cloneNode(true);
    var originals = [element].concat(Array.from(element.querySelectorAll("*")));
    var copies = [clone].concat(Array.from(clone.querySelectorAll("*")));
    originals.forEach(function (source, index) {
      var target = copies[index];
      if (!target) return;
      var style = getComputedStyle(source);
      for (var propertyIndex = 0; propertyIndex < style.length; propertyIndex += 1) {
        var property = style[propertyIndex];
        var value = style.getPropertyValue(property);
        if (value) target.style.setProperty(property, value, style.getPropertyPriority(property));
      }
      target.removeAttribute(ELEMENT_ATTRIBUTE);
      target.style.setProperty("animation", "none", "important");
      target.style.setProperty("transition", "none", "important");
      // Text remains independently promoted and editable. Keep its layout in
      // this snapshot but clear only the glyph paint so it is not duplicated
      // inside the selectable illustration picture.
      if (directText(source)) {
        target.style.setProperty("color", "transparent", "important");
        target.style.setProperty("-webkit-text-fill-color", "transparent", "important");
        target.style.setProperty("text-decoration-color", "transparent", "important");
        target.style.setProperty("-webkit-text-stroke-color", "transparent", "important");
        target.style.setProperty("text-shadow", "none", "important");
      }
      // SVG glyphs are painted by fill/stroke, not CSS color. A semantic
      // HTML diagram can contain a large inline SVG, so leaving those paints
      // intact duplicates every SVG label beneath the editable text overlay.
      // Keep the text nodes in the clone for layout, but remove their glyph
      // paint from the selectable illustration picture.
      if (
        source.namespaceURI === "http://www.w3.org/2000/svg" &&
        ["TEXT", "TEXTPATH", "TSPAN"].includes(source.tagName.toUpperCase())
      ) {
        target.setAttribute("fill", "transparent");
        target.setAttribute("stroke", "none");
        target.style.setProperty("fill", "transparent", "important");
        target.style.setProperty("stroke", "transparent", "important");
        target.style.setProperty("paint-order", "normal", "important");
      }
    });
    clone.style.setProperty("position", "relative", "important");
    clone.style.setProperty("left", "0", "important");
    clone.style.setProperty("top", "0", "important");
    clone.style.setProperty("right", "auto", "important");
    clone.style.setProperty("bottom", "auto", "important");
    clone.style.setProperty("margin", "0", "important");
    clone.style.setProperty("transform", "none", "important");
    clone.style.setProperty("width", Math.max(1, bounds.width) + "px", "important");
    clone.style.setProperty("height", Math.max(1, bounds.height) + "px", "important");
    var wrapper = document.createElement("div");
    wrapper.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
    wrapper.style.cssText = "position:relative;margin:0;padding:0;width:" +
      Math.max(1, bounds.width) + "px;height:" + Math.max(1, bounds.height) +
      "px;overflow:visible";
    // Inline computed styles preserve ordinary descendants. Retain authored
    // rules as well because CSS illustrations frequently use ::before/::after
    // for connector strokes, dots, and decorative rings.
    var cssText = "";
    Array.from(document.styleSheets).forEach(function (sheet) {
      try {
        cssText += Array.from(sheet.cssRules || []).map(function (rule) {
          return rule.cssText;
        }).join("\n") + "\n";
      } catch (_styleSheetAccessError) {
        // Cross-origin sheets are already reflected by the computed styles;
        // only their pseudo-elements cannot be copied into this local image.
      }
    });
    if (cssText) {
      var styleElement = document.createElement("style");
      styleElement.textContent = cssText;
      wrapper.appendChild(styleElement);
    }
    wrapper.appendChild(clone);
    var content = new XMLSerializer().serializeToString(wrapper);
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' +
      Math.max(1, Math.round(bounds.width)) + '" height="' +
      Math.max(1, Math.round(bounds.height)) + '" viewBox="0 0 ' +
      Math.max(1, bounds.width) + ' ' + Math.max(1, bounds.height) + '">' +
      '<foreignObject x="0" y="0" width="100%" height="100%">' + content +
      '</foreignObject></svg>';
  }

  async function htmlDiagramImagePayload(element, reasons, bounds) {
    try {
      var markup = htmlDiagramSerializationMarkup(element, bounds);
      var source = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(markup);
      var image = new Image();
      image.decoding = "sync";
      image.src = source;
      await image.decode();
      var scale = Math.min(2, Math.max(1, window.devicePixelRatio || 1));
      var width = Math.max(1, Math.round(bounds.width * scale));
      var height = Math.max(1, Math.round(bounds.height * scale));
      var canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      var context = canvas.getContext("2d");
      if (!context) throw new Error("2d canvas unavailable");
      context.clearRect(0, 0, width, height);
      context.drawImage(image, 0, 0, width, height);
      return {
        src: canvas.toDataURL("image/png"),
        alt: element.getAttribute("aria-label") || "",
        naturalWidth: width,
        naturalHeight: height,
        objectFit: "fill",
        objectPosition: "50% 50%",
        crop: { left: 0, top: 0, right: 0, bottom: 0 },
      };
    } catch (_htmlDiagramImageError) {
      reasons.push("extraction-error");
      return null;
    }
  }

  var SVG_SHAPE_TAGS = new Set(["PATH", "LINE", "POLYLINE", "POLYGON", "RECT", "CIRCLE", "ELLIPSE"]);

  function decomposableSvgRoot(element) {
    if (!element || element.tagName.toUpperCase() !== "SVG") return false;
    var rect = element.getBoundingClientRect();
    // Small pictograms are just as valuable to edit as large connectors. Their
    // path primitives are cheap to sample and no longer need to remain baked
    // into the residual PNG.
    if (rect.width < 4 || rect.height < 4) return false;
    // Filter definitions do not make the underlying geometry non-editable.
    // The primitives inside <defs> are ignored by svgShapeElement(), while
    // visible paths/rectangles/circles can still be promoted individually.
    // A filtered group may retain its shadow in the residual layer, but it no
    // longer forces the entire SVG illustration into one full-slide bitmap.
    if (element.querySelector("foreignObject,image,use,mask,pattern")) return false;
    // Large illustrations (browser windows, robots, shields, charts, etc.) rely
    // on exact SVG curve, clipping, and paint-order semantics. Converting dozens
    // of paths into independent PowerPoint freeforms visibly breaks those
    // relationships. Keep such an illustration as one movable picture while
    // continuing to promote compact diagrams and connector SVGs shape-by-shape.
    var visiblePrimitiveCount = Array.from(
      element.querySelectorAll("path,line,polyline,polygon,rect,circle,ellipse")
    ).filter(function (primitive) {
      return !primitive.closest("defs,marker,clipPath,mask,pattern");
    }).length;
    return visiblePrimitiveCount <= 24;
  }

  function svgShapeElement(element) {
    if (!element || element.namespaceURI !== "http://www.w3.org/2000/svg") return false;
    if (!SVG_SHAPE_TAGS.has(element.tagName.toUpperCase())) return false;
    if (element.closest("defs,marker,clipPath,mask,pattern")) return false;
    var root = element.closest("svg");
    return Boolean(root && decomposableSvgRoot(root));
  }

  function svgScale(element) {
    var matrix = typeof element.getScreenCTM === "function" ? element.getScreenCTM() : null;
    if (!matrix) return 1;
    var scaleX = Math.hypot(matrix.a, matrix.b);
    var scaleY = Math.hypot(matrix.c, matrix.d);
    return Math.max(0.01, (scaleX + scaleY) / 2);
  }

  function svgScreenPoint(element, point) {
    var matrix = element.getScreenCTM();
    if (!matrix) return null;
    return {
      x: matrix.a * point.x + matrix.c * point.y + matrix.e,
      y: matrix.b * point.x + matrix.d * point.y + matrix.f,
    };
  }

  function svgSampledPoints(element, reasons) {
    if (typeof element.getTotalLength !== "function" || typeof element.getPointAtLength !== "function") {
      reasons.push("unsupported-shape");
      return [];
    }
    try {
      var total = element.getTotalLength();
      if (!Number.isFinite(total) || total <= 0) {
        reasons.push("invalid-bounds");
        return [];
      }
      var screenLength = total * svgScale(element);
      var nativeBounds = element.getBoundingClientRect();
      var compactArrowLike = ["PATH", "POLYLINE", "POLYGON"].includes(
        element.tagName.toUpperCase()
      ) && nativeBounds.width <= 32 && nativeBounds.height <= 32;
      // Detached chevrons and triangle tips need dense sampling so their true
      // vertex is retained. Sparse equal-distance samples can jump over the
      // middle vertex and leave the PowerPoint arrowhead short of its line.
      var segmentCount = Math.max(
        compactArrowLike ? 32 : 1,
        Math.min(64, Math.ceil(screenLength / 14))
      );
      var points = [];
      for (var index = 0; index <= segmentCount; index += 1) {
        var point = svgScreenPoint(element, element.getPointAtLength(total * index / segmentCount));
        if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
          reasons.push("extraction-error");
          return [];
        }
        if (!points.length || Math.hypot(point.x - points[points.length - 1].x, point.y - points[points.length - 1].y) > 0.05) {
          points.push(point);
        }
      }
      return points;
    } catch (_svgGeometryError) {
      reasons.push("extraction-error");
      return [];
    }
  }

  function svgClosedPath(element) {
    var tag = element.tagName.toUpperCase();
    return tag === "POLYGON" ||
      (tag === "PATH" && /[zZ](?:\s*)$/.test(element.getAttribute("d") || ""));
  }

  function svgCircularPortTargets(root) {
    // The connector SVG and its port elements are often siblings under
    // different layout wrappers, so search the rendered slide rather than only
    // the SVG's immediate parent. The distance gate below keeps the match local.
    var scope = document.body || (root && root.parentElement);
    if (!scope) return [];
    return Array.from(scope.querySelectorAll("*"))
      .filter(function (candidate) {
        if (candidate === root || candidate.closest("svg")) return false;
        var rect = candidate.getBoundingClientRect();
        if (rect.width < 8 || rect.width > 30 || rect.height < 8 || rect.height > 30) return false;
        if (Math.abs(rect.width - rect.height) > 3) return false;
        var style = getComputedStyle(candidate);
        if (style.display === "none" || style.visibility === "hidden" || number(style.opacity, 1) <= 0) return false;
        var radius = Math.max(
          number(style.borderTopLeftRadius, 0),
          number(style.borderTopRightRadius, 0),
          number(style.borderBottomRightRadius, 0),
          number(style.borderBottomLeftRadius, 0)
        );
        var borderWidth = Math.max(
          number(style.borderTopWidth, 0),
          number(style.borderRightWidth, 0),
          number(style.borderBottomWidth, 0),
          number(style.borderLeftWidth, 0)
        );
        var semanticPort = /(^|\s|[-_])port(?:\s|$|[-_])/i.test(
          typeof candidate.className === "string" ? candidate.className : ""
        ) || candidate.hasAttribute("data-port");
        return semanticPort ||
          (radius >= Math.min(rect.width, rect.height) * 0.4 && borderWidth >= 1);
      })
      .map(function (candidate) {
        var rect = candidate.getBoundingClientRect();
        return {
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
          radius: Math.min(rect.width, rect.height) / 2,
        };
      });
  }

  function svgCardBoundaryTargets(root) {
    var scope = document.body || (root && root.parentElement);
    if (!scope) return [];
    return Array.from(scope.querySelectorAll("*"))
      .filter(function (candidate) {
        if (
          candidate === root || candidate.closest("svg") ||
          candidate.contains(root) || root.contains(candidate)
        ) return false;
        var rect = candidate.getBoundingClientRect();
        if (rect.width < 60 || rect.width > 700 || rect.height < 36 || rect.height > 500) return false;
        var style = getComputedStyle(candidate);
        if (style.display === "none" || style.visibility === "hidden" || number(style.opacity, 1) <= 0) return false;
        var borderWidth = Math.max(
          number(style.borderTopWidth, 0),
          number(style.borderRightWidth, 0),
          number(style.borderBottomWidth, 0),
          number(style.borderLeftWidth, 0)
        );
        return borderWidth >= 0.75 && style.borderStyle !== "none";
      })
      .map(function (candidate) {
        var rect = candidate.getBoundingClientRect();
        return {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
        };
      });
  }

  function svgStandaloneArrowhead(element) {
    var tag = element.tagName.toUpperCase();
    if (!["PATH", "POLYLINE", "POLYGON"].includes(tag)) return false;
    var rect = element.getBoundingClientRect();
    if (rect.width > 32 || rect.height > 32 || rect.width < 3 || rect.height < 3) return false;
    var style = getComputedStyle(element);
    var fill = parseColor(style.fill);
    var stroke = parseColor(style.stroke);
    if (svgClosedPath(element)) {
      return Boolean(fill && fill.alpha > 0 && (!stroke || stroke.alpha <= 0));
    }
    return Boolean(stroke && stroke.alpha > 0 && (!fill || fill.alpha <= 0));
  }

  function svgSnapEndpointToCard(endpoint, adjacent, cards) {
    var dx = endpoint.x - adjacent.x;
    var dy = endpoint.y - adjacent.y;
    if (Math.hypot(dx, dy) < 0.5) return false;
    var horizontal = Math.abs(dx) >= Math.abs(dy);
    var best = null;
    cards.forEach(function (card) {
      var distance = null;
      var x = endpoint.x;
      var y = endpoint.y;
      if (horizontal && dx > 0 && endpoint.y >= card.top - 4 && endpoint.y <= card.bottom + 4) {
        distance = card.left - endpoint.x;
        x = card.left;
        y = clamp(endpoint.y, card.top, card.bottom);
      } else if (horizontal && dx < 0 && endpoint.y >= card.top - 4 && endpoint.y <= card.bottom + 4) {
        distance = endpoint.x - card.right;
        x = card.right;
        y = clamp(endpoint.y, card.top, card.bottom);
      } else if (!horizontal && dy > 0 && endpoint.x >= card.left - 4 && endpoint.x <= card.right + 4) {
        distance = card.top - endpoint.y;
        x = clamp(endpoint.x, card.left, card.right);
        y = card.top;
      } else if (!horizontal && dy < 0 && endpoint.x >= card.left - 4 && endpoint.x <= card.right + 4) {
        distance = endpoint.y - card.bottom;
        x = clamp(endpoint.x, card.left, card.right);
        y = card.bottom;
      }
      if (distance !== null && distance >= 0.5 && distance <= 56 && (!best || distance < best.distance)) {
        best = { distance: distance, x: x, y: y };
      }
    });
    if (!best) return false;
    endpoint.x = best.x;
    endpoint.y = best.y;
    return true;
  }

  function svgConnectorRenderedPoints(element, reasons) {
    var points = svgSampledPoints(element, reasons);
    if (points.length < 2 || svgClosedPath(element)) return points;
    if (svgStandaloneArrowhead(element)) return points;
    var style = getComputedStyle(element);
    var stroke = parseColor(style.stroke);
    if (!stroke || stroke.alpha <= 0) return points;
    // The authored SVG path is the visual source of truth. Snapping endpoints
    // to nearby HTML cards/ports or rebuilding rounded paths as orthogonal
    // routes changes the visible bends and arrow alignment in PowerPoint.
    // Dense path sampling already produces editable freeforms, so preserve the
    // original path coordinates exactly and let normal layer ordering hide any
    // intentional connector overlap beneath cards or ports.
    return points;
  }

  function svgArrowheadTranslation(element) {
    if (!svgStandaloneArrowhead(element)) return { x: 0, y: 0 };
    var style = getComputedStyle(element);
    var fill = parseColor(style.fill);
    var stroke = parseColor(style.stroke);
    if (
      svgClosedPath(element) &&
      (!fill || fill.alpha <= 0 || (stroke && stroke.alpha > 0))
    ) {
      return { x: 0, y: 0 };
    }
    var rect = element.getBoundingClientRect();
    if (rect.width > 24 || rect.height > 24) return { x: 0, y: 0 };
    var root = element.closest("svg");
    if (!root) return { x: 0, y: 0 };
    var ownPoints = svgSampledPoints(element, []);
    if (!ownPoints.length) return { x: 0, y: 0 };

    var best = null;
    Array.from(root.querySelectorAll("path,line,polyline")).forEach(function (candidate) {
      if (candidate === element || svgClosedPath(candidate)) return;
      var candidateStyle = getComputedStyle(candidate);
      var candidateStroke = parseColor(candidateStyle.stroke);
      if (!candidateStroke || candidateStroke.alpha <= 0) return;
      var candidatePoints = svgConnectorRenderedPoints(candidate, []);
      if (candidatePoints.length < 2) return;
      [0, candidatePoints.length - 1].forEach(function (endpointIndex) {
        var endpoint = candidatePoints[endpointIndex];
        var directionIndex = endpointIndex === 0 ? 1 : candidatePoints.length - 2;
        var directionStep = endpointIndex === 0 ? 1 : -1;
        while (
          directionIndex >= 0 && directionIndex < candidatePoints.length &&
          Math.hypot(
            candidatePoints[directionIndex].x - endpoint.x,
            candidatePoints[directionIndex].y - endpoint.y
          ) < 36
        ) {
          directionIndex += directionStep;
        }
        if (directionIndex < 0 || directionIndex >= candidatePoints.length) {
          directionIndex = endpointIndex === 0 ? 1 : candidatePoints.length - 2;
        }
        var directionPoint = candidatePoints[directionIndex];
        var dx = endpoint.x - directionPoint.x;
        var dy = endpoint.y - directionPoint.y;
        var axis = Math.abs(dx) >= Math.abs(dy) ? "x" : "y";
        var sign = (axis === "x" ? dx : dy) >= 0 ? 1 : -1;
        var extreme = sign > 0
          ? Math.max.apply(Math, ownPoints.map(function (point) { return point[axis]; }))
          : Math.min.apply(Math, ownPoints.map(function (point) { return point[axis]; }));
        ownPoints.filter(function (point) {
          return Math.abs(point[axis] - extreme) <= 0.75;
        }).forEach(function (point) {
          var distance = Math.hypot(endpoint.x - point.x, endpoint.y - point.y);
          if (distance <= 64 && (!best || distance < best.distance)) {
            best = {
              distance: distance,
              x: endpoint.x - point.x,
              y: endpoint.y - point.y,
            };
          }
        });
      });
    });
    return best && best.distance > 0.5
      ? { x: best.x, y: best.y }
      : { x: 0, y: 0 };
  }

  function svgRenderedPoints(element, reasons) {
    return svgClosedPath(element)
      ? svgSampledPoints(element, reasons)
      : svgConnectorRenderedPoints(element, reasons);
  }

  function svgShapeBounds(element, reasons) {
    var tag = element.tagName.toUpperCase();
    if (["RECT", "CIRCLE", "ELLIPSE"].includes(tag)) {
      var nativeRect = element.getBoundingClientRect();
      return rectValue(nativeRect);
    }
    var points = svgRenderedPoints(element, reasons);
    if (!points.length) return rectValue(element.getBoundingClientRect());
    var xs = points.map(function (point) { return point.x; });
    var ys = points.map(function (point) { return point.y; });
    var style = getComputedStyle(element);
    var padding = Math.max(0.5, number(style.strokeWidth, 1) * svgScale(element) / 2);
    var left = Math.min.apply(Math, xs);
    var right = Math.max.apply(Math, xs);
    var top = Math.min.apply(Math, ys);
    var bottom = Math.max.apply(Math, ys);
    if (right - left < 0.5) {
      left -= padding;
      right += padding;
    }
    if (bottom - top < 0.5) {
      top -= padding;
      bottom += padding;
    }
    return {
      x: round(left),
      y: round(top),
      width: round(Math.max(0.5, right - left)),
      height: round(Math.max(0.5, bottom - top)),
    };
  }

  function svgShapePayload(element, reasons, bounds) {
    var style = getComputedStyle(element);
    var tag = element.tagName.toUpperCase();
    var fill = parseColor(style.fill);
    var stroke = parseColor(style.stroke);
    // SVG exposes fill/stroke opacity separately from the color itself.
    // Folding both values together here is important because DrawingML stores
    // the effective alpha on the color; otherwise very faint authored guide
    // lines become fully opaque after export.
    if (fill) {
      fill.alpha = round(clamp(fill.alpha * number(style.fillOpacity, 1), 0, 1));
    }
    if (stroke) {
      stroke.alpha = round(clamp(stroke.alpha * number(style.strokeOpacity, 1), 0, 1));
    }
    if (fill && fill.alpha === 0) fill = null;
    if (stroke && stroke.alpha === 0) stroke = null;
    var strokeWidthPt = stroke
      ? round(Math.max(0.1, number(style.strokeWidth, 1) * svgScale(element) * 0.75))
      : 0;
    var dashValues = String(style.strokeDasharray || "")
      .trim()
      .split(/[\s,]+/)
      .map(function (value) { return number(value, Number.NaN); })
      .filter(function (value) { return Number.isFinite(value) && value > 0; });
    var dash = dashValues.length
      ? (dashValues[0] <= Math.max(1, number(style.strokeWidth, 1) * 1.5) ? "dot" : "dash")
      : undefined;
    var closed = svgClosedPath(element);
    var shape = tag === "RECT"
      ? ((number(element.getAttribute("rx"), 0) > 0 || number(element.getAttribute("ry"), 0) > 0)
        ? "round-rectangle"
        : "rectangle")
      : (["CIRCLE", "ELLIPSE"].includes(tag) ? "ellipse" : "freeform");
    var payload = {
      shape: shape,
      fill: closed || shape !== "freeform" ? fill : null,
      stroke: stroke,
      strokeWidthPt: strokeWidthPt,
      dash: dash,
      lineCap: style.strokeLinecap === "round" ? "round" : undefined,
      lineJoin: style.strokeLinejoin === "round" ? "round" : undefined,
      radiusPt: shape === "round-rectangle"
        ? round(Math.max(number(element.getAttribute("rx"), 0), number(element.getAttribute("ry"), 0)) * svgScale(element) * 0.75)
        : 0,
      endArrow:
        ((style.markerEnd && style.markerEnd !== "none") ||
          (element.getAttribute("marker-end") && element.getAttribute("marker-end") !== "none"))
          ? "triangle"
          : undefined,
      preserveContents: false,
    };
    if (shape === "freeform") {
      var points = svgRenderedPoints(element, reasons);
      payload.points = points.map(function (point) {
        return {
          x: round(clamp((point.x - bounds.x) / Math.max(bounds.width, 0.01), 0, 1)),
          y: round(clamp((point.y - bounds.y) / Math.max(bounds.height, 0.01), 0, 1)),
        };
      });
      payload.closed = closed;
      if (payload.points.length < 2) reasons.push("unsupported-shape");
    }
    if (!payload.fill && !payload.stroke) reasons.push("unsupported-shape");
    return payload;
  }

  function simpleBoxShadowLayers(style) {
    if (!style.boxShadow || style.boxShadow === "none") return [];
    return splitCssList(style.boxShadow).flatMap(function (shadow) {
      if (/\binset\b/i.test(shadow)) return [];
      var colorText = shadow.match(/rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}\b/i);
      var color = colorText ? parseColor(colorText[0]) : null;
      if (!color || color.alpha <= 0) return [];
      var lengths = (shadow.match(/-?(?:\d+(?:\.\d+)?|\.\d+)px/g) || []).map(function (value) {
        return number(value, 0);
      });
      if (lengths.length < 2) return [];
      var blur = Math.max(0, lengths[2] || 0);
      // A blurred shadow has no exact editable PowerPoint equivalent. Keep it
      // in the fidelity backplate; solid shadows can be reconstructed exactly
      // as independent editable shapes.
      if (blur > 0.01) return [];
      return [{
        offsetXPx: round(lengths[0]),
        offsetYPx: round(lengths[1]),
        spreadPx: round(lengths[3] || 0),
        color: color,
      }];
    });
  }

  function roundedRectanglePoints(bounds, inputRadii) {
    var width = Math.max(0.01, bounds.width);
    var height = Math.max(0.01, bounds.height);
    var radii = inputRadii.map(function (radius) {
      return clamp(radius, 0, Math.min(width, height) / 2);
    });
    var scale = Math.min(
      1,
      width / Math.max(0.01, radii[0] + radii[1]),
      width / Math.max(0.01, radii[3] + radii[2]),
      height / Math.max(0.01, radii[0] + radii[3]),
      height / Math.max(0.01, radii[1] + radii[2])
    );
    radii = radii.map(function (radius) { return radius * scale; });
    var points = [];
    function appendArc(centerX, centerY, radius, startDeg, endDeg) {
      var steps = radius > 0.01 ? 5 : 1;
      for (var step = 0; step <= steps; step += 1) {
        var angle = (startDeg + ((endDeg - startDeg) * step) / steps) * Math.PI / 180;
        points.push({
          x: round(clamp((centerX + Math.cos(angle) * radius) / width, 0, 1)),
          y: round(clamp((centerY + Math.sin(angle) * radius) / height, 0, 1)),
        });
      }
    }
    appendArc(radii[0], radii[0], radii[0], 180, 270);
    appendArc(width - radii[1], radii[1], radii[1], 270, 360);
    appendArc(width - radii[2], height - radii[2], radii[2], 0, 90);
    appendArc(radii[3], height - radii[3], radii[3], 90, 180);
    return points.filter(function (point, index) {
      return index === 0 || point.x !== points[index - 1].x || point.y !== points[index - 1].y;
    });
  }

  function shapePayload(element, reasons, bounds, preserveContents) {
    if (svgShapeElement(element)) {
      return svgShapePayload(element, reasons, bounds);
    }
    var style = getComputedStyle(element);
    var backgroundImage = style.backgroundImage || "none";
    var gradient = backgroundImage !== "none"
      ? parseLinearGradient(backgroundImage, bounds)
      : null;
    if (backgroundImage !== "none" && !gradient) {
      reasons.push("unsupported-background");
    }
    var fill = parseColor(style.backgroundColor);
    if (!fill && style.backgroundColor && style.backgroundColor !== "transparent") {
      reasons.push("unsupported-color");
    }
    if (fill && fill.alpha === 0) fill = null;
    var widths = [
      number(style.borderTopWidth, 0),
      number(style.borderRightWidth, 0),
      number(style.borderBottomWidth, 0),
      number(style.borderLeftWidth, 0),
    ];
    var borderStyles = [
      style.borderTopStyle,
      style.borderRightStyle,
      style.borderBottomStyle,
      style.borderLeftStyle,
    ];
    var borderColorValues = [
      style.borderTopColor,
      style.borderRightColor,
      style.borderBottomColor,
      style.borderLeftColor,
    ];
    var borderColors = borderColorValues.map(parseColor);
    var sides = ["top", "right", "bottom", "left"];
    var activeBorders = sides.map(function (side, index) {
      return {
        side: side,
        width: widths[index],
        style: borderStyles[index],
        color: borderColors[index],
      };
    }).filter(function (border) {
      return border.width > 0.01 && border.style !== "none";
    });
    if (activeBorders.some(function (border) {
      return !["solid", "dashed", "dotted"].includes(border.style);
    })) {
      reasons.push("unsupported-shape");
    }
    if (activeBorders.some(function (border) { return !border.color; })) {
      reasons.push("unsupported-color");
    }
    var uniformBorder = activeBorders.length === 4 && activeBorders.every(function (border) {
      var first = activeBorders[0];
      return ["solid", "dashed", "dotted"].includes(border.style) &&
        border.style === first.style && Boolean(border.color) && Boolean(first.color) &&
        Math.abs(border.width - first.width) < 0.01 &&
        border.color.hex === first.color.hex && border.color.alpha === first.color.alpha;
    });
    var stroke = uniformBorder ? activeBorders[0].color : null;
    var borderLines = uniformBorder ? [] : activeBorders.filter(function (border) {
      return ["solid", "dashed", "dotted"].includes(border.style) && border.color && border.color.alpha > 0;
    }).map(function (border) {
      return {
        side: border.side,
        color: border.color,
        widthPt: round(border.width * 0.75),
        dash: border.style === "dashed" ? "dash" : border.style === "dotted" ? "dot" : undefined,
      };
    });
    if (stroke && stroke.alpha === 0) stroke = null;
    if (!fill && !gradient && !stroke && borderLines.length === 0) reasons.push("unsupported-shape");
    var radiusValues = [
      style.borderTopLeftRadius,
      style.borderTopRightRadius,
      style.borderBottomRightRadius,
      style.borderBottomLeftRadius,
    ];
    var radiusPattern = /^([\d.]+)(px|%)$/;
    var radii = radiusValues.map(function (value) {
      var match = String(value).match(radiusPattern);
      if (!match) return Number.NaN;
      return match[2] === "%"
        ? (number(match[1], Number.NaN) / 100) * Math.min(bounds.width, bounds.height)
        : number(match[1], Number.NaN);
    });
    if (radii.some(function (radius) { return !Number.isFinite(radius); })) {
      reasons.push("unsupported-shape");
    }
    var nonUniformRadii = radii.every(Number.isFinite) &&
      radii.some(function (radius) { return Math.abs(radius - radii[0]) >= 0.01; });
    var radiusPx = Number.isFinite(radii[0])
      ? Math.min(radii[0], bounds.width / 2, bounds.height / 2)
      : 0;
    var shape = "rectangle";
    if (Math.min(bounds.width, bounds.height) <= 3 && Math.max(bounds.width, bounds.height) >= 8) {
      shape = "line";
    } else if (
      radiusValues.every(function (value) { return /^(?:50(?:\.0+)?)%$/.test(String(value)); }) ||
      (Math.abs(bounds.width - bounds.height) <= 1 &&
        radiusPx >= Math.min(bounds.width, bounds.height) / 2 - 0.5)
    ) {
      shape = "ellipse";
    } else if (nonUniformRadii) {
      shape = "freeform";
    } else if (radiusPx > 0) {
      shape = "round-rectangle";
    }
    var strokeWidthPt = uniformBorder
      ? round(activeBorders[0].width * 0.75)
      : 0;
    if (shape === "line" && !stroke && fill) {
      stroke = fill;
      fill = null;
      strokeWidthPt = round(Math.max(0.75, Math.min(bounds.width, bounds.height) * 0.75));
    }
    var payload = {
      shape: shape,
      fill: fill,
      gradient: gradient || undefined,
      stroke: stroke,
      strokeWidthPt: strokeWidthPt,
      dash: uniformBorder
        ? activeBorders[0].style === "dashed"
          ? "dash"
          : activeBorders[0].style === "dotted"
            ? "dot"
            : undefined
        : undefined,
      radiusPt: round(radiusPx * 0.75),
      borderLines: borderLines,
      shadowLayers: simpleBoxShadowLayers(style),
      // CSS border triangles are materialized as independent editable
      // freeforms. Attaching another PowerPoint arrow to the host line would
      // duplicate and distort the authored tip.
      endArrow: undefined,
      preserveContents: Boolean(preserveContents),
    };
    if (shape === "freeform" && nonUniformRadii) {
      payload.points = roundedRectanglePoints(bounds, radii);
      payload.closed = true;
      payload.radiusPt = 0;
    }
    return payload;
  }

  function textContainerShape(element, bounds) {
    var style = getComputedStyle(element);
    if (!paintedBackground(style) && !hasBorder(style)) return null;
    if (
      (style.boxShadow && style.boxShadow !== "none") ||
      (style.outlineStyle && style.outlineStyle !== "none" && hasPositiveCssComponent(style.outlineWidth)) ||
      nestedBlockContent(element)
    ) {
      return null;
    }
    if (hasDecoratedDescendant(element)) return null;
    var shapeReasons = [];
    var shape = shapePayload(element, shapeReasons, bounds, false);
    return shapeReasons.length ? null : { boundsPx: bounds, shape: shape };
  }

  function contentSafeShapeBounds(element, bounds) {
    var style = getComputedStyle(element);
    if (
      !nestedBlockContent(element) ||
      (!paintedBackground(style) && !hasBorder(style)) ||
      !["visible", "auto"].includes(style.overflowY)
    ) return bounds;

    var maximumBottom = bounds.y;
    Array.from(element.querySelectorAll("*")).forEach(function (descendant) {
      var descendantStyle = getComputedStyle(descendant);
      if (
        descendantStyle.display === "none" ||
        descendantStyle.visibility === "hidden" ||
        ["absolute", "fixed"].includes(descendantStyle.position)
      ) return;
      var rect = descendant.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return;
      maximumBottom = Math.max(maximumBottom, rect.bottom);
    });

    var desiredBottom = maximumBottom + Math.max(0, number(style.paddingBottom, 0));
    var extra = desiredBottom - (bounds.y + bounds.height);
    // This is a small card-content correction, not a general layout reflow.
    // A larger overflow is more likely to be intentional and stays authored.
    if (extra <= 0.5 || extra > 36) return bounds;
    return {
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: round(bounds.height + extra),
    };
  }

  function looksLikeShape(element) {
    if (svgShapeElement(element)) return true;
    var hint = (element.getAttribute("data-ppt-role") || "").toLowerCase();
    if (hint === "shape") return true;
    if (![
      "DIV", "SPAN", "I", "HR", "SECTION", "ARTICLE", "ASIDE", "HEADER", "FOOTER",
      "MAIN", "NAV", "FIGURE", "TABLE", "THEAD", "TBODY", "TFOOT", "TR",
      "TD", "TH", "UL", "OL", "LI", "BODY"
    ].includes(element.tagName)) return false;
    if (element.tagName === "SPAN" && (element.textContent || "").trim()) return false;
    var style = getComputedStyle(element);
    return paintedBackground(style) || hasBorder(style);
  }

  function paintedForeignElement(element) {
    var style = getComputedStyle(element);
    if (!visibleElement(element)) return false;
    if (paintedBackground(style) || hasBorder(style) || style.boxShadow !== "none") return true;
    var tag = element.tagName.toUpperCase();
    if (element.namespaceURI === "http://www.w3.org/2000/svg") return true;
    if (["IMG", "SVG", "CANVAS", "VIDEO", "IFRAME", "OBJECT", "EMBED", "PICTURE"].includes(tag)) return true;
    if (pseudoPainted(element, "::before") || pseudoPainted(element, "::after")) return true;
    return directText(element);
  }

  function samplePoints(observation) {
    var rects = observationRects(observation);
    var points = [];
    rects.slice(0, 12).forEach(function (rect) {
      points.push([rect.x + rect.width / 2, rect.y + rect.height / 2]);
      if (rect.width > 6 && rect.height > 6) {
        // Quarter points remain inside rounded rectangles and ellipses. Pixel
        // corner probes can fall outside their hit region and falsely report
        // the underlying card as an occluder.
        points.push([rect.x + rect.width * 0.25, rect.y + rect.height * 0.25]);
        points.push([rect.x + rect.width * 0.75, rect.y + rect.height * 0.25]);
        points.push([rect.x + rect.width * 0.25, rect.y + rect.height * 0.75]);
        points.push([rect.x + rect.width * 0.75, rect.y + rect.height * 0.75]);
      }
    });
    return points.filter(function (point) {
      return point[0] >= 0 && point[0] < WIDTH && point[1] >= 0 && point[1] < HEIGHT;
    });
  }

  function observationRects(observation) {
    var rects = [];
    if (observation.text && observation.text.runs) {
      observation.text.runs.forEach(function (run) {
        run.fragmentRectsPx.forEach(function (rect) { rects.push(rect); });
      });
    }
    if (!rects.length) rects.push(observation.boundsPx);
    return rects.filter(function (rect) {
      return rect.width > 0 && rect.height > 0;
    });
  }

  function intersectionCenter(left, right) {
    var x1 = Math.max(left.x, right.x);
    var y1 = Math.max(left.y, right.y);
    var x2 = Math.min(left.x + left.width, right.x + right.width);
    var y2 = Math.min(left.y + left.height, right.y + right.height);
    if (x2 <= x1 || y2 <= y1) return null;
    return [(x1 + x2) / 2, (y1 + y2) / 2];
  }

  function stackIndexForElement(stack, element) {
    return stack.findIndex(function (item) {
      return item === element || element.contains(item) ||
        (element.namespaceURI === "http://www.w3.org/2000/svg" &&
          item.namespaceURI === "http://www.w3.org/2000/svg" &&
          item.contains(element));
    });
  }

  function isOccluded(element, observation) {
    return samplePoints(observation).some(function (point) {
      var stack = document.elementsFromPoint(point[0], point[1]);
      var ownIndex = stack.findIndex(function (item) {
        return item === element || element.contains(item) ||
          (element.namespaceURI === "http://www.w3.org/2000/svg" &&
            item.namespaceURI === "http://www.w3.org/2000/svg" &&
            item.contains(element));
      });
      if (ownIndex < 0) return true;
      return stack.slice(0, ownIndex).some(function (item) {
        if (element.contains(item) || item.contains(element)) return false;
        return paintedForeignElement(item);
      });
    });
  }

  function isRectOccluded(element, observation, allElements) {
    var ownRects = observationRects(observation);
    return allElements.some(function (foreign) {
      if (
        foreign === element ||
        element.contains(foreign) ||
        foreign.contains(element) ||
        !paintedForeignElement(foreign)
      ) {
        return false;
      }
      var foreignRects = Array.from(foreign.getClientRects()).map(rectValue);
      return ownRects.some(function (ownRect) {
        return foreignRects.some(function (foreignRect) {
          var point = intersectionCenter(ownRect, foreignRect);
          if (!point) return false;
          var stack = document.elementsFromPoint(point[0], point[1]);
          var ownIndex = stackIndexForElement(stack, element);
          var foreignIndex = stackIndexForElement(stack, foreign);
          return ownIndex >= 0 && foreignIndex >= 0 && foreignIndex < ownIndex;
        });
      });
    });
  }

  function pseudoPaintRegions(element, pseudo) {
    if (!pseudoPainted(element, pseudo)) return [];
    var style = getComputedStyle(element, pseudo);
    if (
      (style.transform && style.transform !== "none") ||
      hasIndividualTransform(style) ||
      (style.filter && style.filter !== "none") ||
      hasExternalPaint(style)
    ) {
      return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
    }
    if (style.position === "static") {
      return Array.from(element.getClientRects()).map(rectValue);
    }
    if (style.position !== "absolute" && style.position !== "fixed") {
      return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
    }

    var reference = { x: 0, y: 0, width: WIDTH, height: HEIGHT };
    if (style.position === "absolute") {
      var containingBlock = element;
      while (
        containingBlock &&
        getComputedStyle(containingBlock).position === "static"
      ) {
        containingBlock = containingBlock.parentElement;
      }
      if (containingBlock) {
        var containingRect = containingBlock.getBoundingClientRect();
        reference = {
          x: containingRect.left + containingBlock.clientLeft,
          y: containingRect.top + containingBlock.clientTop,
          width: containingBlock.clientWidth,
          height: containingBlock.clientHeight,
        };
      }
    }

    var width = pixelLength(style.width);
    var height = pixelLength(style.height);
    if (!Number.isFinite(width) || !Number.isFinite(height)) {
      return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
    }
    if (style.boxSizing !== "border-box") {
      width +=
        number(style.paddingLeft, 0) +
        number(style.paddingRight, 0) +
        number(style.borderLeftWidth, 0) +
        number(style.borderRightWidth, 0);
      height +=
        number(style.paddingTop, 0) +
        number(style.paddingBottom, 0) +
        number(style.borderTopWidth, 0) +
        number(style.borderBottomWidth, 0);
    }
    var left = pixelLength(style.left);
    var right = pixelLength(style.right);
    var top = pixelLength(style.top);
    var bottom = pixelLength(style.bottom);
    var x = Number.isFinite(left)
      ? reference.x + left
      : Number.isFinite(right)
        ? reference.x + reference.width - right - width
        : Number.NaN;
    var y = Number.isFinite(top)
      ? reference.y + top
      : Number.isFinite(bottom)
        ? reference.y + reference.height - bottom - height
        : Number.NaN;
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
      return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
    }
    return [{ x: round(x), y: round(y), width: round(width), height: round(height) }];
  }

  function isPseudoOccluded(element, observation, allElements) {
    var ownRects = observationRects(observation);
    return allElements.some(function (foreign) {
      if (foreign === element || element.contains(foreign)) return false;
      return ["::before", "::after"].some(function (pseudo) {
        var regions = pseudoPaintRegions(foreign, pseudo);
        if (!regions.length) return false;
        if (!foreign.contains(element) && !couldPseudoPaintAfter(element, foreign, pseudo)) {
          return false;
        }
        return regions.some(function (region) {
          return ownRects.some(function (ownRect) {
            return Boolean(intersectionCenter(ownRect, region));
          });
        });
      });
    });
  }

  function splitCssList(value) {
    var values = [];
    var start = 0;
    var depth = 0;
    for (var index = 0; index < value.length; index += 1) {
      if (value[index] === "(") depth += 1;
      else if (value[index] === ")") depth = Math.max(0, depth - 1);
      else if (value[index] === "," && depth === 0) {
        values.push(value.slice(start, index).trim());
        start = index + 1;
      }
    }
    values.push(value.slice(start).trim());
    return values.filter(Boolean);
  }

  function outerShadowRegions(element) {
    var style = getComputedStyle(element);
    if (!style.boxShadow || style.boxShadow === "none") return [];
    var rect = rectValue(element.getBoundingClientRect());
    return splitCssList(style.boxShadow).flatMap(function (shadow) {
      if (/\binset\b/i.test(shadow)) return [];
      var colorText = shadow.match(/rgba?\([^)]*\)/i);
      var color = colorText ? parseColor(colorText[0]) : null;
      if (color && color.alpha <= 0) return [];
      var lengths = (shadow.match(/-?(?:\d+(?:\.\d+)?|\.\d+)px/g) || []).map(function (value) {
        return number(value, 0);
      });
      if (lengths.length < 2) {
        return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
      }
      var offsetX = lengths[0];
      var offsetY = lengths[1];
      var blur = Math.max(0, lengths[2] || 0);
      var spread = lengths[3] || 0;
      var reach = Math.max(0, blur * 2 + spread);
      return [{
        x: round(rect.x + offsetX - reach),
        y: round(rect.y + offsetY - reach),
        width: round(rect.width + reach * 2),
        height: round(rect.height + reach * 2),
      }];
    });
  }

  function couldPaintAfter(element, foreign) {
    var ownStyle = getComputedStyle(element);
    var foreignStyle = getComputedStyle(foreign);
    if (ownStyle.zIndex !== "auto" || foreignStyle.zIndex !== "auto") return true;
    var ownPositioned = ownStyle.position !== "static";
    var foreignPositioned = foreignStyle.position !== "static";
    if (ownPositioned !== foreignPositioned) return foreignPositioned;
    return Boolean(
      element.compareDocumentPosition(foreign) & Node.DOCUMENT_POSITION_FOLLOWING
    );
  }

  function couldPseudoPaintAfter(element, foreign, pseudo) {
    var pseudoStyle = getComputedStyle(foreign, pseudo);
    if (pseudoStyle.zIndex && pseudoStyle.zIndex !== "auto") return true;
    return couldPaintAfter(element, foreign);
  }

  function isShadowOccluded(element, observation, allElements) {
    var ownRects = observationRects(observation);
    return allElements.some(function (foreign) {
      if (
        foreign === element ||
        element.contains(foreign) ||
        foreign.contains(element) ||
        !couldPaintAfter(element, foreign)
      ) {
        return false;
      }
      return outerShadowRegions(foreign).some(function (shadowRect) {
        return ownRects.some(function (ownRect) {
          return Boolean(intersectionCenter(ownRect, shadowRect));
        });
      });
    });
  }

  function outlineRegions(element, style) {
    if (
      !style.outlineStyle ||
      style.outlineStyle === "none" ||
      !hasPositiveCssComponent(style.outlineWidth)
    ) {
      return [];
    }
    var width = Math.max(0, number(style.outlineWidth, 0));
    var offset = Math.abs(number(style.outlineOffset, 0));
    var reach = width + offset;
    return Array.from(element.getClientRects()).map(function (rect) {
      return {
        x: round(rect.left - reach),
        y: round(rect.top - reach),
        width: round(rect.width + reach * 2),
        height: round(rect.height + reach * 2),
      };
    });
  }

  function textShadowRegions(element, style) {
    if (!style.textShadow || style.textShadow === "none") return [];
    var rects = Array.from(element.getClientRects()).map(rectValue);
    return splitCssList(style.textShadow).flatMap(function (shadow) {
      var colorText = shadow.match(/rgba?\([^)]*\)/i);
      var color = colorText ? parseColor(colorText[0]) : null;
      if (color && color.alpha <= 0) return [];
      var lengths = (shadow.match(/-?(?:\d+(?:\.\d+)?|\.\d+)px/g) || []).map(function (value) {
        return number(value, 0);
      });
      if (lengths.length < 2) {
        return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
      }
      var offsetX = lengths[0];
      var offsetY = lengths[1];
      var blur = Math.max(0, lengths[2] || 0);
      var reach = blur * 2;
      return rects.map(function (rect) {
        return {
          x: round(rect.x + offsetX - reach),
          y: round(rect.y + offsetY - reach),
          width: round(rect.width + reach * 2),
          height: round(rect.height + reach * 2),
        };
      });
    });
  }

  function cssFilterFunctions(value) {
    var functions = [];
    var index = 0;
    while (index < value.length) {
      while (/\s/.test(value[index] || "")) index += 1;
      if (index >= value.length) break;
      var nameStart = index;
      while (/[a-z-]/i.test(value[index] || "")) index += 1;
      var name = value.slice(nameStart, index).toLowerCase();
      if (!name || value[index] !== "(") return null;
      index += 1;
      var valueStart = index;
      var depth = 1;
      while (index < value.length && depth > 0) {
        if (value[index] === "(") depth += 1;
        else if (value[index] === ")") depth -= 1;
        index += 1;
      }
      if (depth !== 0) return null;
      functions.push({ name: name, value: value.slice(valueStart, index - 1).trim() });
    }
    return functions;
  }

  function filterRegions(element, style) {
    if (!style.filter || style.filter === "none") return [];
    var functions = cssFilterFunctions(style.filter);
    var nonSpatial = new Set([
      "brightness", "contrast", "grayscale", "hue-rotate", "invert",
      "opacity", "saturate", "sepia"
    ]);
    if (
      !functions ||
      functions.some(function (item) {
        return item.name !== "blur" && item.name !== "drop-shadow" && !nonSpatial.has(item.name);
      })
    ) {
      return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
    }

    var reachX = 0;
    var reachY = 0;
    var invalid = false;
    functions.forEach(function (item) {
      if (item.name === "blur") {
        var blur = pixelLength(item.value);
        if (!Number.isFinite(blur)) invalid = true;
        else {
          reachX += Math.max(0, blur) * 2;
          reachY += Math.max(0, blur) * 2;
        }
      } else if (item.name === "drop-shadow") {
        var lengths = (item.value.match(/-?(?:\d+(?:\.\d+)?|\.\d+)px/g) || []).map(function (value) {
          return number(value, 0);
        });
        if (lengths.length < 2) invalid = true;
        else {
          reachX += Math.abs(lengths[0]) + Math.max(0, lengths[2] || 0) * 2;
          reachY += Math.abs(lengths[1]) + Math.max(0, lengths[2] || 0) * 2;
        }
      }
    });
    if (invalid) return [{ x: 0, y: 0, width: WIDTH, height: HEIGHT }];
    if (reachX <= 0 && reachY <= 0) return [];
    return Array.from(element.getClientRects()).map(function (rect) {
      return {
        x: round(rect.left - reachX),
        y: round(rect.top - reachY),
        width: round(rect.width + reachX * 2),
        height: round(rect.height + reachY * 2),
      };
    });
  }

  function externalPaintRegions(element) {
    var style = getComputedStyle(element);
    return outlineRegions(element, style)
      .concat(textShadowRegions(element, style))
      .concat(filterRegions(element, style));
  }

  function isExternalPaintOccluded(element, observation, allElements) {
    var ownRects = observationRects(observation);
    return allElements.some(function (foreign) {
      if (foreign === element || element.contains(foreign)) return false;
      if (
        foreign.contains(element) &&
        isDecomposableSvgDropShadow(
          element,
          foreign,
          getComputedStyle(foreign).filter
        )
      ) return false;
      if (!foreign.contains(element) && !couldPaintAfter(element, foreign)) {
        return false;
      }
      return externalPaintRegions(foreign).some(function (paintRect) {
        return ownRects.some(function (ownRect) {
          return Boolean(intersectionCenter(ownRect, paintRect));
        });
      });
    });
  }

  function temporarilyEnablePointerHitTesting(elements) {
    var changes = [];
    elements.forEach(function (element) {
      if (getComputedStyle(element).pointerEvents !== "none") return;
      changes.push({
        element: element,
        value: element.style.getPropertyValue("pointer-events"),
        priority: element.style.getPropertyPriority("pointer-events"),
      });
      element.style.setProperty("pointer-events", "auto", "important");
    });
    return function restorePointerHitTesting() {
      changes.reverse().forEach(function (change) {
        if (change.value) {
          change.element.style.setProperty(
            "pointer-events",
            change.value,
            change.priority
          );
        } else {
          change.element.style.removeProperty("pointer-events");
        }
      });
    };
  }

  function waitForTwoPaints() {
    return new Promise(function (resolve) {
      var settled = false;
      function finish() {
        if (settled) return;
        settled = true;
        resolve();
      }
      // --dump-dom can throttle animation frames. The virtual-time timer keeps
      // extraction progressing while screenshot mode still gets two paints.
      setTimeout(finish, 100);
      requestAnimationFrame(function () { requestAnimationFrame(finish); });
    });
  }

  async function ensureFixedViewport() {
    for (var attempt = 0; attempt < 3; attempt += 1) {
      if (window.innerWidth === WIDTH && window.innerHeight === HEIGHT) return;
      window.resizeBy(WIDTH - window.innerWidth, HEIGHT - window.innerHeight);
      await new Promise(function (resolve) { setTimeout(resolve, 50); });
    }
  }

  function waitForStablePage() {
    var fontReady = document.fonts && document.fonts.ready
      ? document.fonts.ready.catch(function () {})
      : Promise.resolve();
    var imageReady = Promise.allSettled(
      Array.from(document.images).map(function (image) {
        if (image.complete) return Promise.resolve();
        if (typeof image.decode === "function") return image.decode();
        return new Promise(function (resolve) {
          image.addEventListener("load", resolve, { once: true });
          image.addEventListener("error", resolve, { once: true });
        });
      })
    );
    var ready = Promise.allSettled([fontReady, imageReady]);
    var timeout = new Promise(function (resolve) { setTimeout(resolve, 4000); });
    return Promise.race([ready, timeout]).then(waitForTwoPaints);
  }

  function enforceMinimumEditableFontSize() {
    var minimumPx = 12; // 12 CSS px = 9 PowerPoint points.
    var pseudoRules = [];
    [document.body].concat(Array.from(document.body.querySelectorAll("*"))).forEach(
      function (element, index) {
        if (["SCRIPT", "STYLE", "TEXTAREA", "NOSCRIPT", "TEMPLATE"].includes(element.tagName)) {
          return;
        }
        var hasOwnText = Array.from(element.childNodes).some(function (node) {
          return node.nodeType === Node.TEXT_NODE && Boolean((node.textContent || "").trim());
        });
        var style = getComputedStyle(element);
        if (hasOwnText && number(style.fontSize, minimumPx) < minimumPx) {
          element.style.setProperty("font-size", minimumPx + "px", "important");
        }
        ["::before", "::after"].forEach(function (pseudo) {
          var pseudoStyle = getComputedStyle(element, pseudo);
          var content = pseudoStyle.content;
          if (
            content &&
            content !== "none" &&
            content !== "normal" &&
            number(pseudoStyle.fontSize, minimumPx) < minimumPx
          ) {
            var attribute = "data-presenton-min-font-" + index +
              (pseudo === "::before" ? "-before" : "-after");
            element.setAttribute(attribute, "true");
            pseudoRules.push(
              "[" + attribute + "]" + pseudo + "{font-size:" + minimumPx + "px!important}"
            );
          }
        });
      }
    );
    if (pseudoRules.length) {
      var sheet = document.createElement("style");
      sheet.setAttribute("data-presenton-minimum-font-size", "true");
      sheet.textContent = pseudoRules.join("\n");
      document.head.appendChild(sheet);
    }
  }

  function observationContentKey(observation) {
    if (observation.candidateKind === "text" && observation.text) {
      return "text:" + JSON.stringify({
        role: observation.text.role,
        plainText: observation.text.plainText,
        paragraphs: observation.text.paragraphs,
        style: observation.text.style,
        runs: observation.text.runs.map(function (run) {
          return {
            text: run.text,
            boundsPx: run.boundsPx,
            fragmentRectsPx: run.fragmentRectsPx,
            style: run.style,
          };
        }),
        containerShape: observation.text.containerShape,
      });
    }
    if (observation.candidateKind === "image" && observation.image) {
      return "image:" + JSON.stringify({
        src: observation.image.src,
        alt: observation.image.alt,
        naturalWidth: observation.image.naturalWidth,
        naturalHeight: observation.image.naturalHeight,
        objectFit: observation.image.objectFit,
        objectPosition: observation.image.objectPosition,
        crop: observation.image.crop,
      });
    }
    if (observation.candidateKind === "shape" && observation.shape) {
      return "shape:" + JSON.stringify(observation.shape);
    }
    return "";
  }

  function nearlyEqual(left, right, tolerance) {
    return Number.isFinite(left) && Number.isFinite(right) && Math.abs(left - right) <= tolerance;
  }

  function identityMatches(record, expected) {
    if (!expected || typeof expected !== "object") return false;
    var observation = record.observation;
    var expectedBounds = expected.boundsPx || {};
    var actualBounds = observation.boundsPx;
    return (
      (["text", "shape"].includes(expected.candidateKind) || observation.fallbackReasons.length === 0) &&
      observation.id === expected.id &&
      observation.domPath === expected.domPath &&
      observation.tagName === expected.tagName &&
      observation.sourceIndex === expected.sourceIndex &&
      observation.candidateKind === expected.candidateKind &&
      observationContentKey(observation) === expected.contentKey &&
      nearlyEqual(actualBounds.x, expectedBounds.x, 0.25) &&
      nearlyEqual(actualBounds.y, expectedBounds.y, 0.25) &&
      nearlyEqual(actualBounds.width, expectedBounds.width, 0.25) &&
      nearlyEqual(actualBounds.height, expectedBounds.height, 0.25) &&
      nearlyEqual(observation.rotationDeg, expected.rotationDeg, 0.01) &&
      nearlyEqual(observation.opacity, expected.opacity, 0.001)
    );
  }

  function applySuppression(records) {
    var byId = new Map(records.map(function (record) {
      return [record.observation.id, record];
    }));
    var applied = [];
    var rejected = [];
    var transparentPixel =
      "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=";
    ensurePseudoSuppressionStyle();
    promotedElements.forEach(function (expected) {
      var id = expected && typeof expected.id === "string" ? expected.id : "";
      var record = byId.get(id);
      if (!record || !identityMatches(record, expected)) {
        rejected.push(id);
        return;
      }
      var element = record.element;
      var kind = record.observation.candidateKind;
      if (record.pseudo) {
        element.setAttribute(pseudoSuppressionAttribute(record.pseudo), "true");
      } else if (kind === "text") {
        if (expected.suppressWholeElement) {
          element.style.setProperty("visibility", "hidden", "important");
        } else {
          [element].concat(Array.from(element.querySelectorAll("*"))).forEach(function (target) {
            target.style.setProperty("-webkit-text-fill-color", "transparent", "important");
            target.style.setProperty("text-decoration-color", "transparent", "important");
            target.style.setProperty("-webkit-text-stroke-color", "transparent", "important");
            target.style.setProperty("text-shadow", "none", "important");
            // SVG text uses fill/stroke presentation properties instead of
            // the HTML text-fill properties above. Clear those paints too so
            // editable OOXML text does not sit on a raster duplicate.
            if (target.namespaceURI === "http://www.w3.org/2000/svg") {
              target.style.setProperty("fill", "transparent", "important");
              target.style.setProperty("stroke", "transparent", "important");
            }
          });
        }
        if (expected.suppressContainerPaint) {
          element.style.setProperty("background", "none", "important");
          element.style.setProperty("border-color", "transparent", "important");
          element.style.setProperty("outline", "none", "important");
          element.style.setProperty("box-shadow", "none", "important");
        }
      } else if (kind === "image") {
        if (
          element.namespaceURI === "http://www.w3.org/2000/svg" ||
          element.tagName.toUpperCase() !== "IMG"
        ) {
          element.style.setProperty("visibility", "hidden", "important");
        } else {
          var width = element.offsetWidth;
          var height = element.offsetHeight;
          if (width > 0) element.style.setProperty("width", width + "px", "important");
          if (height > 0) element.style.setProperty("height", height + "px", "important");
          element.removeAttribute("srcset");
          element.removeAttribute("sizes");
          var picture = element.closest("picture");
          if (picture) {
            Array.from(picture.querySelectorAll("source")).forEach(function (source) {
              source.removeAttribute("srcset");
            });
          }
          element.src = transparentPixel;
          element.style.setProperty("opacity", "0", "important");
        }
      } else if (kind === "shape") {
        if (element.namespaceURI === "http://www.w3.org/2000/svg") {
          element.style.setProperty("visibility", "hidden", "important");
        } else {
          // Remove only the editable CSS geometry. Descendant icons and
          // unsupported decoration remain as transparent raster overlay paint,
          // while the box shadow stays above the native card underneath it.
          element.style.setProperty("background", "none", "important");
          element.style.setProperty("border-color", "transparent", "important");
          element.style.setProperty("outline", "none", "important");
          element.style.setProperty("box-shadow", "none", "important");
          if (record.observation.shape.endArrow) {
            element.setAttribute("data-presenton-authored-hybrid-suppress-pseudo", "true");
          }
        }
      }
      if (!record.pseudo && element === document.body) {
        // Chromium propagates the body background to the canvas. Clearing both
        // roots is required for --default-background-color=00000000 to produce
        // a genuinely transparent residual layer.
        document.documentElement.style.setProperty("background", "transparent", "important");
        document.body.style.setProperty("background", "transparent", "important");
      }
      element.setAttribute("data-presenton-authored-hybrid-suppressed", "true");
      applied.push(id);
    });
    return { applied: applied, rejected: rejected };
  }

  function writeResult(payload) {
    var json = JSON.stringify(payload);
    var bytes = new TextEncoder().encode(json);
    var binary = "";
    for (var index = 0; index < bytes.length; index += 32768) {
      binary += String.fromCharCode.apply(null, bytes.subarray(index, index + 32768));
    }
    var marker = document.createElement("script");
    marker.type = "application/json";
    marker.id = MARKER_ID;
    marker.textContent = btoa(binary);
    document.body.appendChild(marker);
  }

  try {
    await ensureFixedViewport();
    await waitForStablePage();
    enforceMinimumEditableFontSize();
    await waitForTwoPaints();

    // A direct text node next to a block/flex/grid child is rendered as an
    // anonymous box. querySelectorAll cannot observe that box, so labels next
    // to a nested number badge used to remain absent from the editable layer.
    // Generated flex/grid items (::before/::after) have the same problem: the
    // host bounds start before the generated item, which makes editable text
    // overlap eyebrow bars and chip bullets. Materialize those direct text
    // nodes as neutral spans so Chromium gives us their true laid-out bounds.
    Array.from(document.body.querySelectorAll("*")).forEach(function (element) {
      if (["SCRIPT", "STYLE", "TEXTAREA", "NOSCRIPT", "TEMPLATE", "SVG"].includes(element.tagName)) {
        return;
      }
      var display = getComputedStyle(element).display;
      var generatedFlexItem = ["flex", "inline-flex", "grid", "inline-grid"].includes(display) &&
        (pseudoPainted(element, "::before") || pseudoPainted(element, "::after"));
      if (
        !nestedBlockContent(element) &&
        !hasDecoratedDescendant(element) &&
        !generatedFlexItem
      ) return;
      Array.from(element.childNodes).forEach(function (node) {
        if (node.nodeType !== Node.TEXT_NODE || !(node.textContent || "").trim()) return;
        var span = document.createElement("span");
        span.setAttribute("data-presenton-authored-direct-text", "true");
        node.parentNode.insertBefore(span, node);
        span.appendChild(node);
      });
    });

    // Body owns the full-slide background in many authored templates. Treat it
    // as an editable rectangle so the residual PNG can stay transparent above
    // native cards and connector geometry.
    var allElements = [document.body].concat(Array.from(document.body.querySelectorAll("*")));
    var complexSvgImagePayloads = new Map();
    var complexHtmlImagePayloads = new Map();
    for (var svgIndex = 0; svgIndex < allElements.length; svgIndex += 1) {
      var svgElement = allElements[svgIndex];
      if (
        svgElement.tagName && svgElement.tagName.toUpperCase() === "SVG" &&
        complexKind(svgElement) === "complex-diagram"
      ) {
        var svgReasons = [];
        var svgBounds = unrotatedRect(svgElement, analyzeTransform(svgElement).rotationDeg);
        complexSvgImagePayloads.set(
          svgElement,
          await svgImagePayload(svgElement, svgReasons, svgBounds)
        );
      } else if (
        svgElement.tagName && svgElement.namespaceURI !== "http://www.w3.org/2000/svg" &&
        complexKind(svgElement) === "complex-diagram" &&
        /(^|\s)visual(\s|$)/.test(
          typeof svgElement.className === "string" ? svgElement.className.toLowerCase() : ""
        )
      ) {
        var htmlReasons = [];
        var htmlBounds = unrotatedRect(svgElement, analyzeTransform(svgElement).rotationDeg);
        complexHtmlImagePayloads.set(
          svgElement,
          await htmlDiagramImagePayload(svgElement, htmlReasons, htmlBounds)
        );
      }
    }
    var coveredRoots = [];
    var records = [];
    var warnings = [];

    allElements.forEach(function (element, sourceIndex) {
      if (["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT", "TEMPLATE"].includes(element.tagName)) {
        return;
      }
      if (coveredRoots.some(function (root) { return root.contains(element); })) return;
      if (!visibleElement(element)) return;

      var complexReason = complexKind(element);
      var candidateKind = null;
      if (
        complexReason === "complex-diagram" &&
        (complexSvgImagePayloads.has(element) || complexHtmlImagePayloads.has(element))
      ) {
        candidateKind = "image";
      }
      else if (complexReason) candidateKind = "complex";
      else if (element.tagName === "IMG") candidateKind = "image";
      else if (looksLikeShape(element) && (nestedBlockContent(element) || !textRoot(element))) candidateKind = "shape";
      else if (textRoot(element)) candidateKind = "text";
      else if (looksLikeShape(element)) candidateKind = "shape";
      if (!candidateKind) return;

      var id = "h1-" + String(records.length + 1).padStart(4, "0");
      element.setAttribute(ELEMENT_ATTRIBUTE, id);
      var transform = analyzeTransform(element);
      var reasons = safetyReasons(element, candidateKind, transform);
      if (complexHtmlImagePayloads.has(element)) {
        // The whole semantic visual is captured exactly as a transparent PNG;
        // its internal decoration, clipping, pseudo paint, and local z-index
        // are therefore part of the payload rather than promotion hazards.
        reasons = [];
      }
      var bounds = svgShapeElement(element)
        ? svgShapeBounds(element, reasons)
        : unrotatedRect(element, transform.rotationDeg);
      if (complexReason && candidateKind === "complex") reasons.push(complexReason);
      if (bounds.width <= 0 || bounds.height <= 0 || !Object.values(bounds).every(Number.isFinite)) {
        reasons.push("invalid-bounds");
      }
      if (
        bounds.x < 0 || bounds.y < 0 ||
        bounds.x + bounds.width > WIDTH || bounds.y + bounds.height > HEIGHT
      ) {
        reasons.push("outside-slide");
      }

      var zIndexText = getComputedStyle(element).zIndex;
      var zIndex = /^-?\d+$/.test(zIndexText) ? Number.parseInt(zIndexText, 10) : null;
      var observation = {
        id: id,
        domPath: domPath(element),
        tagName: element.tagName.toLowerCase(),
        sourceIndex: sourceIndex,
        cssZIndex: zIndex,
        boundsPx: bounds,
        rotationDeg: transform.rotationDeg,
        opacity: effectiveOpacity(element),
        candidateKind: candidateKind,
        fallbackReasons: reasons,
      };

      try {
        if (candidateKind === "text") {
          if (nestedBlockContent(element)) reasons.push("complex-content");
          var tagName = element.tagName.toUpperCase();
          var browserPlainText = element.innerText ||
            (["TEXT", "TEXTPATH"].includes(tagName) ? element.textContent : "") || "";
          var extractedRuns = textRuns(element);
          var renderedPlainText = extractedRuns.runs.map(function (run) { return run.text; }).join("");
          if (extractedRuns.failed) reasons.push("run-extraction-error");
          if (
            normalizeWhitespace(extractedRuns.identityText) !== normalizeWhitespace(browserPlainText)
          ) {
            reasons.push("ambiguous-whitespace");
          }
          var plainText = reasons.includes("ambiguous-whitespace")
            ? browserPlainText
            : renderedPlainText;
          var elementTextStyle = textStyle(element, plainText);
          // Preserve the authored CSS wrapping policy. Browser visual lines are
          // still emitted as explicit breaks for fidelity, while PowerPoint's
          // own wrapping remains enabled as a safety net when installed font
          // metrics differ from Chromium. Genuine CSS white-space: nowrap
          // content continues to export with wrapMode="no-wrap".
          observation.text = {
            role: inferTextRole(element, plainText.trim()),
            plainText: plainText,
            paragraphs: plainText.split(/\n+/).map(function (value) { return value.trim(); }).filter(Boolean),
            style: elementTextStyle,
            runs: extractedRuns.runs,
          };
          var containerShape = textContainerShape(element, bounds);
          if (containerShape) observation.text.containerShape = containerShape;
          observation.boundsPx = textContentRect(element, bounds, transform.rotationDeg);
        } else if (candidateKind === "image") {
          observation.image = complexSvgImagePayloads.has(element)
            ? complexSvgImagePayloads.get(element)
            : complexHtmlImagePayloads.has(element)
              ? complexHtmlImagePayloads.get(element)
              : imagePayload(element, reasons, bounds);
          if (!observation.image) reasons.push("extraction-error");
        } else if (candidateKind === "shape") {
          bounds = contentSafeShapeBounds(element, bounds);
          observation.boundsPx = bounds;
          observation.shape = shapePayload(
            element,
            reasons,
            bounds,
            element.children.length > 0 || Boolean((element.textContent || "").trim())
          );
        }
      } catch (_elementError) {
        reasons.push("extraction-error");
      }

      observation.fallbackReasons = unique(reasons);
      records.push({ element: element, observation: observation });
      if (
        ((candidateKind === "complex" || candidateKind === "image") && (
          element.tagName.toUpperCase() === "CANVAS" ||
          (element.tagName.toUpperCase() === "SVG" && complexReason !== "svg-text")
        )) ||
        (candidateKind === "text" && !nestedBlockContent(element))
      ) coveredRoots.push(element);
    });

    // CSS generated content is not represented by querySelectorAll and
    // therefore used to remain fused into the residual backplate. Materialize
    // empty painted pseudo-elements only long enough to measure and serialize
    // them as independent editable PowerPoint shapes. Their authored paint is
    // suppressed separately when the shape is promoted.
    var elementSourceIndexes = new Map();
    allElements.forEach(function (element, index) {
      elementSourceIndexes.set(element, index);
    });
    allElements.forEach(function (element) {
      ["::before", "::after"].forEach(function (pseudo) {
        var hostSourceIndex = elementSourceIndexes.get(element) || 0;
        var pseudoSourceIndex = hostSourceIndex;
        if (pseudo === "::after") {
          for (var index = hostSourceIndex + 1; index < allElements.length; index += 1) {
            if (!element.contains(allElements[index])) break;
            pseudoSourceIndex = index;
          }
        }
        var id = "h1-" + String(records.length + 1).padStart(4, "0");
        var record = materializedPseudoShape(element, pseudo, pseudoSourceIndex, id);
        if (record) records.push(record);
      });
    });

    // elementsFromPoint intentionally omits pointer-events:none nodes even
    // though they still paint. Enable hit testing only while sampling the
    // paint stack, then restore the authored inline declarations exactly.
    var restorePointerHitTesting = temporarilyEnablePointerHitTesting(
      [document.documentElement, document.body].concat(allElements)
    );
    try {
      records.forEach(function (record) {
        var observation = record.observation;
        if (
          !record.pseudo &&
          observation.candidateKind !== "complex" &&
          !complexHtmlImagePayloads.has(record.element) &&
          (isOccluded(record.element, observation) ||
            isRectOccluded(record.element, observation, allElements) ||
            isPseudoOccluded(record.element, observation, allElements) ||
            isShadowOccluded(record.element, observation, allElements) ||
            isExternalPaintOccluded(record.element, observation, allElements))
        ) {
          observation.fallbackReasons = unique(
            observation.fallbackReasons.concat(["occluded"])
          );
        }
      });
    } finally {
      restorePointerHitTesting();
    }

    if (window.innerWidth !== WIDTH || window.innerHeight !== HEIGHT) {
      warnings.push(
        "Expected a 1280x720 viewport but observed " + window.innerWidth + "x" + window.innerHeight + "."
      );
    }
    if (window.devicePixelRatio !== 1) {
      warnings.push("Expected devicePixelRatio 1 but observed " + window.devicePixelRatio + ".");
    }

    var suppression = applySuppression(records);
    if (suppression.applied.length) {
      // Suppressed images are already synchronously removed from paint by an
      // inline !important opacity. decode() is unnecessary here and can remain
      // pending forever in headless Chrome after swapping an image source.
      await waitForTwoPaints();
    }

    writeResult({
      ok: true,
      value: {
        viewport: {
          widthPx: window.innerWidth,
          heightPx: window.innerHeight,
          devicePixelRatio: window.devicePixelRatio,
        },
        elements: records.map(function (record) { return record.observation; }),
        warnings: warnings,
        appliedPromotedElementIds: suppression.applied,
        rejectedPromotedElementIds: suppression.rejected,
      },
    });
  } catch (error) {
    writeResult({
      ok: false,
      error: error && error.stack ? String(error.stack) : String(error),
    });
  }
})();
`;
