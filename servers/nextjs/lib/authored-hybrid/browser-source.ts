import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_01 } from "./browser-source-parts/part01.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_02 } from "./browser-source-parts/part02.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_03 } from "./browser-source-parts/part03.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_04 } from "./browser-source-parts/part04.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_05 } from "./browser-source-parts/part05.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_06 } from "./browser-source-parts/part06.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_07 } from "./browser-source-parts/part07.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_08 } from "./browser-source-parts/part08.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_09 } from "./browser-source-parts/part09.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_10 } from "./browser-source-parts/part10.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_11 } from "./browser-source-parts/part11.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_12 } from "./browser-source-parts/part12.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_13 } from "./browser-source-parts/part13.ts";
import { AUTHORED_HYBRID_BROWSER_SOURCE_PART_14 } from "./browser-source-parts/part14.ts";
/**
 * Plain JavaScript injected into a temporary authored HTML copy. Keep this
 * source self-contained: Chrome executes it directly, without a bundler or a
 * dependency on the generated presentation-export runtime.
 */
import { AUTHORED_HYBRID_CSS_PAINT_BROWSER_SOURCE } from "./css-paint-browser.ts";
import { AUTHORED_HYBRID_SHADOW_BROWSER_SOURCE } from "./shadow-effect-browser.ts";
import { LOCAL_FIT_BROWSER_SOURCE } from "./local-fit-browser.ts";

export const AUTHORED_HYBRID_RESULT_MARKER_ID =
  "__presenton_authored_hybrid_result_v1__";

export const AUTHORED_HYBRID_COMPLETION_MARKER_NAME =
  "__presenton_authored_hybrid_complete_v1__";

/**
 * Mirrors the inline viewport check spliced into the template below. Chrome
 * executes the template directly as injected source (no bundler, no TS
 * import), so the check is duplicated in plain JS there; keep both in sync.
 */
export function authoredHybridViewportMatchesExpected(
  actualWidth: number,
  actualHeight: number,
  expectedViewport: unknown
): boolean {
  if (
    !expectedViewport ||
    typeof expectedViewport !== "object" ||
    typeof (expectedViewport as { width?: unknown }).width !== "number" ||
    typeof (expectedViewport as { height?: unknown }).height !== "number"
  ) {
    return false;
  }
  const expected = expectedViewport as { width: number; height: number };
  return actualWidth === expected.width && actualHeight === expected.height;
}

// The box-shadow mapping helpers are spliced in below; they are hoisted
// function declarations that share this IIFE scope.
export const AUTHORED_HYBRID_BROWSER_SOURCE = String.raw`
(async function () {
  "use strict";
  ${LOCAL_FIT_BROWSER_SOURCE}

  var MARKER_ID = "__presenton_authored_hybrid_result_v1__";
  var COMPLETION_MARKER_NAME = "__presenton_authored_hybrid_complete_v1__";
  var ELEMENT_ATTRIBUTE = "data-presenton-authored-hybrid-id";
  var ISOLATED_INLINE_ATTRIBUTE = "data-presenton-authored-layout-isolated-inline";
  var sourceFitV2 = Boolean(document.querySelector('[data-source-fit-policy="composer-source-fit/v2"]'));
  function authoredHybridViewportMatchesExpected(actualWidth, actualHeight, expectedViewport) {
    return Boolean(expectedViewport) &&
      typeof expectedViewport.width === "number" &&
      typeof expectedViewport.height === "number" &&
      actualWidth === expectedViewport.width &&
      actualHeight === expectedViewport.height;
  }
  var config = window.__PRESENTON_AUTHORED_HYBRID_CONFIG__ || {};
  var WIDTH = window.innerWidth;
  var HEIGHT = window.innerHeight;
  if (!authoredHybridViewportMatchesExpected(WIDTH, HEIGHT, config.expectedViewport)) {
    throw new Error("Unsupported authored canvas viewport " + WIDTH + "x" + HEIGHT + ".");
  }
  var captureMode = ["observation", "hybrid-backplate", "hybrid-paint-band", "fidelity"].includes(
    config.captureMode
  ) ? config.captureMode : "observation";
  var declaredElementIds = Array.isArray(config.declaredElementIds)
    ? config.declaredElementIds
    : [];
  var declaredIdentity = declaredElementIds.length > 0;
  var completionToken = typeof config.completionToken === "string" ? config.completionToken : "";
  var promotedElements = Array.isArray(config.promotedElements)
    ? config.promotedElements
    : [];
  var paintBandElementIds = Array.isArray(config.paintBandElementIds)
    ? config.paintBandElementIds
    : [];
  // Chromium deviceScaleFactor from capture-scale.ts; 1 for legacy direct callers that omit it.
  var CAPTURE_SCALE = typeof config.captureScale === "number" && config.captureScale > 0 ? config.captureScale : 1;
  var fontFallbackPolicy = new Map(
    (Array.isArray(config.fontFallbackPolicy) ? config.fontFallbackPolicy : [])
      .filter(function (rule) {
        return rule && typeof rule.authored === "string" &&
          typeof rule.powerpoint === "string";
      })
      .map(function (rule) {
        return [rule.authored.trim().toLowerCase(), rule.powerpoint.trim()];
      })
  );
${AUTHORED_HYBRID_SHADOW_BROWSER_SOURCE}
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
${AUTHORED_HYBRID_CSS_PAINT_BROWSER_SOURCE}
${AUTHORED_HYBRID_BROWSER_SOURCE_PART_01}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_02}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_03}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_04}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_05}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_06}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_07}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_08}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_09}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_10}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_11}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_12}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_13}${AUTHORED_HYBRID_BROWSER_SOURCE_PART_14}`;
