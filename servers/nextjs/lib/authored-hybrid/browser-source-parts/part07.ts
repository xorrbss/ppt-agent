export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_07 = String.raw`        objectFit: "fill",
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
      var scale = Math.min(CAPTURE_SCALE, Math.max(1, window.devicePixelRatio || 1));
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
`;
