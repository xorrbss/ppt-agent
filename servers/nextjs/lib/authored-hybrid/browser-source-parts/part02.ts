export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_02 = String.raw`    };
  }

  function rigidAncestorRotation(element) {
    var rotationDeg = 0;
    var current = element.parentElement;
    while (current) {
      var style = getComputedStyle(current);
      if (
        (style.transform && style.transform !== "none") ||
        hasIndividualTransform(style)
      ) {
        var transform = analyzeTransform(current);
        // Descendants of scale, skew, perspective, or off-centre rotation do
        // not map to one native PowerPoint rotation. Keep those on raster.
        if (!transform.safe) return null;
        rotationDeg += transform.rotationDeg;
      }
      if (current === document.documentElement) break;
      current = current.parentElement;
    }
    return round(rotationDeg);
  }

  function effectiveRigidRotation(element, transform) {
    if (!transform.safe) return transform.rotationDeg;
    var ancestorRotation = rigidAncestorRotation(element);
    return ancestorRotation === null
      ? transform.rotationDeg
      : round(transform.rotationDeg + ancestorRotation);
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
      var rotationDeg = effectiveRigidRotation(clone, transform);
      var bounds = unrotatedRect(clone, rotationDeg);
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
        rotationDeg: rotationDeg,
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
        var contentBounds = textContentRect(clone, bounds, rotationDeg);
        observation.text.layout = textLayout(
          clone,
          bounds,
          contentBounds,
          extractedRuns.runs
        );
        observation.boundsPx = contentBounds;
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
`;
