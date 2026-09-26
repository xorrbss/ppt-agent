export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_03 = String.raw`    } catch (_pseudoError) {
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

  function sourceTablePaintOrder(element) {
    if (!sourceFitV2 || element.tagName !== "TABLE" ||
        element.getAttribute("data-native") !== "table" ||
        !element.getAttribute("data-source-block-id")) return null;
    var wrapper = element.parentElement;
    if (!wrapper || !wrapper.classList.contains("cx-geo")) return null;
    var ownStyle = getComputedStyle(element);
    var wrapperStyle = getComputedStyle(wrapper);
    if (ownStyle.zIndex !== "auto" || wrapperStyle.position !== "absolute" ||
        !/^-?\d+$/.test(wrapperStyle.zIndex)) return null;
    var zIndex = Number(wrapperStyle.zIndex);
    return Number.isSafeInteger(zIndex) ? { wrapper: wrapper, zIndex: zIndex } : null;
  }

  function safetyReasons(element, kind, transform) {
    var sourceTableOrder = sourceTablePaintOrder(element);
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
        // Pure translation is already included in viewport bounds. A centred
        // rigid rotation is accumulated into every descendant's native
        // rotation; scale, skew, perspective, and off-centre rotation still
        // cannot be represented without changing descendant geometry.
        if (!ancestorTransform.safe) {
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
      if (style.zIndex && style.zIndex !== "auto" &&
          (!sourceTableOrder || current !== sourceTableOrder.wrapper)) reasons.push("unknown-z-order");
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
          ((targetStyle.zIndex && targetStyle.zIndex !== "auto" &&
            (!sourceTableOrder || target !== sourceTableOrder.wrapper)) ||
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
`;
