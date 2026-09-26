export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_13 = String.raw`        (!generatedFlexItem || hasDirectLineBreak)
      ) return;
      Array.from(element.childNodes).forEach(function (node) {
        if (node.nodeType !== Node.TEXT_NODE || !(node.textContent || "").trim()) return;
        var span = document.createElement("span");
        span.setAttribute("data-presenton-authored-direct-text", "true");
        node.parentNode.insertBefore(span, node);
        span.appendChild(node);
      });
    });

    // Floated inline descendants participate in their parent's prose flow in
    // the DOM, but are laid out as independent boxes by CSS. Preserve that
    // separation in PowerPoint so right-aligned KPIs and badges do not get
    // appended to the preceding paragraph at its left edge.
    if (!declaredIdentity) Array.from(document.body.querySelectorAll("*")).forEach(function (element) {
      if (
        getComputedStyle(element).cssFloat !== "none" &&
        Boolean((element.innerText || element.textContent || "").trim())
      ) {
        element.setAttribute(ISOLATED_INLINE_ATTRIBUTE, "true");
      }
    });

    // Body owns the full-slide background in many authored templates. Treat it
    // as an editable rectangle so the residual PNG can stay transparent above
    // native cards and connector geometry.
    // Declared identity may assign the semantic document root to <html>. Keep
    // it in the observation graph so a data-qid on <body> never points at an
    // unobserved parent. Compatibility extraction retains its historic body
    // root and ordinal behavior.
    var allElements = (declaredIdentity
      ? [document.documentElement, document.body]
      : [document.body]
    ).concat(Array.from(document.body.querySelectorAll("*")));
    var complexSvgImagePayloads = new Map();
    var complexHtmlImagePayloads = new Map();
    for (var svgIndex = 0; svgIndex < allElements.length; svgIndex += 1) {
      var svgElement = allElements[svgIndex];
      if (
        svgElement.tagName && svgElement.tagName.toUpperCase() === "SVG" &&
        complexKind(svgElement) === "complex-diagram"
      ) {
        var svgReasons = [];
        var svgBounds = unrotatedRect(svgElement, analyzeTransform(svgElement).rotationDeg);
        complexSvgImagePayloads.set(
          svgElement,
          await svgImagePayload(svgElement, svgReasons, svgBounds)
        );
        rememberLocalFitImage(svgElement, svgBounds, complexSvgImagePayloads.get(svgElement), "source");
      } else if (
        svgElement.tagName && svgElement.namespaceURI !== "http://www.w3.org/2000/svg" &&
        complexKind(svgElement) === "complex-diagram" &&
        /(^|\s)(visual|scene)(\s|$)/.test(
          typeof svgElement.className === "string" ? svgElement.className.toLowerCase() : ""
        )
      ) {
        var htmlReasons = [];
        var htmlBounds = unrotatedRect(svgElement, analyzeTransform(svgElement).rotationDeg);
        complexHtmlImagePayloads.set(
          svgElement,
          await htmlDiagramImagePayload(svgElement, htmlReasons, htmlBounds)
        );
        rememberLocalFitImage(svgElement, htmlBounds, complexHtmlImagePayloads.get(svgElement), "suppressed");
      }
    }
    var coveredRoots = [];
    var records = [];
    var warnings = [];

    allElements.forEach(function (element, sourceIndex) {
      if (["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT", "TEMPLATE"].includes(element.tagName)) {
        return;
      }
      if (
        !declaredIdentity &&
        !element.hasAttribute(ISOLATED_INLINE_ATTRIBUTE) &&
        coveredRoots.some(function (root) { return root.contains(element); })
      ) return;
      var elementVisible = visibleElement(element);
      // Declared QIDs are part of the immutable semantic denominator even
      // when Chromium does not paint them (for example a responsive branch
      // hidden with display:none).  Dropping them here makes the observation
      // graph smaller than the sealed identity registry and turns an audited
      // raster fallback into a generic QID mismatch.  Compatibility mode may
      // continue to omit invisible DOM nodes because it has no declared
      // ownership graph.
      if (!elementVisible && !declaredIdentity) return;

      var complexReason = complexKind(element);
      var candidateKind = null;
      if (
        complexReason === "complex-diagram" &&
        (complexSvgImagePayloads.has(element) || complexHtmlImagePayloads.has(element))
      ) {
        candidateKind = "image";
      }
      else if (complexReason) candidateKind = "complex";
      else if (element.tagName === "IMG") candidateKind = "image";
      else if (looksLikeShape(element) && (nestedBlockContent(element) || !textRoot(element))) candidateKind = "shape";
      else if (textRoot(element)) candidateKind = "text";
      else if (looksLikeShape(element)) candidateKind = "shape";
      // Declared QIDs are identity nodes even when Chromium propagates their
      // paint (notably the <html> canvas background) and ordinary heuristics
      // would omit them. Preserve that ownership node as a shape observation;
      // reconciliation/admission can still demote it truthfully.
      else if (declaredIdentity && element.hasAttribute("data-qid")) candidateKind = "shape";
      if (!candidateKind) return;

      var declaredQid = element.getAttribute("data-qid");
      if (declaredIdentity && (!declaredQid || !declaredIdSet.has(declaredQid))) {
        return;
      }

      var id = declaredIdentity
        ? declaredQid
        : "h1-" + String(records.length + 1).padStart(4, "0");
      var transform = analyzeTransform(element);
      var rotationDeg = effectiveRigidRotation(element, transform);
      var reasons = safetyReasons(element, candidateKind, transform);
      if (complexSvgImagePayloads.has(element)) {
        reasons = complexSvgImageReasons(reasons);
      }
      if (complexHtmlImagePayloads.has(element)) {
        // The whole semantic visual is captured exactly as a transparent PNG;
        // its internal decoration, clipping, pseudo paint, and local z-index
        // are therefore part of the payload rather than promotion hazards.
        reasons = [];
      }
      var bounds = svgShapeElement(element)
        ? svgShapeBounds(element, reasons)
        : unrotatedRect(element, rotationDeg);
      if (complexReason && candidateKind === "complex") reasons.push(complexReason);
      if (bounds.width <= 0 || bounds.height <= 0 || !Object.values(bounds).every(Number.isFinite)) {
        reasons.push("invalid-bounds");
      }
      if (
        bounds.x < 0 || bounds.y < 0 ||
        bounds.x + bounds.width > WIDTH || bounds.y + bounds.height > HEIGHT
      ) {
        reasons.push("outside-slide");
      }

      var sourceTableOrder = sourceTablePaintOrder(element);
      var zIndexText = sourceTableOrder ? String(sourceTableOrder.zIndex) : getComputedStyle(element).zIndex;
      var zIndex = /^-?\d+$/.test(zIndexText) ? Number.parseInt(zIndexText, 10) : null;
      var observation = {
        id: id,
        identityAuthority: declaredIdentity ? "declared-qid" : "compatibility-ordinal",
        parentElementId: declaredIdentity
          ? (function () {
              var parent = element.parentElement && element.parentElement.closest("[data-qid]");
              return parent ? parent.getAttribute("data-qid") : null;
            })()
          : null,
        domPath: domPath(element),
        tagName: element.tagName.toLowerCase(),
        sourceIndex: sourceIndex,
        cssZIndex: zIndex,
        boundsPx: bounds,
        rotationDeg: rotationDeg,
        opacity: effectiveOpacity(element),
        candidateKind: candidateKind,
        fallbackReasons: reasons,
      };
      if (complexReason === "complex-table" && candidateKind === "complex") {
        var tableEvidence = tableCellEvidence(element);
        if (tableEvidence) observation.tableEvidence = tableEvidence;
        if ((element.getAttribute("data-native") || "").trim().toLowerCase() === "table") {
          observation.nativeHint = "table";
        }
      }

      try {
        if (candidateKind === "text") {
          if (nestedBlockContent(element)) reasons.push("complex-content");
          var tagName = element.tagName.toUpperCase();
          var browserPlainText = element.innerText ||
            (["TEXT", "TEXTPATH"].includes(tagName) ? element.textContent : "") || "";
          var extractedRuns = textRuns(element);
          // One native textbox cannot preserve the hole occupied by an
          // independently owned inline child. Keep only this parent's paint
          // on the raster layer instead of collapsing its surrounding runs.
          if (extractedRuns.discontinuousOwnership) reasons.push("complex-content");
          var renderedPlainText = extractedRuns.runs.map(function (run) { return run.text; }).join("");
          var ownsIsolatedInline = Boolean(
            element.querySelector("[" + ISOLATED_INLINE_ATTRIBUTE + "]")
          ) || Array.from(element.querySelectorAll("[data-qid]")).some(independentlyObservedText);
          if (extractedRuns.failed) reasons.push("run-extraction-error");
          if (
            !ownsIsolatedInline &&
            normalizeWhitespace(extractedRuns.identityText) !== normalizeWhitespace(browserPlainText)
          ) {
            reasons.push("ambiguous-whitespace");
          }
          var plainText = reasons.includes("ambiguous-whitespace")
            ? browserPlainText
            : renderedPlainText;
          var elementTextStyle = textStyle(element, plainText);
          // Preserve the authored CSS wrapping policy. Browser visual lines are
          // still emitted as explicit breaks for fidelity, while PowerPoint's
          // own wrapping remains enabled as a safety net when installed font
          // metrics differ from Chromium. Genuine CSS white-space: nowrap
          // content continues to export with wrapMode="no-wrap".
          observation.text = {
            role: inferTextRole(element, plainText.trim()),
            plainText: plainText,
            paragraphs: plainText.split(/\n+/).map(function (value) { return value.trim(); }).filter(Boolean),
            style: elementTextStyle,
            runs: extractedRuns.runs,
          };
          var containerShape = textContainerShape(element, bounds);
          if (containerShape) observation.text.containerShape = containerShape;
          var contentBounds = textContentRect(element, bounds, rotationDeg);
          observation.text.layout = textLayout(
            element,
            bounds,
            contentBounds,
            extractedRuns.runs
          );
          observation.boundsPx = contentBounds;
        } else if (candidateKind === "image") {
          observation.image = complexSvgImagePayloads.has(element)
            ? complexSvgImagePayloads.get(element)
            : complexHtmlImagePayloads.has(element)
              ? complexHtmlImagePayloads.get(element)
              : imagePayload(element, reasons, bounds);
          if (!observation.image) reasons.push("extraction-error");
        } else if (candidateKind === "shape") {
          bounds = contentSafeShapeBounds(element, bounds);
          observation.boundsPx = bounds;
          observation.shape = shapePayload(
            element,
            reasons,
            bounds,
            element.children.length > 0 || Boolean((element.textContent || "").trim())
          );
        }
      } catch (_elementError) {
        reasons.push("extraction-error");
      }

      observation.fallbackReasons = unique(reasons);
      records.push({ element: element, observation: observation });
      if (
        ((candidateKind === "complex" || candidateKind === "image") && (
          element.tagName.toUpperCase() === "CANVAS" ||
          (element.tagName.toUpperCase() === "SVG" && complexReason !== "svg-text")
        )) ||
        (candidateKind === "text" && !nestedBlockContent(element))
      ) coveredRoots.push(element);
    });

    // CSS generated content is not represented by querySelectorAll and
    // therefore used to remain fused into the residual backplate. Materialize
    // empty painted pseudo-elements only long enough to measure and serialize
    // them as independent editable PowerPoint shapes. Their authored paint is
    // suppressed separately when the shape is promoted.
    var elementSourceIndexes = new Map();
    allElements.forEach(function (element, index) {
      elementSourceIndexes.set(element, index);
    });
    if (!declaredIdentity) allElements.forEach(function (element) {
      ["::before", "::after"].forEach(function (pseudo) {
        var hostSourceIndex = elementSourceIndexes.get(element) || 0;
        var pseudoSourceIndex = hostSourceIndex;
        if (pseudo === "::after") {
          for (var index = hostSourceIndex + 1; index < allElements.length; index += 1) {
            if (!element.contains(allElements[index])) break;
            pseudoSourceIndex = index;
          }
        }
        var id = "h1-" + String(records.length + 1).padStart(4, "0");
        var record = materializedPseudoShape(element, pseudo, pseudoSourceIndex, id);
        if (record) records.push(record);
      });
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
          !record.pseudo &&
          observation.candidateKind !== "complex" &&
          !complexSvgImagePayloads.has(record.element) &&
          !complexHtmlImagePayloads.has(record.element) &&
          (isOccluded(record.element, observation) ||
            isRectOccluded(record.element, observation, allElements) ||
            isPseudoOccluded(record.element, observation, allElements) ||
            isShadowOccluded(record.element, observation, allElements) ||
            isExternalPaintOccluded(record.element, observation, allElements))
        ) {
          observation.fallbackReasons = unique(
            observation.fallbackReasons.concat(["occluded"])
`;
