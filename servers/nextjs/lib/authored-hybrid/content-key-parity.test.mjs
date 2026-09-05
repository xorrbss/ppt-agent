import assert from "node:assert/strict";
import test from "node:test";

import { AUTHORED_HYBRID_BROWSER_SOURCE } from "./browser-source.ts";
import { promotedElementContentKey } from "./promoted-elements.ts";

/**
 * `promotedElementContentKey` (extracted-side, in `promoted-elements.ts`) and
 * `observationContentKey` (browser-side, hoisted inside the giant IIFE in
 * `browser-source.ts`) must produce byte-identical strings for the same
 * content, because `identityMatches` in the browser compares them directly
 * to decide whether a promoted element may be suppressed from the raster
 * backplate. A prior regression dropped `lineIndex` from the browser side's
 * object literal (JSON.stringify silently omits an `undefined`-valued key),
 * so every promoted text root's content key mismatched and the browser
 * rejected the promotion, forcing the whole slide onto a raster backplate.
 * That happened silently in production from 2026-08-29 onward, and the only
 * tests that exercise `observationContentKey` are the Chrome integration
 * tests, which skip themselves whenever Chrome is unavailable. This test
 * evaluates the exact browser source text (no browser, no Chrome) so the
 * two implementations are pinned together by a plain `node:test` run.
 */

/** Pull one top-level `function name(...) { ... }` out of a larger script by matching braces, without executing anything else in it. */
function extractFunctionSource(scriptSource, functionName) {
  const marker = `function ${functionName}(`;
  const start = scriptSource.indexOf(marker);
  assert.notEqual(start, -1, `${functionName} not found in AUTHORED_HYBRID_BROWSER_SOURCE`);
  const bodyOpen = scriptSource.indexOf("{", start);
  let depth = 0;
  let end = -1;
  for (let i = bodyOpen; i < scriptSource.length; i += 1) {
    const char = scriptSource[i];
    if (char === "{") depth += 1;
    else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        end = i;
        break;
      }
    }
  }
  assert.notEqual(end, -1, `${functionName} has no matching closing brace`);
  return scriptSource.slice(start, end + 1);
}

const observationContentKeySource = extractFunctionSource(
  AUTHORED_HYBRID_BROWSER_SOURCE,
  "observationContentKey"
);
// Evaluate the exact text Chrome runs, same pattern as shadow-effect.test.mjs.
const observationContentKey = new Function(
  `${observationContentKeySource}\nreturn observationContentKey;`
)();

const textStyle = {
  fontFamily: "Arial, sans-serif",
  fontFamilies: ["Arial", "sans-serif"],
  cjkFallbackFamilies: ["Malgun Gothic"],
  fontSizePt: 18,
  fontWeight: 400,
  bold: false,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "123456", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 24, multiple: 1.333, source: "computed" },
  horizontalAlignment: "left",
  verticalAlignment: "middle",
  direction: "ltr",
  wrapMode: "wrap",
};

const containerShapePayload = {
  shape: "round-rectangle",
  fill: { hex: "EEEEFF", alpha: 1 },
  stroke: null,
  strokeWidthPt: 0,
  radiusPt: 8,
};

const runBoundsPx = { x: 10, y: 40, width: 120, height: 22 };
const fragmentPx = { x: 10, y: 40, width: 120, height: 22 };
const containerBoundsPx = { x: 0, y: 20, width: 200, height: 80 };

function extractedElement(lineIndex) {
  return {
    id: "run-a",
    classification: { mode: "raster", candidateKind: "text", reasons: ["external-paint"] },
    text: {
      role: "body",
      plainText: "Korean soft wrap",
      paragraphs: ["Korean soft wrap"],
      style: textStyle,
      runs: [
        {
          text: "Korean soft wrap",
          bounds: { px: runBoundsPx },
          fragments: [{ px: fragmentPx }],
          style: textStyle,
          script: "cjk",
          breakKind: "soft",
          lineIndex,
        },
      ],
      containerShape: {
        bounds: { px: containerBoundsPx },
        shape: containerShapePayload,
      },
    },
  };
}

function observedRecord(lineIndex) {
  return {
    candidateKind: "text",
    text: {
      role: "body",
      plainText: "Korean soft wrap",
      paragraphs: ["Korean soft wrap"],
      style: textStyle,
      runs: [
        {
          text: "Korean soft wrap",
          boundsPx: runBoundsPx,
          fragmentRectsPx: [fragmentPx],
          style: textStyle,
          script: "cjk",
          breakKind: "soft",
          lineIndex,
        },
      ],
      containerShape: {
        boundsPx: containerBoundsPx,
        shape: containerShapePayload,
      },
    },
  };
}

test("promotedElementContentKey and observationContentKey agree on a synthetic text observation", () => {
  const extractedKey = promotedElementContentKey(extractedElement(0));
  const observedKey = observationContentKey(observedRecord(0));
  assert.equal(extractedKey, observedKey);
  assert.match(extractedKey, /"lineIndex":0/);
});

test("a lineIndex change on one side is reflected identically on the other (guards the 2026-08-29 regression)", () => {
  // This is the exact shape of the prior bug: the browser side used to omit
  // `lineIndex` from its object literal entirely (an `undefined`-valued key
  // is dropped by JSON.stringify), while the extracted side always included
  // it. That made every content key mismatch and rejected every promotion.
  // Pinning both sides to the same (defined) lineIndex here means a future
  // regression that reintroduces that asymmetry fails this assertion instead
  // of surfacing only in a skipped Chrome test.
  for (const lineIndex of [0, 1, 3]) {
    assert.equal(
      promotedElementContentKey(extractedElement(lineIndex)),
      observationContentKey(observedRecord(lineIndex)),
      `mismatch at lineIndex=${lineIndex}`
    );
  }
  // A run with no line at all (lineIndex undefined) must also agree: both
  // sides then omit the key the same way.
  assert.equal(
    promotedElementContentKey(extractedElement(undefined)),
    observationContentKey(observedRecord(undefined))
  );
  // Different lineIndex values must not collide with each other.
  assert.notEqual(
    observationContentKey(observedRecord(0)),
    observationContentKey(observedRecord(1))
  );
});

test("promotedElementContentKey and observationContentKey agree on image, shape, and complex observations", () => {
  const imagePayload = {
    src: "data:image/png;base64,AAAA",
    alt: "Chart",
    naturalWidth: 640,
    naturalHeight: 480,
    objectFit: "cover",
    objectPosition: "center",
    crop: { left: 0, top: 0, right: 0, bottom: 0 },
  };
  assert.equal(
    promotedElementContentKey({ id: "img-a", image: imagePayload }),
    observationContentKey({ candidateKind: "image", image: imagePayload })
  );

  const shapePayload = {
    shape: "rectangle",
    fill: { hex: "FFFFFF", alpha: 1 },
    stroke: null,
    strokeWidthPt: 0,
    radiusPt: 0,
  };
  assert.equal(
    promotedElementContentKey({ id: "shape-a", shape: shapePayload }),
    observationContentKey({ candidateKind: "shape", shape: shapePayload })
  );

  const tableEvidence = { rows: 2, columns: 3 };
  assert.equal(
    promotedElementContentKey({
      id: "complex-a",
      classification: {
        mode: "raster",
        candidateKind: "complex",
        reasons: ["complex-table", "complex-chart"],
      },
      tableEvidence,
    }),
    observationContentKey({
      candidateKind: "complex",
      fallbackReasons: ["complex-chart", "complex-table"],
      tableEvidence,
    })
  );
});
