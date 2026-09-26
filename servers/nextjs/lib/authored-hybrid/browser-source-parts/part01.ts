export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_01 = String.raw`  function fontFamilies(value) {
    return value
      .split(",")
      .map(function (family) {
        return family.trim().replace(/^['\"]|['\"]$/g, "");
      })
      .filter(Boolean);
  }

  function expandedFontFamilies(families) {
    var result = [];
    var seen = new Set();
    function append(family) {
      var normalized = String(family || "").trim().replace(/^['\"]|['\"]$/g, "");
      var key = normalized.toLowerCase();
      if (!normalized || seen.has(key)) return;
      seen.add(key);
      result.push(normalized);
    }
    families.forEach(function (family) {
      append(family);
      append(fontFallbackPolicy.get(String(family || "").trim().toLowerCase()));
    });
    return result;
  }

  function cjkFallbacks(text, families) {
    if (!/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/u.test(text)) return [];
    var serifIntent = families.some(function (family) {
      return /(?:^|\s)(?:serif|batang|batangche|cambria)(?:\s|$)/i.test(family) ||
        /(?:noto|source)\s+serif/i.test(family);
    });
    var fallback = [
      serifIntent ? "Noto Serif KR" : "Malgun Gothic",
      serifIntent ? "Batang" : "Noto Sans KR",
      "Apple SD Gothic Neo",
      "Arial Unicode MS",
      serifIntent ? "serif" : "sans-serif",
    ];
    var knownCjk = new Set(
      fallback.map(function (family) {
        return family.toLowerCase();
      })
    );
    var preferred = [];
    families.forEach(function (family) {
      var lower = family.toLowerCase();
      var mapped = fontFallbackPolicy.get(lower);
      if (mapped && knownCjk.has(mapped.toLowerCase())) preferred.push(mapped);
      if (lower !== "sans-serif" && knownCjk.has(lower)) preferred.push(family);
    });
    preferred = unique(preferred);
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

  function textScript(character) {
    if (/[⺀-⿿々-〇぀-ヿ㄀-ㄯㆠ-ㆿㇰ-ㇿ㐀-䶿一-鿿가-힯豈-﫿]/u.test(character)) {
      return "cjk";
    }
    if (/[0-9A-Za-zÀ-ʯ]/u.test(character)) return "latin";
    return "common";
  }

  function scriptSlices(text) {
    if (!text) return [];
    var points = Array.from(text);
    var scripts = points.map(textScript);
    scripts.forEach(function (script, index) {
      if (script !== "common") return;
      var previous = index > 0 ? scripts[index - 1] : null;
      if (previous && previous !== "common") {
        scripts[index] = previous;
        return;
      }
      var following = scripts.slice(index + 1).find(function (candidate) {
        return candidate !== "common";
      });
      if (following) scripts[index] = following;
    });
    var result = [];
    var utf16Offset = 0;
    var start = 0;
    var value = "";
    var activeScript = scripts[0];
    points.forEach(function (point, index) {
      var script = scripts[index];
      if (value && script !== activeScript) {
        result.push({ text: value, start: start, end: utf16Offset, script: activeScript });
        start = utf16Offset;
        value = "";
        activeScript = script;
      }
      value += point;
      utf16Offset += point.length;
    });
    result.push({ text: value, start: start, end: utf16Offset, script: activeScript });
    return result;
  }

  function textStyle(element, text) {
    var style = getComputedStyle(element);
    var families = expandedFontFamilies(
      fontFamilies(style.fontFamily || "sans-serif")
    );
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
    // A direct text node in a flex/grid container is laid out as an anonymous
    // item. Generated badges and shape labels commonly center that item with
    // justify-content/place-items while leaving text-align at its default
    // value. DrawingML has no anonymous flex/grid item, so retaining left
    // here pins the text to the shape's left edge. Map the centered horizontal
    // layout axis to the equivalent paragraph alignment. Preserve any authored
    // non-default paragraph alignment.
    var display = style.display || "";
    if (alignment === "left") {
      var horizontalLayoutAlignment = "";
      if (display === "flex" || display === "inline-flex") {
        var horizontalFlexDirection = style.flexDirection || "row";
        var horizontalFlexIsColumn =
          horizontalFlexDirection === "column" ||
          horizontalFlexDirection === "column-reverse";
        horizontalLayoutAlignment = horizontalFlexIsColumn
          ? style.alignItems
          : style.justifyContent;
      } else if (display === "grid" || display === "inline-grid") {
        horizontalLayoutAlignment =
          style.justifyItems === "center" ? style.justifyItems : style.justifyContent;
      }
      if (horizontalLayoutAlignment === "center") alignment = "center";
    }
    var fontSizePt = sourceFitV2 ? round(fontSizePx * 0.75) : Math.max(9, round(fontSizePx * 0.75));
    var lineHeightMultiple = round(lineHeightPx / Math.max(fontSizePx, 0.01));
    // Which alignment property maps to the vertical (block) axis depends on the
    // layout: a flex ROW centers vertically via align-items (cross axis) while its
    // justify-content controls the HORIZONTAL axis; a flex COLUMN is the reverse;
    // grid maps align-items to the vertical axis. Treating a row's justify-content
    // as a vertical anchor (the old bug) shifted centered text within its box.
    var vertical = "top";
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
      ...(sourceFitV2 ? { sourceFitPolicy: "composer-source-fit/v2" } : {}),
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
`;
