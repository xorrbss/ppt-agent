export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_05 = String.raw`        var previous = index > 0 ? runs[index - 1] : null;
        var next = index + 1 < runs.length ? runs[index + 1] : null;
        if (runs[index].softLineBreak) {
          // A soft break is the browser's own wrap point, and it is dropped at
          // serialization -- PowerPoint rewraps the paragraph itself. So the
          // whitespace the browser wrapped at is the only word boundary the
          // exported text will ever carry. Stripping both sides, as a surviving
          // hard break may, glued the words together across every wrapped line
          // ("...완전하게" + "보존되어야..."). Keep exactly one space, and keep
          // none where the source had none, so a CJK mid-word wrap stays
          // unspaced.
          var wrapped =
            (previous !== null && / $/.test(previous.text)) ||
            (next !== null && /^ /.test(next.text));
          if (previous !== null && previous.text !== "\n") {
            previous.text = previous.text.replace(/ +$/g, "");
            if (wrapped) previous.text += " ";
          }
          if (next !== null) next.text = next.text.replace(/^ +/g, "");
          continue;
        }
        if (previous !== null) previous.text = previous.text.replace(/ +$/g, "");
        if (next !== null) next.text = next.text.replace(/^ +/g, "");
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
              pushLineBreak(parent, parent.getBoundingClientRect(), "line");
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
              pushLineBreak(parent, characterRect, "soft");
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
      if (
        node !== element &&
        (node.hasAttribute(ISOLATED_INLINE_ATTRIBUTE) || independentlyObservedText(node))
      ) {
        if (runs.some(function (run) { return Boolean(run.text.trim()); })) {
          skippedOwnedInlineAfterText = true;
        }
        return;
      }
      if (node.tagName === "BR") {
        var parent = node.parentElement || element;
        var rect = parent.getBoundingClientRect();
        runs.push({
          text: "\n",
          boundsPx: { x: round(rect.left), y: round(rect.top), width: 0, height: 0 },
          fragmentRectsPx: [],
          style: textStyle(parent, "\n"),
          breakKind: "line",
        });
        activeLineBand = null;
        return;
      }
      var lineBoundary = createsTextLineBoundary(node);
      if (lineBoundary && runs.some(function (run) {
        return run.text !== "\n" && Boolean(run.text.trim());
      })) {
        pushLineBreak(node, node.getBoundingClientRect(), "paragraph");
        activeLineBand = null;
      }
      Array.from(node.childNodes).forEach(visit);
      if (lineBoundary && hasFollowingTextContent(node)) {
        pushLineBreak(node, node.getBoundingClientRect(), "paragraph");
        activeLineBand = null;
      }
    }

    Array.from(element.childNodes).forEach(visit);
    normalizeCollapsibleRunWhitespace();
    var identityText = runs.map(function (run) {
      return run.softLineBreak ? "" : run.text;
    }).join("");
    return { runs: runs, failed: failed, identityText: identityText, discontinuousOwnership: discontinuousOwnership };
  }

  function edgeValues(style, prefix, suffix) {
    return {
      top: round(number(style[prefix + "Top" + suffix], 0)),
      right: round(number(style[prefix + "Right" + suffix], 0)),
      bottom: round(number(style[prefix + "Bottom" + suffix], 0)),
      left: round(number(style[prefix + "Left" + suffix], 0)),
    };
  }

  function unionPaintedTextBounds(runs) {
    var fragments = [];
    runs.forEach(function (run) {
      if (run.text === "\n") return;
      (run.fragmentRectsPx || []).forEach(function (rect) {
        if (
          rect && rect.width >= 0 && rect.height > 0 &&
          Object.values(rect).every(Number.isFinite)
        ) fragments.push(rect);
      });
    });
    if (!fragments.length) return null;
    var left = Math.min.apply(null, fragments.map(function (rect) { return rect.x; }));
    var top = Math.min.apply(null, fragments.map(function (rect) { return rect.y; }));
    var right = Math.max.apply(null, fragments.map(function (rect) {
      return rect.x + rect.width;
    }));
    var bottom = Math.max.apply(null, fragments.map(function (rect) {
      return rect.y + rect.height;
    }));
    return {
      x: round(left),
      y: round(top),
      width: round(Math.max(0, right - left)),
      height: round(Math.max(0, bottom - top)),
    };
  }

  function measuredLineRects(runs) {
    var lines = [];
    var sourceLineEpoch = 0;
    runs.forEach(function (run) {
      // Font metric rectangles can overlap across tightly spaced source lines.
      // A captured break is authoritative even when those rectangles overlap.
      if (sourceFitV2 && run.text === "\n") { sourceLineEpoch += 1; return; }
      if (run.text === "\n") return;
      (run.fragmentRectsPx || []).forEach(function (rect) {
        if (!rect || rect.height <= 0 || !Object.values(rect).every(Number.isFinite)) return;
        var match = lines.find(function (line) {
          if (sourceFitV2 && line.sourceLineEpoch !== sourceLineEpoch) return false;
          var overlap = Math.min(line.bottom, rect.y + rect.height) -
            Math.max(line.top, rect.y);
          return overlap >= Math.min(line.height, rect.height) * 0.25;
        });
        if (!match) {
          lines.push({
            sourceLineEpoch: sourceLineEpoch,
            left: rect.x,
            top: rect.y,
            right: rect.x + rect.width,
            bottom: rect.y + rect.height,
            height: rect.height,
          });
          return;
        }
        match.left = Math.min(match.left, rect.x);
        match.top = Math.min(match.top, rect.y);
        match.right = Math.max(match.right, rect.x + rect.width);
        match.bottom = Math.max(match.bottom, rect.y + rect.height);
        match.height = Math.max(1, match.bottom - match.top);
      });
    });
    return lines
      .sort(function (left, right) { return (sourceFitV2 ? left.sourceLineEpoch - right.sourceLineEpoch : 0) || left.top - right.top || left.left - right.left; })
      .map(function (line) {
        return {
          x: round(line.left),
          y: round(line.top),
          width: round(Math.max(0, line.right - line.left)),
          height: round(Math.max(0, line.bottom - line.top)),
        };
      });
  }

  function computedTextAlignSource(element, style) {
    var parent = element.parentElement;
    if (!parent) return "default";
    var parentStyle = getComputedStyle(parent);
    return String(style.textAlign || "") === String(parentStyle.textAlign || "")
      ? "inherited"
      : "self";
  }

  function textLayout(element, boxBounds, contentBounds, runs) {
    var style = getComputedStyle(element);
    var display = String(style.display || "");
    var isFlex = display === "flex" || display === "inline-flex";
    var flexDirection = isFlex &&
      ["row", "row-reverse", "column", "column-reverse"].includes(style.flexDirection)
      ? style.flexDirection
      : null;
    var hasPaintedText = runs.some(function (run) {
      return run.text !== "\n" && Boolean(run.text) &&
        (run.fragmentRectsPx || []).length > 0;
    });
    var lineRectsPx = measuredLineRects(runs);
    var nextLineIndex = 0;
    runs.forEach(function (run) {
      if (run.text === "\n") {
        run.lineIndex = nextLineIndex;
        nextLineIndex += 1;
        return;
      }
      var fragments = (run.fragmentRectsPx || []).filter(function (rect) {
        return rect && rect.height > 0 && Object.values(rect).every(Number.isFinite);
      });
      if (!fragments.length || !lineRectsPx.length) {
        run.lineIndex = nextLineIndex;
        return;
`;
