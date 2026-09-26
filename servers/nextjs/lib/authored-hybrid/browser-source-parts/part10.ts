export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_10 = String.raw`      return stack.slice(0, ownIndex).some(function (item) {
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
`;
