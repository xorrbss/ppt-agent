export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_08 = String.raw`  }

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
`;
