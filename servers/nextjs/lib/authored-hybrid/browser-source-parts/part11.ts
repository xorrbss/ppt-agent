export const AUTHORED_HYBRID_BROWSER_SOURCE_PART_11 = String.raw`    functions.forEach(function (item) {
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
      if (
        foreign.contains(element) &&
        isDecomposableSvgDropShadow(
          element,
          foreign,
          getComputedStyle(foreign).filter
        )
      ) return false;
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

  async function waitForStablePage() {
    var readinessStarted = performance.now();
    var mutationCount = 0;
    var observer = typeof MutationObserver === "function"
      ? new MutationObserver(function (entries) { mutationCount += entries.length; })
      : null;
    if (observer) {
      observer.observe(document.documentElement, {
        attributes: true,
        childList: true,
        characterData: true,
        subtree: true,
      });
    }
    var timeoutToken;
    var timeout = new Promise(function (_resolve, reject) {
      timeoutToken = setTimeout(function () {
        reject(new Error("authored_render_readiness_timeout"));
      }, 4000);
    });
    var images = Array.from(document.images);
    var decodedImageCount = 0;
    var unresolvedResources = [];
    async function decodeImage(image, index) {
      try {
        if (!image.complete) {
          await new Promise(function (resolve, reject) {
            image.addEventListener("load", resolve, { once: true });
            image.addEventListener("error", reject, { once: true });
          });
        }
        if (typeof image.decode === "function") await image.decode();
        if (image.naturalWidth <= 0 || image.naturalHeight <= 0) {
          throw new Error("empty-image");
        }
        decodedImageCount += 1;
      } catch (_error) {
        unresolvedResources.push("image:" + index);
      }
    }
    try {
      await Promise.race([
        Promise.all([
          document.fonts && document.fonts.ready
            ? document.fonts.ready
            : Promise.resolve(),
          Promise.all(images.map(decodeImage)),
        ]),
        timeout,
      ]);
      if (document.fonts && document.fonts.status !== "loaded") {
        unresolvedResources.push("fonts:pending");
      }
      if (
        typeof document.getAnimations === "function" &&
        document.getAnimations().some(function (animation) {
          return animation.playState === "running" || animation.playState === "pending";
        })
      ) unresolvedResources.push("animation:active");
      if (unresolvedResources.length) {
        throw new Error(
          "authored_render_resources_unresolved:" + unresolvedResources.join(",")
        );
      }

      var stableSince = performance.now();
      var previousMutationCount = mutationCount;
      while (performance.now() - stableSince < 100) {
        await new Promise(function (resolve) { setTimeout(resolve, 20); });
        if (mutationCount !== previousMutationCount) {
          previousMutationCount = mutationCount;
          stableSince = performance.now();
        }
        if (performance.now() - readinessStarted > 3800) {
          throw new Error("authored_render_mutation_window_unstable");
        }
      }
      await waitForTwoPaints();
      return {
        schemaVersion: "browser-render-readiness/v1",
        status: "ready",
        fontStatus: document.fonts ? "loaded" : "unsupported",
        imageCount: images.length,
        decodedImageCount: decodedImageCount,
        unresolvedResources: [],
        mutationFreeWindowMs: 100,
        continuousPaintEpochs: 2,
        captureEpoch: Math.round(performance.now() * 1000),
        captureMode: captureMode,
      };
    } finally {
      clearTimeout(timeoutToken);
      if (observer) observer.disconnect();
    }
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
            script: run.script,
            breakKind: run.breakKind,
            // The extractor's expected key carries the line index; omitting
            // it here rejected every promoted text root at backplate capture.
            lineIndex: run.lineIndex,
          };
        }),
        containerShape: observation.text.containerShape,
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
    if (observation.candidateKind === "complex") {
      return "complex:" + JSON.stringify({
        fallbackReasons: observation.fallbackReasons.slice().sort(),
        tableEvidence: observation.tableEvidence || null,
      });
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
    var declaredQidMatches =
      expected.identityAuthority === "declared-qid" &&
      observation.identityAuthority === "declared-qid" &&
      record.element.getAttribute("data-qid") === expected.id;
    var compatibilityIdentityMatches =
      expected.identityAuthority !== "declared-qid" &&
      observation.domPath === expected.domPath &&
      observation.sourceIndex === expected.sourceIndex;
    return (
      (["text", "shape", "complex"].includes(expected.candidateKind) || observation.fallbackReasons.length === 0) &&
      observation.id === expected.id &&
      (declaredQidMatches || compatibilityIdentityMatches) &&
      observation.tagName === expected.tagName &&
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
    ensurePseudoSuppressionStyle();
    promotedElements.forEach(function (expected) {
      var id = expected && typeof expected.id === "string" ? expected.id : "";
      var record = byId.get(id);
      if (!record || !identityMatches(record, expected)) {
        rejected.push(id);
        return;
`;
