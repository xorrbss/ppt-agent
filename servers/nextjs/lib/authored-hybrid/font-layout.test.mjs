import assert from "node:assert/strict";
import test from "node:test";

import { mergePowerPointTextLayout } from "./font-layout.ts";

function bounds(x, width) {
  return {
    px: { x, y: 20, width, height: 40 },
    inches: { x: x / 96, y: 20 / 96, width: width / 96, height: 40 / 96 },
  };
}

function style(fontFamily) {
  return {
    fontFamily,
    fontFamilies: [fontFamily, "sans-serif"],
    cjkFallbackFamilies: ["Malgun Gothic"],
    fontSizePt: 24,
    fontWeight: 400,
    bold: false,
    italic: false,
    underline: false,
    strike: false,
    color: { hex: "000000", alpha: 1 },
    letterSpacingPt: 0,
    lineHeight: { points: 30, multiple: 1.25, source: "computed" },
    horizontalAlignment: "left",
    verticalAlignment: "top",
    direction: "ltr",
    wrapMode: "wrap",
  };
}

function textElement(fontFamily, textBounds, plainText = "한국어 줄바꿈") {
  const textStyle = style(fontFamily);
  return {
    id: "h1-0001",
    domPath: "body > p:nth-of-type(1)",
    tagName: "p",
    sourceIndex: 1,
    zOrder: 4,
    cssZIndex: null,
    bounds: textBounds,
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText,
      paragraphs: [plainText],
      style: textStyle,
      runs: [
        {
          text: plainText,
          bounds: textBounds,
          fragments: [textBounds],
          style: textStyle,
        },
      ],
      containerShape: {
        bounds: bounds(10, 300),
        shape: {
          shape: "rectangle",
          fill: { hex: "FFFFFF", alpha: 1 },
          stroke: null,
          strokeWidthPt: 0,
          radiusPt: 0,
        },
      },
    },
  };
}

test("mapped layout replaces only compatible text metrics", () => {
  const source = textElement("Noto Sans KR", bounds(20, 180));
  const layout = {
    ...textElement("Malgun Gothic", bounds(20, 225)),
    zOrder: 99,
    classification: {
      mode: "raster",
      candidateKind: "text",
      reasons: ["decorated-text"],
    },
  };
  const result = mergePowerPointTextLayout([source], [layout]);

  assert.equal(result.appliedTextElements, 1);
  assert.equal(result.elements[0].zOrder, source.zOrder);
  assert.deepEqual(result.elements[0].classification, source.classification);
  assert.equal(result.elements[0].bounds.px.width, 225);
  assert.equal(result.elements[0].text.style.fontFamily, "Noto Sans KR");
  assert.strictEqual(
    result.elements[0].text.containerShape,
    source.text.containerShape
  );
});

test("mapped layout preserves authored visual line breaks and run emphasis", () => {
  const source = textElement(
    "Pretendard",
    bounds(20, 180),
    "첫째 줄\n둘째 줄"
  );
  source.text.runs = [
    {
      ...source.text.runs[0],
      text: "첫째 ",
      style: { ...source.text.runs[0].style, bold: false },
    },
    {
      ...source.text.runs[0],
      text: "줄\n둘째 줄",
      style: { ...source.text.runs[0].style, bold: true },
    },
  ];
  const layout = textElement(
    "Malgun Gothic",
    bounds(20, 225),
    "첫째 줄\n둘째 줄"
  );
  layout.text.runs = [
    {
      ...layout.text.runs[0],
      text: "첫째 줄\n둘째 줄",
    },
  ];

  const result = mergePowerPointTextLayout([source], [layout]);

  assert.deepEqual(
    result.elements[0].text.runs.map((run) => run.text),
    ["첫째 ", "줄\n둘째 줄"]
  );
  assert.deepEqual(
    result.elements[0].text.runs.map((run) => run.style.bold),
    [false, true]
  );
  assert.ok(
    result.elements[0].text.runs.every(
      (run) => run.style.fontFamily === "Pretendard"
    )
  );
});

test("embedded typeface retains authored geometry after packaging succeeds", () => {
  const source = textElement("Noto Sans KR", bounds(20, 180));
  const layout = textElement("Malgun Gothic", bounds(20, 225));

  const result = mergePowerPointTextLayout([source], [layout], {
    embeddedTypefaceFamilies: ['  "nOtO sAnS Kr"  '],
  });

  assert.equal(result.appliedTextElements, 0);
  assert.strictEqual(result.elements[0], source);
  assert.equal(result.elements[0].bounds.px.width, 180);
  assert.equal(result.elements[0].text.style.fontFamily, "Noto Sans KR");
});

test("identity drift retains source metrics and non-text elements", () => {
  const source = textElement("Noto Sans KR", bounds(20, 180));
  const drifted = textElement(
    "Malgun Gothic",
    bounds(20, 225),
    "다른 문자열"
  );
  const shape = {
    id: "h1-0002",
    domPath: "body > div:nth-of-type(1)",
    tagName: "div",
    sourceIndex: 2,
    zOrder: 5,
    cssZIndex: null,
    bounds: bounds(50, 100),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "rectangle",
      fill: { hex: "FFFFFF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const result = mergePowerPointTextLayout([source, shape], [drifted]);

  assert.equal(result.appliedTextElements, 0);
  assert.strictEqual(result.elements[0], source);
  assert.strictEqual(result.elements[1], shape);
});
