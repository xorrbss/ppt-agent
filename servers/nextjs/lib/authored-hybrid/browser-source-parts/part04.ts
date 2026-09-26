export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_04 = String.raw`      pseudoPainted(element, "::marker")
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
        boxShadowUnmapped(ownStyle.boxShadow) ||
        (ownStyle.outlineStyle !== "none" &&
          hasPositiveCssComponent(ownStyle.outlineWidth))
      ) {
        reasons.push("unsupported-shape");
      }
    }
    return unique(reasons);
  }

  function complexSvgImageReasons(reasons) {
    // A path-heavy SVG is rendered into its own transparent PNG before the
    // residual backplate is captured. Paint owned by ancestors (cards,
    // shadows, and generated decoration) remains in the backplate because
    // suppression hides only the SVG root. The SVG's internal paint is already
    // represented by the PNG, while final overlap selection preserves browser
    // paint order. Do not let those container-level signals fuse the picture
    // back into the slide-sized bitmap.
    return reasons.filter(function (reason) {
      return ![
        "pseudo-element",
        "external-paint",
        "unknown-z-order",
      ].includes(reason);
    });
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
    if (svgShapeElement(element) && typeof element.getTotalLength === "function") {
      try {
        return element.getTotalLength() > 0 && (parseColor(style.stroke) || parseColor(style.fill));
      } catch (_svgLengthError) {
        return false;
      }
    }
    return rect.width >= 0.5 && rect.height >= 0.5;
  }

  var INLINE_TAGS = new Set([
    "A", "ABBR", "B", "BDI", "BDO", "BR", "CITE", "CODE", "DEL", "EM",
    "I", "INS", "KBD", "MARK", "Q", "S", "SAMP", "SMALL", "SPAN", "STRONG",
    "SUB", "SUP", "TIME", "U", "VAR", "WBR", "TSPAN"
  ]);
  var TEXT_ROOT_TAGS = new Set([
    "H1", "H2", "H3", "H4", "H5", "H6", "P", "LI", "FIGCAPTION",
    "BLOCKQUOTE", "LABEL", "SMALL", "TEXT", "TEXTPATH"
  ]);

  function directText(element) {
    return Array.from(element.childNodes).some(function (node) {
      return node.nodeType === Node.TEXT_NODE && Boolean(node.textContent.trim());
    });
  }

  function textRoot(element) {
    var tag = element.tagName.toUpperCase();
    // SVGTextElement does not expose innerText consistently in Chromium.
    // Its textContent is nevertheless rendered text and must be promoted just
    // like an HTML label so it cannot disappear from an editable export.
    var renderedText = element.innerText ||
      (["TEXT", "TEXTPATH"].includes(tag) ? element.textContent : "") || "";
    if (!renderedText.trim()) return false;
    return TEXT_ROOT_TAGS.has(tag) ||
      (directText(element) && !nestedBlockContent(element)) ||
      inlineFlowTextRoot(element);
  }

  function inlineFlowTextRoot(element) {
    var children = Array.from(element.children);
    if (children.length < 2 || nestedBlockContent(element)) return false;
    var display = getComputedStyle(element).display;
    if ([
      "flex", "inline-flex", "grid", "inline-grid", "table", "inline-table"
    ].includes(display)) return false;
    return children.every(function (child) {
      return INLINE_TAGS.has(child.tagName.toUpperCase()) &&
        ["inline", "contents"].includes(getComputedStyle(child).display);
    });
  }

  function nestedBlockContent(element) {
    return Array.from(element.querySelectorAll("*")).some(function (child) {
      return !INLINE_TAGS.has(child.tagName.toUpperCase());
    });
  }

  function hasDecoratedDescendant(element) {
    return Array.from(element.querySelectorAll("*")).some(function (child) {
      var childStyle = getComputedStyle(child);
      return paintedBackground(childStyle) || hasBorder(childStyle) ||
        hasExternalPaint(childStyle) || pseudoPainted(child, "::before") ||
        pseudoPainted(child, "::after");
    });
  }

  function complexKind(element) {
    // SVG DOM tagName values are lowercase in Chromium, unlike HTML tagName.
    var tag = element.tagName.toUpperCase();
    // Declared tables are admitted through the authored-native-table contract
    // as one editable DrawingML table. Keep the whole TABLE as the observed
    // complex ownership node; declared extraction excludes data-qid-ignore
    // row/cell descendants so they cannot become duplicate native paint.
    if (tag === "TABLE") return "complex-table";
    // Canvas and SVG still need a fidelity fallback because their authored
    // primitives are not represented by ordinary DOM boxes.
    if (tag === "SVG") {
      if (decomposableSvgRoot(element)) return null;
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
    // Some authored templates use a semantic .visual or .scene wrapper for a
    // large, CSS-built hero illustration instead of an SVG. Its nested
    // browser/card/connector primitives cannot be reconstructed reliably as
    // independent PowerPoint shapes, but the complete illustration should
    // still be a selectable object rather than remain fused into the slide
    // backplate.
    // Require an accessible label and substantial geometry so small helpers
    // such as .visual-title are not accidentally collapsed into pictures.
    if (/(^|\s)(visual|scene)(\s|$)/.test(classes)) {
      var visualRect = element.getBoundingClientRect();
      if (
        (element.getAttribute("aria-label") || "").trim() &&
        visualRect.width >= 240 && visualRect.height >= 180 &&
        element.querySelectorAll("*").length >= 12
      ) {
        return "complex-diagram";
      }
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

  function independentlyObservedText(element) {
    // Explicitly declared text identities remain independent edit targets.
    // Undeclared inline emphasis stays in its parent's styled text runs.
    return declaredIdentity && declaredIdSet.has(element.getAttribute("data-qid")) &&
      !complexKind(element) && textRoot(element) &&
      !(looksLikeShape(element) && nestedBlockContent(element));
  }

  function textRuns(element) {
    var runs = [];
    var failed = false;
    var activeLineBand = null;
    var skippedOwnedInlineAfterText = false;
    var discontinuousOwnership = false;

    function rectBand(rect) {
      return {
        top: rect.top,
        bottom: rect.bottom,
        height: Math.max(1, rect.height),
      };
    }

    function sharesVisualLine(rect, band) {
      if (!band) return false;
      var overlap = Math.min(rect.bottom, band.bottom) - Math.max(rect.top, band.top);
      var minimumHeight = Math.min(Math.max(1, rect.height), band.height);
      return overlap >= minimumHeight * 0.25;
    }

    function extendLineBand(band, rect) {
      if (!band) return rectBand(rect);
      // Retain the common vertical band instead of growing a union forever.
      // A union can be stretched by the zero-width space at a browser wrap
      // point until it overlaps the next visual line, losing every subsequent
      // soft break. The intersection remains stable across mixed-size inline
      // runs while separating genuinely different baselines.
      var top = Math.max(band.top, rect.top);
      var bottom = Math.min(band.bottom, rect.bottom);
      if (bottom <= top) return rectBand(rect);
      return { top: top, bottom: bottom, height: Math.max(1, bottom - top) };
    }

    function pushLineBreak(parent, rect, breakKind) {
      if (!runs.length || runs[runs.length - 1].text.endsWith("\n")) return;
      runs.push({
        text: "\n",
        boundsPx: { x: round(rect.left), y: round(rect.top), width: 0, height: 0 },
        fragmentRectsPx: [],
        style: textStyle(parent, "\n"),
        breakKind: breakKind,
        softLineBreak: breakKind === "soft",
      });
    }

    function pushTextSlice(node, parent, start, end) {
      if (end <= start) return;
      var text = (node.textContent || "").slice(start, end);
      if (!text) return;
      if (skippedOwnedInlineAfterText && text.trim()) discontinuousOwnership = true;
      scriptSlices(text).forEach(function (slice) {
        var range = document.createRange();
        range.setStart(node, start + slice.start);
        range.setEnd(node, start + slice.end);
        var fragments = Array.from(range.getClientRects())
          .filter(function (rect) { return rect.width > 0 || rect.height > 0; })
          .map(rectValue);
        var extractedRun = {
          text: slice.text,
          boundsPx: rectValue(range.getBoundingClientRect()),
          fragmentRectsPx: fragments,
          style: textStyle(parent, slice.text),
          script: slice.script,
        };
        localFitExtractedRunSources.set(extractedRun, localFitOriginalTextNodes.get(node));
        if (localFitOriginalWhitespace.has(node)) localFitWhitespaceRuns.add(extractedRun);
        runs.push(extractedRun);
      });
    }

    function normalizeCollapsibleRunWhitespace() {
      var whiteSpace = getComputedStyle(element).whiteSpace;
      if (["pre", "pre-wrap", "break-spaces"].includes(whiteSpace)) return;
      runs.forEach(function (run) {
        // Newlines used only to pretty-print authored HTML are ordinary
        // collapsible whitespace. Keep the standalone newline runs that we
        // created for browser visual lines, but never turn indentation between
        // inline spans into a PowerPoint paragraph break.
        if (run.text !== "\n") run.text = run.text.replace(/[\t\f\v\r\n ]+/g, " ");
      });
      for (var index = 0; index < runs.length; index += 1) {
        if (runs[index].text !== "\n") continue;
`;
