import assert from "node:assert/strict";
import test from "node:test";

import { splitTextByScript } from "./font-policy.ts";
import {
  powerPointTextBoundsForPreflight,
  preflightNativeTypography,
  prepareNativeElements,
  serializePreparedNativeElement,
} from "./native-plan.ts";
import {
  extractAuthoredSlideDom,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";

function bounds(x, y, width, height) {
  return {
    px: { x, y, width, height },
    inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
  };
}

function mixedTextElement() {
  const style = {
    fontFamily: "IBM Plex Mono",
    fontFamilies: ["IBM Plex Mono", "Consolas", "monospace"],
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
  return {
    id: "mixed-1",
    domPath: "body > p:nth-of-type(1)",
    tagName: "p",
    sourceIndex: 1,
    zOrder: 1,
    cssZIndex: null,
    bounds: bounds(80, 70, 260, 42),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "AI 2026 전략",
      paragraphs: ["AI 2026 전략"],
      style,
      runs: [{
        text: "AI 2026 전략",
        bounds: bounds(80, 70, 180, 32),
        fragments: [bounds(80, 70, 180, 32)],
        style,
      }],
      layout: {
        boxBounds: bounds(80, 70, 260, 42),
        contentBounds: bounds(80, 70, 260, 42),
        paintedTextBounds: bounds(80, 70, 180, 32),
        paddingPx: { top: 0, right: 0, bottom: 0, left: 0 },
        borderPx: { top: 0, right: 0, bottom: 0, left: 0 },
        marginPx: { top: 0, right: 0, bottom: 0, left: 0 },
        rowGapPx: 0,
        columnGapPx: 0,
        display: "block",
        flexDirection: null,
        alignItems: "normal",
        justifyContent: "normal",
        textAlignSource: "default",
        lineCount: 1,
        singleLine: true,
        lineRects: [bounds(80, 70, 180, 32)],
        paragraphSpacingPx: { before: 0, after: 0 },
      },
    },
  };
}

test("mixed CJK and Latin text splits at safe script boundaries", () => {
  assert.deepEqual(
    splitTextByScript("AI 2026 전략").map(({ text, script }) => ({ text, script })),
    [
      { text: "AI 2026 ", script: "latin" },
      { text: "전략", script: "cjk" },
    ]
  );
});

test("native typography requires the actual bold embedding face and records blockers", () => {
  const result = preflightNativeTypography([mixedTextElement()], {
    embeddedTypefaceFaces: [
      { typeface: "IBM Plex Mono", face: "bold" },
      { typeface: "Noto Sans KR", face: "regular" },
    ],
  });

  assert.deepEqual(result.rejectedElementIds, ["mixed-1"]);
  assert.ok(result.typographyIssues.some((issue) =>
    issue.code === "embedding-face-missing" &&
      issue.requiredTypeface === "Noto Sans KR" &&
      issue.requiredFace === "bold"
  ));
  assert.deepEqual(result.elements[0].classification.reasons, [
    "typography-embedding-face-missing",
    "typography-font-substitution",
  ]);
});

test("face-complete mixed text promotes with Noto CJK and IBM Plex Mono Latin runs", async () => {
  const faces = [
    { typeface: "IBM Plex Mono", face: "bold" },
    { typeface: "Noto Sans KR", face: "bold" },
  ];
  const result = preflightNativeTypography([mixedTextElement()], {
    embeddedTypefaceFaces: faces,
  });
  assert.deepEqual(result.rejectedElementIds, []);
  assert.deepEqual(
    result.elements[0].text.runs.map((run) => [run.text, run.script, run.style.fontFamily]),
    [
      ["AI 2026 ", "latin", "IBM Plex Mono"],
      ["전략", "cjk", "Noto Sans KR"],
    ]
  );

  const prepared = await prepareNativeElements(result.elements);
  const xml = serializePreparedNativeElement(prepared[0], 3, undefined, {
    embeddedTypefaceFaces: faces,
  });
  assert.match(xml, /typeface="IBM Plex Mono"/);
  assert.match(xml, /typeface="Noto Sans KR"/);
  assert.doesNotMatch(xml, /typeface="Consolas"/);
  assert.match(xml, /<a:noAutofit\/>/);
  assert.doesNotMatch(xml, /<a:normAutofit\/>/);
});

test("reviewed Windows fallback faces admit logical CJK and mono CSS names", async () => {
  const mixed = mixedTextElement();
  const serif = structuredClone(mixedTextElement());
  serif.id = "serif-1";
  serif.text.plainText = "전략과 실행";
  serif.text.paragraphs = ["전략과 실행"];
  serif.text.style.fontFamily = "Noto Serif KR";
  serif.text.style.fontFamilies = ["Noto Serif KR", "Batang", "serif"];
  serif.text.runs = [{
    ...serif.text.runs[0],
    text: "전략과 실행",
    style: serif.text.style,
  }];
  const faces = [
    { typeface: "Consolas", face: "bold" },
    { typeface: "Malgun Gothic", face: "bold" },
    { typeface: "Batang", face: "bold" },
  ];

  const result = preflightNativeTypography([mixed, serif], {
    embeddedTypefaceFaces: faces,
  });
  assert.deepEqual(result.rejectedElementIds, []);
  assert.deepEqual(result.typographyIssues, []);

  const prepared = await prepareNativeElements(result.elements);
  const xml = prepared.map((element, index) =>
    serializePreparedNativeElement(element, index + 3, undefined, {
      embeddedTypefaceFaces: faces,
    })
  ).join("");
  assert.match(xml, /typeface="Consolas"/);
  assert.match(xml, /typeface="Malgun Gothic"/);
  assert.match(xml, /typeface="Batang"/);
  assert.doesNotMatch(xml, /typeface="(?:IBM Plex Mono|Noto Sans KR|Noto Serif KR)"/);
});

test("ancestor backplate paint does not reject an otherwise stable native title", () => {
  const title = mixedTextElement();
  const ancestor = {
    ...title,
    id: "card-parent",
    domPath: "body",
    sourceIndex: 0,
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["external-paint"],
    },
  };
  delete ancestor.text;
  const result = preflightNativeTypography([title], {
    embeddedTypefaceFaces: [
      { typeface: "IBM Plex Mono", face: "bold" },
      { typeface: "Noto Sans KR", face: "bold" },
    ],
    backplateElements: [ancestor],
  });

  assert.deepEqual(result.promotableElementIds, ["mixed-1"]);
  assert.deepEqual(result.typographyIssues, []);
});

test("upper raster paint in a standalone text box's transparent margin stays promotable", () => {
  const title = mixedTextElement();
  const marginPaint = {
    ...title,
    id: "upper-margin-paint",
    domPath: "body > div:nth-of-type(2)",
    sourceIndex: title.sourceIndex + 1,
    bounds: bounds(300, 72, 32, 24),
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["external-paint"],
    },
  };
  delete marginPaint.text;

  const result = preflightNativeTypography([title], {
    embeddedTypefaceFaces: [
      { typeface: "IBM Plex Mono", face: "bold" },
      { typeface: "Noto Sans KR", face: "bold" },
    ],
    backplateElements: [marginPaint],
  });

  assert.deepEqual(result.promotableElementIds, ["mixed-1"]);
  assert.deepEqual(result.typographyIssues, []);
});

test("unrepresented lines and a true upper raster sibling remain exact fallbacks", () => {
  const faces = [
    { typeface: "IBM Plex Mono", face: "bold" },
    { typeface: "Noto Sans KR", face: "bold" },
  ];
  const lineMismatch = mixedTextElement();
  lineMismatch.id = "line-mismatch";
  lineMismatch.text.layout.lineCount = 2;
  lineMismatch.text.layout.singleLine = false;
  lineMismatch.text.layout.lineRects = [
    bounds(80, 70, 180, 20),
    bounds(80, 92, 120, 20),
  ];
  const collision = mixedTextElement();
  collision.id = "collision";
  const upperSibling = {
    ...collision,
    id: "upper-raster",
    domPath: "body > div:nth-of-type(2)",
    sourceIndex: collision.sourceIndex + 1,
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["external-paint"],
    },
  };
  delete upperSibling.text;

  const mismatched = preflightNativeTypography([lineMismatch], {
    embeddedTypefaceFaces: faces,
  });
  assert.deepEqual(mismatched.elements[0].classification.reasons, [
    "typography-line-break-mismatch",
  ]);

  const collided = preflightNativeTypography([collision], {
    embeddedTypefaceFaces: faces,
    backplateElements: [upperSibling],
  });
  assert.deepEqual(collided.elements[0].classification.reasons, [
    "typography-backplate-collision",
  ]);
});

test("browser-measured title height and numeric label width receive PPT guards", () => {
  const title = mixedTextElement();
  title.text.layout.lineCount = 2;
  title.text.layout.singleLine = false;
  title.text.layout.lineRects = [
    bounds(80, 70, 210, 32),
    bounds(80, 103, 190, 32),
  ];
  title.text.runs = [
    { ...title.text.runs[0], text: "AI 2026\n전략" },
  ];
  const titleBounds = powerPointTextBoundsForPreflight(title);
  assert.ok(titleBounds.height > title.bounds.px.height);

  const label = mixedTextElement();
  label.text.role = "numeric";
  label.bounds = bounds(80, 70, 120, 42);
  label.text.layout.boxBounds = bounds(80, 70, 120, 42);
  label.text.layout.contentBounds = bounds(80, 70, 120, 42);
  label.text.layout.lineRects = [bounds(80, 70, 118, 32)];
  const labelBounds = powerPointTextBoundsForPreflight(label);
  assert.ok(labelBounds.width > label.bounds.px.width);
});

test("Chrome records script-aware runs and authored visual line rectangles", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    t.skip("Chrome/Chromium is unavailable");
    return;
  }
  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;left:80px;top:80px;width:210px;margin:0;
      font:700 30px/38px "Noto Sans KR",sans-serif}
  </style></head><body><p>AI 2026 한국어 줄바꿈 측정</p></body></html>`;
  const previousRendererFlag = process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
  process.env.AUTHORED_VNEXT_RENDERER_ENABLED = "true";
  let slide;
  try {
    slide = await extractAuthoredSlideDom(html, {
      chromeExecutable,
      timeoutMs: 20_000,
    });
  } finally {
    if (previousRendererFlag === undefined) {
      delete process.env.AUTHORED_VNEXT_RENDERER_ENABLED;
    } else {
      process.env.AUTHORED_VNEXT_RENDERER_ENABLED = previousRendererFlag;
    }
  }
  const element = slide.elements.find((candidate) => "text" in candidate);
  assert.ok(element);
  assert.deepEqual(
    [...new Set(element.text.runs.filter((run) => run.text !== "\n").map((run) => run.script))].sort(),
    ["cjk", "latin"]
  );
  assert.equal(element.text.layout.lineRects.length, element.text.layout.lineCount);
  assert.ok(element.text.layout.lineCount >= 2);
});
