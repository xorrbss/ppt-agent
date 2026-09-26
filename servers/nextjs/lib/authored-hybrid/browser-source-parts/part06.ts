export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_06 = String.raw`      }
      var centerY = fragments.reduce(function (sum, rect) {
        return sum + rect.y + rect.height / 2;
      }, 0) / fragments.length;
      var nearestLine = lineRectsPx.reduce(function (best, line, index) {
        var distance = Math.abs(centerY - (line.y + line.height / 2));
        return distance < best.distance ? { index: index, distance: distance } : best;
      }, { index: 0, distance: Number.POSITIVE_INFINITY });
      run.lineIndex = nearestLine.index;
      nextLineIndex = Math.max(nextLineIndex, nearestLine.index);
    });
    var explicitLineCount = hasPaintedText
      ? 1 + runs.filter(function (run) { return run.text === "\n"; }).length
      : 0;
    var lineCount = Math.max(explicitLineCount, lineRectsPx.length);
    return {
      boxBoundsPx: boxBounds,
      contentBoundsPx: contentBounds,
      paintedTextBoundsPx: unionPaintedTextBounds(runs),
      paddingPx: edgeValues(style, "padding", ""),
      borderPx: edgeValues(style, "border", "Width"),
      marginPx: edgeValues(style, "margin", ""),
      rowGapPx: Math.max(0, round(number(style.rowGap, 0))),
      columnGapPx: Math.max(0, round(number(style.columnGap, 0))),
      display: display,
      flexDirection: flexDirection,
      alignItems: String(style.alignItems || "normal"),
      justifyContent: String(style.justifyContent || "normal"),
      textAlignSource: computedTextAlignSource(element, style),
      lineCount: lineCount,
      singleLine: lineCount === 1,
      lineRectsPx: lineRectsPx,
      // The root element's CSS margins have already participated in layout
      // and are therefore represented by boxBoundsPx. Reapplying them as
      // DrawingML paragraph spacing would move the text a second time inside
      // its PowerPoint text box. Internal block boundaries are emitted as
      // paragraph breaks; their measured geometry remains represented by the
      // captured line boxes rather than by the text root's outer margins.
      paragraphSpacingPx: {
        before: 0,
        after: 0,
      },
    };
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
    var positionTokens = objectPosition.split(/\s+/).filter(Boolean);
    var positionValue = function (token, horizontal) {
      token = (token || "center").toLowerCase();
      if (token === "center") return 0.5;
      if ((horizontal && token === "left") || (!horizontal && token === "top")) return 0;
      if ((horizontal && token === "right") || (!horizontal && token === "bottom")) return 1;
      var match = /^(\d+(?:\.\d+)?)%$/.exec(token);
      var value = match ? Number(match[1]) / 100 : NaN;
      return Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
    };
    var positionX = positionTokens.length <= 2 ? positionValue(positionTokens[0], true) : null;
    var positionY = positionTokens.length <= 2 ? positionValue(positionTokens[1], false) : null;
    if (positionX === null || positionY === null) {
      reasons.push("unsupported-object-position");
    }
    var crop = { left: 0, top: 0, right: 0, bottom: 0 };
    if (objectFit === "cover" && bounds.width > 0 && bounds.height > 0 && element.naturalWidth > 0) {
      var boxRatio = bounds.width / bounds.height;
      var imageRatio = element.naturalWidth / element.naturalHeight;
      if (imageRatio > boxRatio) {
        var visibleWidth = boxRatio / imageRatio;
        crop.left = round((1 - visibleWidth) * positionX);
        crop.right = round((1 - visibleWidth) * (1 - positionX));
      } else if (imageRatio < boxRatio) {
        var visibleHeight = imageRatio / boxRatio;
        crop.top = round((1 - visibleHeight) * positionY);
        crop.bottom = round((1 - visibleHeight) * (1 - positionY));
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

  function sourceCellParagraphs(cell) {
    var paragraphs = [];
    var candidates = Array.from(cell.querySelectorAll("p"));
    if (!candidates.length) candidates = [cell];
    candidates.forEach(function (paragraph) {
      var runs = [];
      var lastRunOwner = null;
      function walk(node) {
        Array.from(node.childNodes).forEach(function (child) {
          if (child.nodeType === Node.TEXT_NODE && child.textContent) {
            var style = getComputedStyle(child.parentElement);
            var color = parseColor(style.color);
            if (runs.length && lastRunOwner === child.parentElement) { runs[runs.length - 1].text += child.textContent; return; }
            lastRunOwner = child.parentElement;
            runs.push({ text: child.textContent, fontFamily: fontFamilies(style.fontFamily)[0], fontSizePt: round(number(style.fontSize, 0) * 0.75), weight: parseInt(style.fontWeight, 10) || 400, italic: style.fontStyle === "italic", underline: (style.textDecorationLine || "").includes("underline"), color: "#" + color.hex });
          } else if (child.nodeType === Node.ELEMENT_NODE) {
            if (child.tagName === "BR") {
              if (!runs.length) {
                var breakStyle = getComputedStyle(child.parentElement);
                lastRunOwner = child.parentElement;
                runs.push({ text: "", fontFamily: fontFamilies(breakStyle.fontFamily)[0], fontSizePt: round(number(breakStyle.fontSize, 0) * .75), weight: parseInt(breakStyle.fontWeight, 10) || 400, italic: breakStyle.fontStyle === "italic", underline: (breakStyle.textDecorationLine || "").includes("underline"), color: "#" + parseColor(breakStyle.color).hex });
              }
              runs[runs.length - 1].text += "\n";
            }
            else walk(child);
          }
        });
      }
      walk(paragraph);
      if (!runs.length && paragraph === cell) return;
      if (!runs.length) {
        var emptyStyle = getComputedStyle(paragraph.querySelector("span") || paragraph);
        runs.push({ text: "", fontFamily: fontFamilies(emptyStyle.fontFamily)[0], fontSizePt: round(number(emptyStyle.fontSize, 0) * .75), weight: parseInt(emptyStyle.fontWeight, 10) || 400, italic: emptyStyle.fontStyle === "italic", underline: (emptyStyle.textDecorationLine || "").includes("underline"), color: "#" + parseColor(emptyStyle.color).hex });
      }
      var paragraphStyle = getComputedStyle(paragraph);
      var alignment = paragraphStyle.textAlign;
      var lineHeight = number(paragraphStyle.lineHeight, Number.NaN);
      paragraphs.push({ runs: runs, alignment: alignment === "center" ? "center" : alignment === "right" || alignment === "end" ? "right" : alignment === "justify" ? "justify" : "left",
        ...(Number.isFinite(lineHeight) && lineHeight > 0 ? { lineHeightPt: round(lineHeight * .75) } : {}),
        spaceBeforePt: round(Math.max(0, number(paragraphStyle.marginTop, 0)) * .75),
        spaceAfterPt: round(Math.max(0, number(paragraphStyle.marginBottom, 0)) * .75) });
    });
    return paragraphs;
  }

  function tableCellEvidence(element) {
    if (element.tagName.toUpperCase() !== "TABLE" || !element.rows) return null;
    var rows = Array.from(element.rows);
    if (!rows.length || rows.length > 100) return null;
    var result = rows.map(function () { return []; });
    var logicalCells = rows.map(function () { return []; });
    var merges = [];
    for (var rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      var column = 0;
      for (var cell of Array.from(rows[rowIndex].cells)) {
        while (result[rowIndex][column]) column += 1;
        if (!sourceFitV2 && (cell.rowSpan !== 1 || cell.colSpan !== 1)) return null;
        if (rowIndex + cell.rowSpan > rows.length || column + cell.colSpan > 20) return null;
        var style = getComputedStyle(cell);
        var fill = parseColor(style.backgroundColor);
        var color = parseColor(style.color);
        var families = fontFamilies(style.fontFamily || "");
        var sizePx = number(style.fontSize, Number.NaN);
        var weight = Number.parseInt(style.fontWeight, 10);
        if (!Number.isFinite(weight)) weight = style.fontWeight === "bold" ? 700 : 400;
        if (!color || color.alpha !== 1 || !families.length || !Number.isFinite(sizePx) || (fill && fill.alpha !== 0 && fill.alpha !== 1) || style.fontStyle !== "normal" || (style.textDecorationLine && style.textDecorationLine !== "none")) return null;
        var paragraphs = sourceFitV2 ? sourceCellParagraphs(cell) : null;
        var value = {
          text: paragraphs ? paragraphs.map(function (p) { return p.runs.map(function (r) { return r.text; }).join(""); }).join("\n") : String(cell.innerText || cell.textContent || "").replace(/\r\n?/g, "\n").trim(),
          ...(fill && fill.alpha === 1 ? { fill: "#" + fill.hex } : {}),
          textColor: "#" + color.hex, bold: weight >= 600, fontFamily: families[0], fontSizePt: round(sizePx * 0.75),
          ...(paragraphs && paragraphs.length ? { paragraphs: paragraphs } : {}),
        };
        if (cell.rowSpan > 1 || cell.colSpan > 1) merges.push({ row: rowIndex, column: column, rowSpan: cell.rowSpan, columnSpan: cell.colSpan });
        for (var r = rowIndex; r < rowIndex + cell.rowSpan; r += 1) for (var c = column; c < column + cell.colSpan; c += 1) {
          if (result[r][c]) return null;
          result[r][c] = r === rowIndex && c === column ? value : Object.assign({}, value, { text: "", paragraphs: undefined });
          logicalCells[r][c] = cell;
        }
        column += cell.colSpan;
      }
    }
    var width = result[0].length;
    if (!width || result.some(function (row) { return row.length !== width || Array.from(row).some(function (cell) { return !cell; }); })) return null;
    var layout = tableLayoutEvidence(element, rows);
    if (sourceFitV2) {
      layout.cellStyles = logicalCells.map(function (cells) { return cells.map(function (cell) { return layout.cellStyles[cell.parentElement.rowIndex][cell.cellIndex]; }); });
      var cols = Array.from(element.querySelectorAll("col"));
      layout.columnWidthsPx = cols.length === width ? cols.map(function (col) { return round(col.getBoundingClientRect().width); }) : logicalCells[0].map(function (cell) { return round(cell.getBoundingClientRect().width / cell.colSpan); });
      layout.mergedCells = merges;
      layout.sourceFitPolicy = "composer-source-fit/v2";
    }
    return Object.assign({ rows: result }, layout);
  }

  // Geometry and style facts for markup-driven native tables. Kept outside
  // "rows" so declared-table reconciliation, which compares rows only, is
  // unaffected by this richer evidence.
  function tableLayoutEvidence(element, rows) {
    var unsupportedPaint = false;
    var edges = ["Top", "Right", "Bottom", "Left"];
    function paintUnsupported(style) {
      if (style.backgroundImage && style.backgroundImage !== "none") return true;
      return edges.some(function (edge) {
        var borderStyle = style["border" + edge + "Style"];
        var width = number(style["border" + edge + "Width"], 0);
        return width > 0 && borderStyle !== "none" && borderStyle !== "hidden" && borderStyle !== "solid";
      });
    }
    var rootStyle = getComputedStyle(element);
    if (paintUnsupported(rootStyle)) unsupportedPaint = true;
    var rootFill = parseColor(rootStyle.backgroundColor);
    var cellStyles = rows.map(function (row) {
      if (paintUnsupported(getComputedStyle(row))) unsupportedPaint = true;
      return Array.from(row.cells).map(function (cell) {
        var style = getComputedStyle(cell);
        if (paintUnsupported(style)) unsupportedPaint = true;
        var borders = {};
        edges.forEach(function (edge) {
          var width = number(style["border" + edge + "Width"], 0);
          var color = parseColor(style["border" + edge + "Color"]);
          if (width <= 0 || style["border" + edge + "Style"] !== "solid" || !color) return;
          if (color.alpha !== 1) { unsupportedPaint = true; return; }
          borders[edge.toLowerCase()] = { color: "#" + color.hex, widthPx: round(width) };
        });
        var textAlign = style.textAlign;
        var vertical = style.verticalAlign;
        return {
          horizontalAlign: textAlign === "center" ? "center"
            : textAlign === "right" || textAlign === "end" ? "right"
            : textAlign === "justify" ? "justify" : "left",
          verticalAlign: vertical === "top" ? "top" : vertical === "bottom" ? "bottom" : "middle",
          borders: borders,
          ...(sourceFitV2 ? { paddingPx: { top: number(style.paddingTop, 0), right: number(style.paddingRight, 0), bottom: number(style.paddingBottom, 0), left: number(style.paddingLeft, 0) } } : {}),
        };
      });
    });
    return {
      columnWidthsPx: Array.from(rows[0].cells).map(function (cell) {
        return round(cell.getBoundingClientRect().width);
      }),
      rowHeightsPx: rows.map(function (row) { return round(row.getBoundingClientRect().height); }),
      cellStyles: cellStyles,
      ...(rootFill && rootFill.alpha === 1 ? { rootFill: "#" + rootFill.hex } : {}),
      ...(element.querySelector("img,svg,canvas,video,picture,iframe") ? { containsReplacedContent: true } : {}),
      ...(unsupportedPaint ? { unsupportedPaint: true } : {}),
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
        alt: element.getAttribute("aria-label") || element.getAttribute("title") || "",
        naturalWidth: width,
        naturalHeight: height,
`;
