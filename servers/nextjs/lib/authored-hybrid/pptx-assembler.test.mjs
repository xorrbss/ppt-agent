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
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:pic><p:nvPicPr><p:cNvPr id="2" name="historical full-slide image"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic></p:spTree></p:cSld></p:sld>`;
const RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/></Relationships>`;

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

function skeleton(
  width = "12192000",
  sharedMediaReference = false,
  aliasedSlideReference = false
) {
  const relationshipReference =
    aliasedSlideReference === "numeric-entity" ? "rId&#50;" : "rId2";
  const slide = aliasedSlideReference
    ? SLIDE.replace(
        "<p:cSld>",
        aliasedSlideReference === "numeric-entity"
          ? `<p:cSld><p:bg><p:bgPr><a:blipFill><a:blip r:embed="${relationshipReference}"/></a:blipFill></p:bgPr></p:bg>`
          : `<p:cSld><p:bg><p:bgPr><a:blipFill><a:blip rel:embed="${relationshipReference}"/></a:blipFill></p:bgPr></p:bg>`
      ).replace(
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"',
        aliasedSlideReference === "numeric-entity"
          ? 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
          : 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:rel="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
      )
    : SLIDE;
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from(CONTENT_TYPES)],
    ["ppt/presentation.xml", Buffer.from(PRESENTATION.replace("12192000", width))],
    ["ppt/slides/slide1.xml", Buffer.from(slide)],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from(RELS)],
    ["ppt/media/image1.png", Buffer.from("historical full-slide raster")],
  ]);
  if (sharedMediaReference) {
    entries.set("ppt/slides/slide2.xml", Buffer.from(SLIDE));
    const sharedRelationships =
      sharedMediaReference === "explicit-close"
        ? RELS.replace(
            /(<Relationship Id="rId2"[^>]*)\/>/,
            "$1></Relationship>"
          )
        : sharedMediaReference === "numeric-entity"
          ? RELS.replace("image1.png", "image&#49;.png")
        : sharedMediaReference === "uppercase-target"
          ? RELS.replace("image1.png", "IMAGE1.PNG")
        : RELS;
    const sharedRelsPath =
      sharedMediaReference === "uppercase-rels-path"
        ? "ppt/slides/_RELS/slide2.xml.RELS"
        : "ppt/slides/_rels/slide2.xml.rels";
    entries.set(sharedRelsPath, Buffer.from(sharedRelationships));
  }
  return writePptxArchive(entries);
}

test("assembler sandwiches residual raster between the editable canvas and foreground geometry", async () => {
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
      containerShape: {
        bounds: bounds(64, 48, 640, 124),
        shape: {
          shape: "round-rectangle",
          fill: { hex: "EAF2FF", alpha: 1 },
          stroke: { hex: "2F6BCA", alpha: 1 },
          strokeWidthPt: 1,
          radiusPt: 12,
        },
      },
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
  const canvas = {
    id: "editable-canvas",
    domPath: "body > section",
    tagName: "section",
    sourceIndex: 0,
    zOrder: 0,
    cssZIndex: 0,
    bounds: bounds(0, 0, 1280, 720),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "rectangle",
      fill: { hex: "F7FAFC", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([canvas, text, shape]);
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
    slideXml.indexOf("Presenton hybrid shape editable-canvas") <
      slideXml.indexOf("Presenton hybrid backplate")
  );
  assert.ok(
    slideXml.indexOf("Presenton hybrid backplate") <
      slideXml.indexOf("Presenton hybrid shape native-shape")
  );
  assert.ok(
    slideXml.indexOf("Presenton hybrid backplate") <
      slideXml.indexOf("Presenton hybrid shape editable-cjk-container")
  );
  assert.ok(
    slideXml.indexOf("Presenton hybrid backplate") <
      slideXml.indexOf("Presenton hybrid text editable-cjk")
  );
  const textOverlayStart = slideXml.indexOf("Presenton hybrid text editable-cjk");
  const textOverlayEnd = slideXml.indexOf("</p:sp>", textOverlayStart);
  const textOverlayXml = slideXml.slice(textOverlayStart, textOverlayEnd);
  assert.match(textOverlayXml, /<a:noFill\/>/);
  assert.doesNotMatch(textOverlayXml, /val="EAF2FF"/);
  assert.match(relsXml, /hybrid-s1-backplate-[a-f0-9]{16}\.png/);
  assert.doesNotMatch(relsXml, /Id="rId2"/);
  assert.equal(entries.has("ppt/media/image1.png"), false);
  assert.ok([...entries.keys()].some((name) => /^ppt\/media\/hybrid-s1-backplate-/.test(name)));
  assert.match(entries.get("[Content_Types].xml").toString("utf8"), /Extension="png"/);
});

test("assembler stores an opaque full-slide residual as the slide background", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 3, background: "#F7FAFC" },
  })
    .png()
    .toBuffer();
  const canvas = {
    id: "editable-canvas",
    domPath: "body > section",
    tagName: "section",
    sourceIndex: 0,
    zOrder: 0,
    cssZIndex: 0,
    bounds: bounds(0, 0, 1280, 720),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "rectangle",
      fill: { hex: "F7FAFC", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([canvas]);
  const output = assembleAuthoredHybridPptx(skeleton(), [
    { slideNumber: 1, backplatePng: backplate, elements: prepared },
  ]);
  const entries = readPptxArchive(output);
  const slideXml = entries.get("ppt/slides/slide1.xml").toString("utf8");

  assert.match(slideXml, /<p:bg><p:bgPr><a:blipFill[^>]*><a:blip r:embed="rId3"\/>/);
  assert.doesNotMatch(slideXml, /Presenton hybrid backplate/);
  assert.doesNotMatch(slideXml, /Presenton hybrid shape editable-canvas/);
});

test("assembler keeps media that an untouched slide still references", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", true), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);

  assert.doesNotMatch(
    entries.get("ppt/slides/_rels/slide1.xml.rels").toString("utf8"),
    /Id="rId2"/
  );
  assert.match(
    entries.get("ppt/slides/_rels/slide2.xml.rels").toString("utf8"),
    /Id="rId2"/
  );
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler keeps media referenced by an explicitly closed Relationship", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", "explicit-close"), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);

  assert.match(
    entries.get("ppt/slides/_rels/slide2.xml.rels").toString("utf8"),
    /Id="rId2"[^>]*><\/Relationship>/
  );
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler keeps relationships referenced through an alternate namespace prefix", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", false, true), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);
  const slideXml = entries.get("ppt/slides/slide1.xml").toString("utf8");
  const relationshipsXml = entries
    .get("ppt/slides/_rels/slide1.xml.rels")
    .toString("utf8");

  assert.match(slideXml, /rel:embed="rId2"/);
  assert.match(relationshipsXml, /Id="rId2"/);
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler resolves XML entities in slide relationship references", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", false, "numeric-entity"), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);
  const slideXml = entries.get("ppt/slides/slide1.xml").toString("utf8");
  const relationshipsXml = entries
    .get("ppt/slides/_rels/slide1.xml.rels")
    .toString("utf8");

  assert.match(slideXml, /r:embed="rId&#50;"/);
  assert.match(relationshipsXml, /Id="rId2"/);
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler resolves XML entities before checking shared media targets", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", "numeric-entity"), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);

  assert.match(
    entries.get("ppt/slides/_rels/slide2.xml.rels").toString("utf8"),
    /Target="\.\.\/media\/image&#49;\.png"/
  );
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler compares shared OPC part targets without ASCII case", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", "uppercase-target"), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);

  assert.match(
    entries.get("ppt/slides/_rels/slide2.xml.rels").toString("utf8"),
    /Target="\.\.\/media\/IMAGE1\.PNG"/
  );
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler scans relationship parts without ASCII case", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  const output = assembleAuthoredHybridPptx(skeleton("12192000", "uppercase-rels-path"), [
    { slideNumber: 1, backplatePng: backplate, elements: [] },
  ]);
  const entries = readPptxArchive(output);

  assert.match(
    entries.get("ppt/slides/_RELS/slide2.xml.RELS").toString("utf8"),
    /Target="\.\.\/media\/image1\.png"/
  );
  assert.equal(entries.has("ppt/media/image1.png"), true);
});

test("assembler inserts promoted text literally even with $-escape sequences", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  })
    .png()
    .toBuffer();
  // "$'" (and "$&", "$$") are String.replace special patterns; the assembled
  // shape XML is spliced with replace(spTree, replacement) and must land verbatim.
  const text = {
    id: "dollar-text",
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
      plainText: "매출 US$'000 & 순이익 $$1",
      paragraphs: ["매출 US$'000 & 순이익 $$1"],
      style: textStyle,
      runs: [],
    },
  };
  const prepared = await prepareNativeElements([text]);
  const output = assembleAuthoredHybridPptx(skeleton(), [
    { slideNumber: 1, backplatePng: backplate, elements: prepared },
  ]);
  const slideXml = readPptxArchive(output)
    .get("ppt/slides/slide1.xml")
    .toString("utf8");

  assert.match(
    slideXml,
    /<a:t xml:space="preserve">매출 US\$&apos;000 &amp; 순이익 \$\$1<\/a:t>/
  );
  // The original single spTree must not be duplicated by pattern expansion.
  assert.equal((slideXml.match(/<p:spTree>/g) || []).length, 1);
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
