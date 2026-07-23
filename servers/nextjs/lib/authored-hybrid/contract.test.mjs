import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORED_HYBRID_RESULT_MARKER_ID,
} from "./browser-source.ts";
import {
  instrumentAuthoredHtml,
  parseAuthoredHybridDomDump,
} from "./html-instrumentation.ts";

import {
  assertAuthoredHybridSlide,
  authoredHtmlSha256,
  buildAuthoredHybridSlide,
  classifyAuthoredHybridCandidate,
  normalizeFallbackReasons,
  pxToInches,
  pxToPoints,
  rectPxToBounds,
  serializeAuthoredHybridSlide,
} from "./index.ts";

const TEXT_STYLE = {
  fontFamily: "Arial",
  fontFamilies: ["Arial", "sans-serif"],
  cjkFallbackFamilies: ["Noto Sans KR", "Malgun Gothic"],
  fontSizePt: 24,
  fontWeight: 700,
  bold: true,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "112233", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 30, multiple: 1.25, source: "computed" },
  horizontalAlignment: "left",
  verticalAlignment: "top",
  direction: "ltr",
  wrapMode: "wrap",
};

test("1280x720 CSS pixels map to the fixed wide-slide contract", () => {
  assert.equal(pxToInches(1280), 13.333333);
  assert.equal(pxToInches(720), 7.5);
  assert.equal(pxToPoints(1), 0.75);
  assert.equal(pxToPoints(16), 12);
  assert.deepEqual(rectPxToBounds({ x: 96, y: 48, width: 192, height: 96 }), {
    px: { x: 96, y: 48, width: 192, height: 96 },
    inches: { x: 1, y: 0.5, width: 2, height: 1 },
  });
});

test("classifier is deterministic, conservative, and isolated per element", () => {
  assert.deepEqual(
    classifyAuthoredHybridCandidate({
      candidateKind: "text",
      textRole: "body",
      hasPayload: true,
      fallbackReasons: [],
    }),
    { mode: "native", kind: "text", confidence: "safe" }
  );
  assert.deepEqual(
    classifyAuthoredHybridCandidate({
      candidateKind: "image",
      hasPayload: true,
      fallbackReasons: ["filter", "clip-path", "filter"],
    }),
    {
      mode: "raster",
      candidateKind: "image",
      reasons: ["clip-path", "filter"],
    }
  );
  assert.deepEqual(
    normalizeFallbackReasons([
      "occluded",
      "external-paint",
      "mask",
      "occluded",
    ]),
    ["mask", "external-paint", "occluded"]
  );
  assert.deepEqual(
    classifyAuthoredHybridCandidate({
      candidateKind: "text",
      textRole: "unsupported",
      hasPayload: true,
      fallbackReasons: [],
    }),
    {
      mode: "raster",
      candidateKind: "text",
      reasons: ["unsupported-role"],
    }
  );
});

test("browser observations become a versioned JSON-safe H2 contract", () => {
  const html = "<!doctype html><p>분기 <strong>성과</strong></p>";
  const observation = {
    viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: 1 },
    appliedPromotedElementIds: [],
    rejectedPromotedElementIds: [],
    warnings: ["font substituted", "font substituted"],
    elements: [
      {
        id: "h1-0002",
        domPath: "body > div:nth-of-type(1)",
        tagName: "div",
        sourceIndex: 4,
        cssZIndex: 2,
        boundsPx: { x: 50, y: 300, width: 100, height: 40 },
        rotationDeg: 0,
        opacity: 1,
        candidateKind: "shape",
        fallbackReasons: ["filter"],
        shape: {
          shape: "rectangle",
          fill: { hex: "445566", alpha: 1 },
          stroke: null,
          strokeWidthPt: 0,
          radiusPt: 0,
        },
      },
      {
        id: "h1-0001",
        domPath: "body > p:nth-of-type(1)",
        tagName: "p",
        sourceIndex: 2,
        cssZIndex: null,
        boundsPx: { x: 96, y: 48, width: 384, height: 64 },
        rotationDeg: 7.5,
        opacity: 0.8,
        candidateKind: "text",
        fallbackReasons: [],
        text: {
          role: "title",
          plainText: "분기 성과",
          paragraphs: ["분기 성과"],
          style: TEXT_STYLE,
          runs: [
            {
              text: "분기 ",
              boundsPx: { x: 96, y: 48, width: 80, height: 32 },
              fragmentRectsPx: [{ x: 96, y: 48, width: 80, height: 32 }],
              style: TEXT_STYLE,
            },
            {
              text: "성과",
              boundsPx: { x: 176, y: 48, width: 72, height: 32 },
              fragmentRectsPx: [{ x: 176, y: 48, width: 72, height: 32 }],
              style: { ...TEXT_STYLE, color: { hex: "0066CC", alpha: 1 } },
            },
          ],
        },
      },
    ],
  };

  const slide = buildAuthoredHybridSlide(html, observation, undefined);
  assert.equal(slide.schemaVersion, "presenton.authored-hybrid/v1");
  assert.equal(slide.source.htmlSha256, authoredHtmlSha256(html));
  assert.equal(slide.source.baseUrl, null);
  assert.deepEqual(slide.elements.map((element) => element.id), ["h1-0001", "h1-0002"]);
  assert.deepEqual(slide.elements.map((element) => element.zOrder), [0, 1]);
  assert.equal(slide.elements[0].classification.mode, "native");
  assert.equal(slide.elements[0].bounds.inches.x, 1);
  assert.equal(slide.elements[0].rotationDeg, 7.5);
  assert.equal(slide.elements[0].text.runs.length, 2);
  assert.equal(slide.elements[0].text.runs[1].style.color.hex, "0066CC");
  assert.equal(slide.elements[1].classification.mode, "raster");
  assert.deepEqual(slide.backplate.eligibleElementIds, ["h1-0001"]);
  assert.deepEqual(slide.backplate.rasterElementIds, ["h1-0002"]);
  assert.deepEqual(slide.warnings, ["font substituted"]);
  assert.doesNotThrow(() => assertAuthoredHybridSlide(slide));
  assert.deepEqual(JSON.parse(serializeAuthoredHybridSlide(slide)), slide);

  const invalid = structuredClone(slide);
  invalid.elements[0].opacity = 1.5;
  assert.throws(() => assertAuthoredHybridSlide(invalid), /invalid opacity/);

  const invalidRole = structuredClone(slide);
  invalidRole.elements[0].text.role = "hero";
  assert.throws(() => assertAuthoredHybridSlide(invalidRole), /text\.role is invalid/);

  const invalidColor = structuredClone(slide);
  invalidColor.elements[0].text.style.color.hex = "#123";
  assert.throws(() => assertAuthoredHybridSlide(invalidColor), /uppercase sRGB/);

  const invalidWrapMode = structuredClone(slide);
  invalidWrapMode.elements[0].text.style.wrapMode = "overflow";
  assert.throws(() => assertAuthoredHybridSlide(invalidWrapMode), /wrapMode is invalid/);

  const invalidUnits = structuredClone(slide);
  invalidUnits.elements[0].bounds.inches.x = 99;
  assert.throws(() => assertAuthoredHybridSlide(invalidUnits), /px\/96 inch/);

  const freeform = structuredClone(slide);
  freeform.elements[1].shape = {
    shape: "freeform",
    fill: null,
    stroke: { hex: "2878D8", alpha: 1 },
    strokeWidthPt: 2,
    radiusPt: 0,
    points: [
      { x: 0, y: 0.5 },
      { x: 0.45, y: 0.5 },
      { x: 0.65, y: 1 },
      { x: 1, y: 1 },
    ],
    closed: false,
  };
  assert.doesNotThrow(() => assertAuthoredHybridSlide(freeform));

  const invalidFreeform = structuredClone(freeform);
  invalidFreeform.elements[1].shape.points[2].x = 1.2;
  assert.throws(() => assertAuthoredHybridSlide(invalidFreeform), /points\[2\]\.x/);
});

test("raster-classified text keeps an editable payload and suppression eligibility", () => {
  const html = "<!doctype html><p>decorated but editable</p>";
  const slide = buildAuthoredHybridSlide(
    html,
    {
      viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: 1 },
      appliedPromotedElementIds: [],
      rejectedPromotedElementIds: [],
      warnings: [],
      elements: [
        {
          id: "h1-0001",
          domPath: "body > p:nth-of-type(1)",
          tagName: "p",
          sourceIndex: 0,
          cssZIndex: null,
          boundsPx: { x: 40, y: 40, width: 320, height: 48 },
          rotationDeg: 0,
          opacity: 1,
          candidateKind: "text",
          fallbackReasons: ["decorated-text", "pseudo-element"],
          text: {
            role: "body",
            plainText: "decorated but editable",
            paragraphs: ["decorated but editable"],
            style: TEXT_STYLE,
            runs: [],
          },
        },
      ],
    },
    undefined
  );

  assert.equal(slide.elements[0].classification.mode, "raster");
  assert.equal(slide.elements[0].text.plainText, "decorated but editable");
  assert.deepEqual(slide.backplate.eligibleElementIds, ["h1-0001"]);
  assert.deepEqual(slide.backplate.rasterElementIds, ["h1-0001"]);
  assert.doesNotThrow(() => assertAuthoredHybridSlide(slide));
});

test("contract construction rejects a viewport that would corrupt geometry", () => {
  assert.throws(
    () =>
      buildAuthoredHybridSlide(
        "<p>x</p>",
        {
          viewport: { widthPx: 1024, heightPx: 768, devicePixelRatio: 1 },
          appliedPromotedElementIds: [],
          rejectedPromotedElementIds: [],
          warnings: [],
          elements: [],
        },
        undefined
      ),
    /expected 1280x720 viewport/
  );
});

test("HTML instrumentation fixes the asset context and neutralises only temporary CSP", () => {
  const instrumented = instrumentAuthoredHtml(
    '<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="script-src none"><base href="https://old.invalid/"></head><body><p>x</p></body></html>',
    {
      baseUrl: "https://example.com/decks/",
      promotedElements: [
        {
          id: "h1-0001",
          domPath: "body > p:nth-of-type(1)",
          tagName: "p",
          sourceIndex: 0,
          candidateKind: "text",
          boundsPx: { x: 0, y: 0, width: 1, height: 1 },
          rotationDeg: 0,
          opacity: 1,
          contentKey: "text:x",
        },
      ],
    }
  );

  assert.doesNotMatch(instrumented, /content-security-policy/i);
  assert.equal(instrumented.match(/<base\b/gi)?.length, 1);
  assert.match(instrumented, /<base href="https:\/\/example\.com\/decks\/">/);
  assert.match(
    instrumented,
    /__PRESENTON_AUTHORED_HYBRID_CONFIG__=\{"promotedElements":\[/
  );
  assert.ok(instrumented.indexOf("<p>x</p>") < instrumented.indexOf("</body>"));
});

test("DOM dump parser selects the actual result element, not analyser source text", () => {
  const value = {
    viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: 1 },
    elements: [],
    warnings: [],
    appliedPromotedElementIds: [],
    rejectedPromotedElementIds: [],
  };
  const encoded = Buffer.from(
    JSON.stringify({ ok: true, value }),
    "utf8"
  ).toString("base64");
  const serializedDom = [
    "<html><body>",
    `<script>var MARKER_ID = "${AUTHORED_HYBRID_RESULT_MARKER_ID}";</script>`,
    `<script type="application/json" id="${AUTHORED_HYBRID_RESULT_MARKER_ID}">${encoded}</script>`,
    "</body></html>",
  ].join("");

  assert.deepEqual(parseAuthoredHybridDomDump(serializedDom), value);
});
