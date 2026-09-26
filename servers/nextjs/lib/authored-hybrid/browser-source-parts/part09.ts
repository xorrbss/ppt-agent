export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_09 = String.raw`    var borderStyles = [
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
    var isThin = Math.min(bounds.width, bounds.height) <= 3 &&
      Math.max(bounds.width, bounds.height) >= 8;
    var isMaterializedPseudo = element.hasAttribute(
      "data-presenton-authored-pseudo-clone"
    );
    var hasDetachedArrowTip = ["::before", "::after"].some(function (pseudo) {
      try {
        return Boolean(pseudoBorderTrianglePayload(getComputedStyle(element, pseudo)));
      } catch (_pseudoStyleError) {
        return false;
      }
    });
    // A thin painted CSS box is still a box: serializing every divider as a
    // PowerPoint line changes its end caps, centre line, and effective length.
    // Keep rectangles native unless the author supplied connector semantics
    // through a generated rail or a detached CSS border-triangle arrowhead.
    if (isThin && (isMaterializedPseudo || hasDetachedArrowTip)) {
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
    var outline = undefined;
    var outlineWidthPx = number(style.outlineWidth, 0);
    var outlineStyle = String(style.outlineStyle || "none").toLowerCase();
    if (outlineStyle !== "none" && outlineWidthPx > 0.01) {
      var outlineColor = parseColor(style.outlineColor);
      if (!["solid", "dashed", "dotted"].includes(outlineStyle)) {
        reasons.push("unsupported-shape");
      } else if (!outlineColor) {
        reasons.push("unsupported-color");
      } else if (outlineColor.alpha > 0) {
        outline = {
          color: outlineColor,
          widthPt: round(outlineWidthPx * 0.75),
          offsetPx: round(number(style.outlineOffset, 0)),
          dash: outlineStyle === "dashed"
            ? "dash"
            : outlineStyle === "dotted"
              ? "dot"
              : undefined,
        };
      }
    }
    if (shape === "line" && !stroke && fill) {
      stroke = fill;
      fill = null;
      strokeWidthPt = round(Math.max(0.75, Math.min(bounds.width, bounds.height) * 0.75));
    }
    var shadowPlan = boxShadowPlan(style.boxShadow);
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
      shadowLayers: shadowPlan.shadowLayers,
      outerShadow: shadowPlan.outerShadow,
      backplateShadowLayers: shadowPlan.backplateShadowLayers || undefined,
      outline: outline,
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
    if (boxShadowUnmapped(style.boxShadow) || nestedBlockContent(element)) {
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
`;
