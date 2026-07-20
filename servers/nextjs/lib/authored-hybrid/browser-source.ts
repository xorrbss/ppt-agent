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
    var vertical = "top";
    var alignItems = style.alignItems;
    var justifyContent = style.justifyContent;
    if (alignItems === "center" || justifyContent === "center") vertical = "middle";
    if (alignItems === "flex-end" || justifyContent === "flex-end") vertical = "bottom";
    return {
      fontFamily: families[0] || "sans-serif",
      fontFamilies: families.length ? families : ["sans-serif"],
      cjkFallbackFamilies: cjkFallbacks(text, families),
      fontSizePt: round(fontSizePx * 0.75),
      fontWeight: fontWeight,
      bold: fontWeight >= 600,
      italic: style.fontStyle === "italic" || style.fontStyle === "oblique",
      underline: decoration.includes("underline"),
      strike: decoration.includes("line-through"),
      color: parseColor(style.color) || { hex: "000000", alpha: 1 },
      letterSpacingPt:
        style.letterSpacing === "normal" ? 0 : round(number(style.letterSpacing, 0) * 0.75),
      lineHeight: {
        points: round(lineHeightPx * 0.75),
        multiple: round(lineHeightPx / Math.max(fontSizePx, 0.01)),
        source: lineHeightSource,
      },
      horizontalAlignment: alignment,
      verticalAlignment: vertical,
      direction: style.direction === "rtl" ? "rtl" : "ltr",
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
      return { safe: false, rotationDeg: 0 };
    }
    if (!transform || transform === "none") {
      return { safe: true, rotationDeg: 0 };
    }
    var match = transform.match(/^matrix\(([^)]+)\)$/);
    if (!match) return { safe: false, rotationDeg: 0 };
    var values = match[1].split(",").map(function (part) {
      return number(part, Number.NaN);
    });
    if (values.length !== 6 || values.some(function (value) { return !Number.isFinite(value); })) {
      return { safe: false, rotationDeg: 0 };
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
    var safe =
      Math.abs(scaleX - 1) < 0.001 &&
      Math.abs(scaleY - 1) < 0.001 &&
      Math.abs(dot) < 0.001 &&
      determinant > 0.999 &&
      Math.abs(e) < 0.001 &&
      Math.abs(f) < 0.001 &&
      centeredOrigin;
    return {
      safe: safe,
      rotationDeg: safe ? round((Math.atan2(b, a) * 180) / Math.PI) : 0,
    };
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
      if (style.filter && style.filter !== "none") reasons.push("filter");
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
        reasons.push("transformed-ancestor");
        reasons.push("unknown-z-order");
      }
      // Replaced images commonly expose a UA-level overflow:clip even when
      // no authored clipping is present. Their crop safety is evaluated from
      // object-fit/object-position instead, while ancestor clipping remains a
      // hard raster fallback.
      if (
        !documentRoot && (!own || kind !== "image") &&
        (style.overflowX !== "visible" || style.overflowY !== "visible")
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
          (targetStyle.overflowX !== "visible" || targetStyle.overflowY !== "visible")
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
    return rect.width >= 0.5 && rect.height >= 0.5;
  }

  var INLINE_TAGS = new Set([
    "A", "ABBR", "B", "BDI", "BDO", "BR", "CITE", "CODE", "DEL", "EM",
    "I", "INS", "KBD", "MARK", "Q", "S", "SAMP", "SMALL", "SPAN", "STRONG",
    "SUB", "SUP", "TIME", "U", "VAR", "WBR"
  ]);
  var TEXT_ROOT_TAGS = new Set([
    "H1", "H2", "H3", "H4", "H5", "H6", "P", "LI", "FIGCAPTION",
    "BLOCKQUOTE", "LABEL", "SMALL"
  ]);

  function directText(element) {
    return Array.from(element.childNodes).some(function (node) {
      return node.nodeType === Node.TEXT_NODE && Boolean(node.textContent.trim());
    });
  }

  function textRoot(element) {
    if (!element.innerText || !element.innerText.trim()) return false;
    return TEXT_ROOT_TAGS.has(element.tagName) || directText(element);
  }

  function nestedBlockContent(element) {
    return Array.from(element.querySelectorAll("*")).some(function (child) {
      return !INLINE_TAGS.has(child.tagName);
    });
  }

  function complexKind(element) {
    // SVG DOM tagName values are lowercase in Chromium, unlike HTML tagName.
    var tag = element.tagName.toUpperCase();
    if (tag === "TABLE") return "complex-table";
    if (tag === "SVG") {
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

    function visit(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        var text = node.textContent || "";
        if (!text) return;
        try {
          var range = document.createRange();
          range.selectNodeContents(node);
          var fragments = Array.from(range.getClientRects())
            .filter(function (rect) { return rect.width > 0 || rect.height > 0; })
            .map(rectValue);
          var bounds = rectValue(range.getBoundingClientRect());
          runs.push({
            text: text,
            boundsPx: bounds,
            fragmentRectsPx: fragments,
            style: textStyle(node.parentElement || element, text),
          });
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
        return;
      }
      Array.from(node.childNodes).forEach(visit);
    }

    Array.from(element.childNodes).forEach(visit);
    return { runs: runs, failed: failed };
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

  function shapePayload(element, reasons, bounds) {
    var style = getComputedStyle(element);
    if (style.backgroundImage && style.backgroundImage !== "none") {
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
    var borderUniform = widths.every(function (width) {
      return Math.abs(width - widths[0]) < 0.01;
    });
    var borderStyles = [
      style.borderTopStyle,
      style.borderRightStyle,
      style.borderBottomStyle,
      style.borderLeftStyle,
    ];
    var borderStyleUniform = borderStyles.every(function (value) {
      return value === borderStyles[0];
    });
    if (
      !borderUniform ||
      !borderStyleUniform ||
      !["none", "solid"].includes(borderStyles[0])
    ) {
      reasons.push("unsupported-shape");
    }
    var borderColorValues = [
      style.borderTopColor,
      style.borderRightColor,
      style.borderBottomColor,
      style.borderLeftColor,
    ];
    var borderColors = borderColorValues.map(parseColor);
    if (widths[0] > 0 && borderColors.some(function (color) { return !color; })) {
      reasons.push("unsupported-color");
    }
    var stroke = widths[0] > 0 ? borderColors[0] : null;
    if (
      widths[0] > 0 &&
      borderColors.some(function (color) {
        return !color || !stroke || color.hex !== stroke.hex || color.alpha !== stroke.alpha;
      })
    ) {
      reasons.push("unsupported-shape");
    }
    if (stroke && stroke.alpha === 0) stroke = null;
    if (!fill && !stroke) reasons.push("unsupported-shape");
    var radiusValues = [
      style.borderTopLeftRadius,
      style.borderTopRightRadius,
      style.borderBottomRightRadius,
      style.borderBottomLeftRadius,
    ];
    var radiusPattern = /^([\d.]+)px$/;
    var radii = radiusValues.map(function (value) {
      var match = String(value).match(radiusPattern);
      return match ? number(match[1], Number.NaN) : Number.NaN;
    });
    if (
      (radii.some(function (radius) { return !Number.isFinite(radius); }) ||
        radii.some(function (radius) { return Math.abs(radius - radii[0]) >= 0.01; }))
    ) {
      reasons.push("unsupported-shape");
    }
    var radiusPx = Number.isFinite(radii[0])
      ? Math.min(radii[0], bounds.width / 2, bounds.height / 2)
      : 0;
    var shape = "rectangle";
    if (Math.min(bounds.width, bounds.height) <= 3 && Math.max(bounds.width, bounds.height) >= 8) {
      shape = "line";
    } else if (radiusPx > 0) {
      shape = "round-rectangle";
    }
    return {
      shape: shape,
      fill: fill,
      stroke: stroke,
      strokeWidthPt: round(widths[0] * 0.75),
      radiusPt: round(radiusPx * 0.75),
    };
  }

  function looksLikeShape(element) {
    if (element.children.length > 0 || (element.textContent || "").trim()) return false;
    var hint = (element.getAttribute("data-ppt-role") || "").toLowerCase();
    if (hint === "shape") return true;
    if (!["DIV", "SPAN", "HR"].includes(element.tagName)) return false;
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
      return item === element || element.contains(item);
    });
  }

  function isOccluded(element, observation) {
    return samplePoints(observation).some(function (point) {
      var stack = document.elementsFromPoint(point[0], point[1]);
      var ownIndex = stack.findIndex(function (item) {
        return item === element || element.contains(item);
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
      observation.fallbackReasons.length === 0 &&
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
    promotedElements.forEach(function (expected) {
      var id = expected && typeof expected.id === "string" ? expected.id : "";
      var record = byId.get(id);
      if (!record || !identityMatches(record, expected)) {
        rejected.push(id);
        return;
      }
      var element = record.element;
      var kind = record.observation.candidateKind;
      if (kind === "text") {
        [element].concat(Array.from(element.querySelectorAll("*"))).forEach(function (target) {
          target.style.setProperty("-webkit-text-fill-color", "transparent", "important");
          target.style.setProperty("text-decoration-color", "transparent", "important");
          target.style.setProperty("-webkit-text-stroke-color", "transparent", "important");
          target.style.setProperty("text-shadow", "none", "important");
        });
      } else if (kind === "image") {
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
      } else if (kind === "shape") {
        element.style.setProperty("visibility", "hidden", "important");
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

    var allElements = Array.from(document.body.querySelectorAll("*"));
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
      if (complexReason) candidateKind = "complex";
      else if (element.tagName === "IMG") candidateKind = "image";
      else if (textRoot(element)) candidateKind = "text";
      else if (looksLikeShape(element)) candidateKind = "shape";
      if (!candidateKind) return;

      var id = "h1-" + String(records.length + 1).padStart(4, "0");
      element.setAttribute(ELEMENT_ATTRIBUTE, id);
      var transform = analyzeTransform(element);
      var bounds = unrotatedRect(element, transform.rotationDeg);
      var reasons = safetyReasons(element, candidateKind, transform);
      if (complexReason) reasons.push(complexReason);
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
          var plainText = element.innerText || "";
          var extractedRuns = textRuns(element);
          if (extractedRuns.failed) reasons.push("run-extraction-error");
          if (
            normalizeWhitespace(extractedRuns.runs.map(function (run) { return run.text; }).join("")) !==
            normalizeWhitespace(plainText)
          ) {
            reasons.push("ambiguous-whitespace");
          }
          observation.text = {
            role: inferTextRole(element, plainText.trim()),
            plainText: plainText,
            paragraphs: plainText.split(/\n+/).map(function (value) { return value.trim(); }).filter(Boolean),
            style: textStyle(element, plainText),
            runs: extractedRuns.runs,
          };
        } else if (candidateKind === "image") {
          observation.image = imagePayload(element, reasons, bounds);
        } else if (candidateKind === "shape") {
          observation.shape = shapePayload(element, reasons, bounds);
        }
      } catch (_elementError) {
        reasons.push("extraction-error");
      }

      observation.fallbackReasons = unique(reasons);
      records.push({ element: element, observation: observation });
      if (candidateKind === "complex" || candidateKind === "text") coveredRoots.push(element);
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
          observation.candidateKind !== "complex" &&
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
