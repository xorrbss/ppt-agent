import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

import { prepareNativeElements } from "./native-plan.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
</Types>`;
const PRESENTATION = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>`;
const SLIDE = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:pic><p:nvPicPr><p:cNvPr id="2" name="historical full-slide image"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr></p:pic></p:spTree></p:cSld></p:sld>`;
const RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`;

const bounds = (x, y, width, height) => ({
  px: { x, y, width, height },
  inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
});
const textStyle = {
  fontFamily: "Malgun Gothic",
  fontFamilies: ["Malgun Gothic", "sans-serif"],
  cjkFallbackFamilies: ["Malgun Gothic", "Noto Sans CJK KR"],
  fontSizePt: 28,
  fontWeight: 700,
  bold: true,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "112233", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 34, multiple: 1.2, source: "computed" },
  horizontalAlignment: "left",
  verticalAlignment: "top",
  direction: "ltr",
};

function skeleton(width = "12192000") {
  return writePptxArchive(
    new Map([
      ["[Content_Types].xml", Buffer.from(CONTENT_TYPES)],
      ["ppt/presentation.xml", Buffer.from(PRESENTATION.replace("12192000", width))],
      ["ppt/slides/slide1.xml", Buffer.from(SLIDE)],
      ["ppt/slides/_rels/slide1.xml.rels", Buffer.from(RELS)],
    ])
  );
}

test("assembler puts a transparent backplate below editable native layers", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } },
  })
    .png()
    .toBuffer();
  const text = {
    id: "editable-cjk",
    domPath: "body > h1",
    tagName: "h1",
    sourceIndex: 0,
    zOrder: 3,
    cssZIndex: 3,
    bounds: bounds(80, 60, 600, 100),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "편집 가능한 서울",
      paragraphs: ["편집 가능한 서울"],
      style: textStyle,
      runs: [],
    },
  };
  const shape = {
    id: "native-shape",
    domPath: "body > div",
    tagName: "div",
    sourceIndex: 1,
    zOrder: 2,
    cssZIndex: 2,
    bounds: bounds(64, 210, 300, 120),
    rotationDeg: 12,
    opacity: 1,
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "ellipse",
      fill: { hex: "22AA77", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([text, shape]);
  const output = assembleAuthoredHybridPptx(skeleton(), [
    { slideNumber: 1, backplatePng: backplate, elements: prepared },
  ]);
  const entries = readPptxArchive(output);
  const slideXml = entries.get("ppt/slides/slide1.xml").toString("utf8");
  const relsXml = entries
    .get("ppt/slides/_rels/slide1.xml.rels")
    .toString("utf8");

  assert.doesNotMatch(slideXml, /historical full-slide image/);
  assert.match(slideXml, /Presenton hybrid backplate/);
  assert.match(slideXml, /<a:t xml:space="preserve">편집 가능한 서울<\/a:t>/);
  assert.match(slideXml, /prst="ellipse"/);
  assert.ok(
    slideXml.indexOf("Presenton hybrid backplate") <
      slideXml.indexOf("Presenton hybrid shape native-shape")
  );
  assert.ok(
    slideXml.indexOf("Presenton hybrid shape native-shape") <
      slideXml.indexOf("Presenton hybrid text editable-cjk")
  );
  assert.match(relsXml, /hybrid-s1-backplate-[a-f0-9]{16}\.png/);
  assert.ok([...entries.keys()].some((name) => /^ppt\/media\/hybrid-s1-backplate-/.test(name)));
  assert.match(entries.get("[Content_Types].xml").toString("utf8"), /Extension="png"/);
});

test("assembler fails closed for an incompatible slide size or malformed PNG", async () => {
  const png = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  assert.throws(
    () =>
      assembleAuthoredHybridPptx(skeleton("100"), [
        { slideNumber: 1, backplatePng: png, elements: [] },
      ]),
    /fixed 16:9/
  );
  assert.throws(
    () =>
      assembleAuthoredHybridPptx(skeleton(), [
        { slideNumber: 1, backplatePng: Buffer.from("not-png"), elements: [] },
      ]),
    /1280x720 PNG/
  );
});
