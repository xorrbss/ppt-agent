import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORED_HYBRID_BROWSER_SOURCE,
  authoredHybridViewportMatchesExpected,
} from "./browser-source.ts";
import { instrumentAuthoredHtml } from "./html-instrumentation.ts";

/**
 * The injected analyser throws unless window.innerWidth/Height exactly match
 * `config.expectedViewport`, which the runner derives from the deck's
 * AuthoredCanvasContract (see extractor.ts/chrome-runner.ts) instead of a
 * hardcoded 1280x720/720x1280 allow-list. This pins three things together:
 * the exported TS predicate, the exact plain-JS twin Chrome actually
 * executes (extracted from AUTHORED_HYBRID_BROWSER_SOURCE, same pattern as
 * content-key-parity.test.mjs), and the config the HTML instrumentation step
 * builds for a given canvas.
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

const browserSource = extractFunctionSource(
  AUTHORED_HYBRID_BROWSER_SOURCE,
  "authoredHybridViewportMatchesExpected"
);
// Evaluate the exact text Chrome runs, same pattern as shadow-effect.test.mjs.
const browserAuthoredHybridViewportMatchesExpected = new Function(
  `${browserSource}\nreturn authoredHybridViewportMatchesExpected;`
)();

const SUPPORTED_SIZES = [
  { width: 1280, height: 720 },
  { width: 720, height: 1280 },
  { width: 720, height: 1040 },
  { width: 1188, height: 840 },
  { width: 840, height: 1188 },
  { width: 720, height: 720 },
  { width: 1680, height: 720 },
];

test("both the TS predicate and its browser-injected twin accept every supported canvas size", () => {
  for (const size of SUPPORTED_SIZES) {
    const expectedViewport = { width: size.width, height: size.height };
    assert.equal(
      authoredHybridViewportMatchesExpected(size.width, size.height, expectedViewport),
      true,
      `${size.width}x${size.height} should match itself (TS)`
    );
    assert.equal(
      browserAuthoredHybridViewportMatchesExpected(size.width, size.height, expectedViewport),
      true,
      `${size.width}x${size.height} should match itself (browser)`
    );
  }
});

test("both implementations reject a viewport that does not equal the expected size", () => {
  const mismatches = [
    [1280, 720, { width: 720, height: 1280 }],
    [720, 1040, { width: 1188, height: 840 }],
    [1188, 840, { width: 840, height: 1188 }],
    [1680, 720, { width: 720, height: 1680 }],
  ];
  for (const [actualWidth, actualHeight, expectedViewport] of mismatches) {
    assert.equal(
      authoredHybridViewportMatchesExpected(actualWidth, actualHeight, expectedViewport),
      false
    );
    assert.equal(
      browserAuthoredHybridViewportMatchesExpected(actualWidth, actualHeight, expectedViewport),
      false
    );
  }
});

test("both implementations reject a missing or malformed expected size", () => {
  const malformed = [
    undefined,
    null,
    {},
    { width: 1280 },
    { height: 720 },
    { width: "1280", height: 720 },
    { width: 1280, height: "720" },
  ];
  for (const expectedViewport of malformed) {
    assert.equal(
      authoredHybridViewportMatchesExpected(1280, 720, expectedViewport),
      false
    );
    assert.equal(
      browserAuthoredHybridViewportMatchesExpected(1280, 720, expectedViewport),
      false
    );
  }
});

function parseInstrumentedConfig(instrumented) {
  const match = instrumented.match(
    /__PRESENTON_AUTHORED_HYBRID_CONFIG__=(\{.*?\});<\/script>/
  );
  assert.ok(match, "instrumented HTML must embed the authored hybrid config");
  return JSON.parse(match[1]);
}

test("instrumentAuthoredHtml defaults expectedViewport to the legacy 16:9 canvas", () => {
  const instrumented = instrumentAuthoredHtml("<p>x</p>");
  const config = parseInstrumentedConfig(instrumented);
  assert.deepEqual(config.expectedViewport, { width: 1280, height: 720 });
});

test("instrumentAuthoredHtml carries the caller's canvas as expectedViewport", () => {
  const cases = [
    { widthPx: 1280, heightPx: 720 },
    { widthPx: 720, heightPx: 1280 },
    { widthPx: 720, heightPx: 1040 },
    { widthPx: 1188, heightPx: 840 },
    { widthPx: 840, heightPx: 1188 },
    { widthPx: 720, heightPx: 720 },
    { widthPx: 1680, heightPx: 720 },
  ];
  for (const canvas of cases) {
    const instrumented = instrumentAuthoredHtml("<p>x</p>", { canvas });
    const config = parseInstrumentedConfig(instrumented);
    assert.deepEqual(config.expectedViewport, {
      width: canvas.widthPx,
      height: canvas.heightPx,
    });
  }
});
