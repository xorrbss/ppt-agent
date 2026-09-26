export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_12 = String.raw`      }
      var element = record.element;
      var kind = record.observation.candidateKind;
      if (record.pseudo) {
        element.setAttribute(pseudoSuppressionAttribute(record.pseudo), "true");
      } else if (kind === "text") {
        if (expected.suppressWholeElement) {
          element.style.setProperty("visibility", "hidden", "important");
        } else {
          [element].concat(Array.from(element.querySelectorAll("*"))).forEach(function (target) {
            target.style.setProperty("-webkit-text-fill-color", "transparent", "important");
            target.style.setProperty("text-decoration-color", "transparent", "important");
            target.style.setProperty("-webkit-text-stroke-color", "transparent", "important");
            target.style.setProperty("text-shadow", "none", "important");
            // SVG text uses fill/stroke presentation properties instead of
            // the HTML text-fill properties above. Clear those paints too so
            // editable OOXML text does not sit on a raster duplicate.
            if (target.namespaceURI === "http://www.w3.org/2000/svg") {
              target.style.setProperty("fill", "transparent", "important");
              target.style.setProperty("stroke", "transparent", "important");
            }
          });
        }
        if (expected.suppressContainerPaint) {
          element.style.setProperty("background", "none", "important");
          element.style.setProperty("border-color", "transparent", "important");
          element.style.setProperty("outline", "none", "important");
          // A mapped outer shadow is a native effect on the container shape.
          // Only residual (unmapped) layers stay on the backplate; with the
          // base paint removed they cannot duplicate the native shape itself.
          if (expected.suppressOuterShadow) {
            element.style.setProperty("box-shadow", mappedResidualBoxShadow(getComputedStyle(element).boxShadow), "important");
          }
        }
      } else if (kind === "image") {
        if (
          element.namespaceURI === "http://www.w3.org/2000/svg" ||
          element.tagName.toUpperCase() !== "IMG"
        ) {
          element.style.setProperty("visibility", "hidden", "important");
        } else {
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
        }
      } else if (kind === "shape") {
        if (element.namespaceURI === "http://www.w3.org/2000/svg") {
          element.style.setProperty("visibility", "hidden", "important");
        } else {
          // Remove only the editable CSS geometry. Descendant icons and
          // unsupported decoration remain as transparent raster overlay paint.
          // A mapped outer shadow becomes a native effect on the card, so only
          // residual layers stay; an unmapped shadow stays whole on the backplate.
          element.style.setProperty("background", "none", "important");
          element.style.setProperty("border-color", "transparent", "important");
          element.style.setProperty("outline", "none", "important");
          if (expected.suppressOuterShadow) {
            element.style.setProperty("box-shadow", mappedResidualBoxShadow(getComputedStyle(element).boxShadow), "important");
          }
          if (record.observation.shape.endArrow) {
            element.setAttribute("data-presenton-authored-hybrid-suppress-pseudo", "true");
          }
        }
      } else if (kind === "complex" && expected.suppressWholeElement) {
        // Only server-admitted declared chart islands enter this branch. Hide
        // the complete authored chart after identity/content verification so
        // its semantic data can be represented exactly once by native OOXML.
        element.style.setProperty("visibility", "hidden", "important");
      }
      if (!record.pseudo && element === document.body) {
        // Chromium propagates the body background to the canvas. Clearing both
        // roots is required for --default-background-color=00000000 to produce
        // a genuinely transparent residual layer.
        document.documentElement.style.setProperty("background", "transparent", "important");
        document.body.style.setProperty("background", "transparent", "important");
      }
      element.setAttribute("data-presenton-authored-hybrid-suppressed", "true");
      applied.push(id);
    });
    return { applied: applied, rejected: rejected };
  }

  function isolatePaintBand(records) {
    var requested = new Set(paintBandElementIds);
    if (requested.size !== paintBandElementIds.length) {
      throw new Error("authored_hybrid_paint_band_qid_duplicate");
    }
    var byId = new Map(records.map(function (record) {
      return [record.observation.id, record];
    }));
    paintBandElementIds.forEach(function (id) {
      var record = byId.get(id);
      if (!record || record.observation.identityAuthority !== "declared-qid") {
        throw new Error("authored_hybrid_paint_band_qid_mismatch");
      }
    });
    var keptRecords = records.filter(function (record) {
      return requested.has(record.observation.id);
    });
    ensurePseudoSuppressionStyle();
    var observedElements = new Set(records.filter(function (record) {
      return !record.pseudo;
    }).map(function (record) {
      return record.element;
    }));
    function unobservedLayoutOnlyLeaf(element) {
      // Line breaks and empty layout boxes have no independent paint owner.
      // Keep their DOM and dimensions intact: hiding/removing BR changes text
      // layout even though there are no pixels to isolate into a raster band.
      if (
        element.namespaceURI !== "http://www.w3.org/1999/xhtml" ||
        !["BR", "WBR", "DIV", "SPAN"].includes(element.tagName.toUpperCase()) ||
        element.childElementCount !== 0 || /\S/.test(element.textContent || "")
      ) return false;
      var style = getComputedStyle(element);
      return style.filter === "none" && style.backdropFilter === "none" &&
        style.mixBlendMode === "normal" &&
        !paintedBackground(style) && !hasBorder(style) &&
        !hasExternalPaint(style) && !pseudoPainted(element, "::before") &&
        !pseudoPainted(element, "::after");
    }
    function canClearComplexAncestor(record) {
      if (record.pseudo) return false;
      var element = record.element;
      if (!["DIV", "SECTION"].includes(element.tagName.toUpperCase())) return false;
      if (!record.observation.fallbackReasons.every(function (reason) {
        return reason === "complex-chart" || reason === "complex-diagram";
      })) return false;
      if (Array.from(element.childNodes).some(function (node) {
        return node.nodeType === 3 && /\S/.test(node.nodeValue || "");
      })) return false;
      var style = getComputedStyle(element);
      if (
        style.opacity !== "1" || style.filter !== "none" ||
        style.backdropFilter !== "none" || style.mixBlendMode !== "normal" ||
        style.clipPath !== "none" || style.maskImage !== "none" ||
        (style.transform && style.transform !== "none") || hasIndividualTransform(style)
      ) return false;
      return Array.from(element.querySelectorAll("*")).every(function (descendant) {
        if (observedElements.has(descendant)) return true;
        var descendantStyle = getComputedStyle(descendant);
        return descendantStyle.display === "none" ||
          descendantStyle.visibility === "hidden" || descendantStyle.opacity === "0" ||
          unobservedLayoutOnlyLeaf(descendant);
      });
    }
    // Decide ownership before band suppression changes computed visibility.
    var clearableComplexAncestors = new Set(records.filter(function (record) {
      return record.observation.candidateKind === "complex" && canClearComplexAncestor(record);
    }));
    function clearOwnedPaint(record) {
      var element = record.element;
      var kind = record.observation.candidateKind;
      if (record.pseudo) {
        element.setAttribute(pseudoSuppressionAttribute(record.pseudo), "true");
        return;
      }
      if (kind === "text") {
        [element].concat(Array.from(element.querySelectorAll("*"))).forEach(function (target) {
          target.style.setProperty("-webkit-text-fill-color", "transparent", "important");
          target.style.setProperty("text-decoration-color", "transparent", "important");
          target.style.setProperty("-webkit-text-stroke-color", "transparent", "important");
          target.style.setProperty("text-shadow", "none", "important");
        });
      }
      if (kind === "image") element.style.setProperty("opacity", "0", "important");
      element.style.setProperty("background", "none", "important");
      element.style.setProperty("border-color", "transparent", "important");
      element.style.setProperty("outline", "none", "important");
      element.style.setProperty("box-shadow", "none", "important");
      element.setAttribute("data-presenton-authored-hybrid-suppress-pseudo", "true");
    }
    records.forEach(function (record) {
      if (requested.has(record.observation.id)) return;
      var containsKeptPaint = keptRecords.some(function (kept) {
        return record.element !== kept.element && record.element.contains(kept.element);
      });
      if (
        containsKeptPaint &&
        record.observation.candidateKind === "complex" &&
        !clearableComplexAncestors.has(record)
      ) {
        throw new Error("authored_hybrid_paint_band_complex_ancestor");
      }
      if (containsKeptPaint) clearOwnedPaint(record);
      else record.element.style.setProperty("visibility", "hidden", "important");
    });
    if (!requested.has(document.body.getAttribute("data-qid") || "")) {
      document.documentElement.style.setProperty("background", "transparent", "important");
      document.body.style.setProperty("background", "transparent", "important");
    }
    return paintBandElementIds.slice();
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
    marker.hidden = true;
    marker.style.setProperty("display", "none", "important");
    marker.textContent = btoa(binary);
    document.body.appendChild(marker);
    if (completionToken) {
      var completion = document.createElement("meta");
      completion.name = COMPLETION_MARKER_NAME;
      completion.content = completionToken;
      completion.hidden = true;
      document.body.appendChild(completion);
    }
  }

  try {
    await ensureFixedViewport();
    var renderReadiness = await waitForStablePage();
    var localFitObservation = null;
    // Missing policy evidence fails only the policy consumer, never a legacy export.
    try { localFitObservation = observeLocalFitFonts(); } catch (_) {}
    if (captureMode === "fidelity") {
      writeResult({
        ok: true,
        value: {
          viewport: {
            widthPx: window.innerWidth,
            heightPx: window.innerHeight,
            devicePixelRatio: window.devicePixelRatio,
          },
          elements: [],
          localFitObservation: localFitObservation,
          warnings: [],
          appliedPromotedElementIds: [],
          rejectedPromotedElementIds: [],
          renderReadiness: renderReadiness,
        },
      });
      return;
    }

    var declaredIdSet = new Set(declaredElementIds);
    if (declaredIdentity) {
      if (declaredIdSet.size !== declaredElementIds.length) {
        throw new Error("authored_semantic_qid_duplicate");
      }
      declaredElementIds.forEach(function (elementId) {
        if (
          typeof elementId !== "string" ||
          !/^[A-Za-z][A-Za-z0-9._:-]{0,127}$/.test(elementId)
        ) throw new Error("authored_semantic_qid_invalid");
      });
      var observedQids = Array.from(document.querySelectorAll("[data-qid]"))
        .map(function (element) { return element.getAttribute("data-qid") || ""; });
      if (
        observedQids.length !== new Set(observedQids).size ||
        observedQids.some(function (qid) {
          return !/^[A-Za-z][A-Za-z0-9._:-]{0,127}$/.test(qid);
        })
      ) throw new Error("authored_semantic_qid_invalid");
      if (
        observedQids.length !== declaredElementIds.length ||
        observedQids.some(function (qid) { return !declaredIdSet.has(qid); }) ||
        declaredElementIds.some(function (qid) { return !observedQids.includes(qid); })
      ) throw new Error("authored_semantic_qid_mismatch");
    }

    // A direct text node next to a block/flex/grid child is rendered as an
    // anonymous box. querySelectorAll cannot observe that box, so labels next
    // to a nested number badge used to remain absent from the editable layer.
    // Generated flex/grid items (::before/::after) have the same problem: the
    // host bounds start before the generated item, which makes editable text
    // overlap eyebrow bars and chip bullets. Materialize those direct text
    // nodes as neutral spans so Chromium gives us their true laid-out bounds.
    if (!declaredIdentity) Array.from(document.body.querySelectorAll("*")).forEach(function (element) {
      if (["SCRIPT", "STYLE", "TEXTAREA", "NOSCRIPT", "TEMPLATE", "SVG"].includes(element.tagName)) {
        return;
      }
      var display = getComputedStyle(element).display;
      var generatedFlexItem = ["flex", "inline-flex", "grid", "inline-grid"].includes(display) &&
        (pseudoPainted(element, "::before") || pseudoPainted(element, "::after"));
      var hasDirectLineBreak = Array.from(element.children).some(function (child) {
        return child.tagName === "BR";
      });
      if (
        !nestedBlockContent(element) &&
        !hasDecoratedDescendant(element) &&
`;
