import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

import { prepareNativeElements } from "./native-plan.ts";
import {
  assembleAuthoredHybridPptx,
  SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX,
} from "./pptx-assembler.ts";
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

test("assembler preserves audited raster-native-raster z-band order", async () => {
  const raster = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } },
  }).png().toBuffer();
  const text = {
    id: "middle.text", identityAuthority: "declared-qid", domPath: "body > p", tagName: "p",
    sourceIndex: 0, zOrder: 1, cssZIndex: 1, bounds: bounds(100, 100, 300, 60), rotationDeg: 0, opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: { role: "body", plainText: "중간 native", paragraphs: ["중간 native"], style: textStyle, runs: [] },
  };
  const prepared = await prepareNativeElements([text]);
  const pptx = assembleAuthoredHybridPptx(skeleton(), [{
    slideNumber: 1,
    backplatePng: raster,
    elements: prepared,
    rasterBands: [
      { zBand: 0, rasterPng: raster, semanticOwnerQids: ["lower.raster"] },
      { zBand: 2, rasterPng: raster, semanticOwnerQids: ["upper.raster"] },
    ],
    nativeZBandByElementId: { "middle.text": 1 },
  }]);
  const xml = readPptxArchive(pptx).get("ppt/slides/slide1.xml").toString("utf8");
  const lower = xml.indexOf("Presenton hybrid backplate");
  const middle = xml.indexOf("Presenton hybrid text middle.text");
  const upper = xml.indexOf("Presenton hybrid raster z-band 1");
  assert.ok(lower >= 0 && lower < middle && middle < upper);
  assert.equal((xml.match(/presenton-semantic-raster-ownership-v1:/g) ?? []).length, 2);
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

test("assembler preserves the exact raster semantic QID set in PowerPoint-readable ownership shapes", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 3, background: "#F7FAFC" },
  }).png().toBuffer();
  const qids = ["s01-title-main", "s01-chart-revenue", "s01-note-cjk"];
  const output = assembleAuthoredHybridPptx(skeleton(), [{
    slideNumber: 1,
    backplatePng: backplate,
    elements: [],
    semanticOwnerQids: qids,
  }]);
  const slideXml = readPptxArchive(output)
    .get("ppt/slides/slide1.xml")
    .toString("utf8");

  assert.match(slideXml, /<p:bg>/);
  for (const qid of qids) {
    assert.match(
      slideXml,
      new RegExp(`name="Presenton raster visual fragment ${qid} semantic-owner"`),
    );
  }
  const descriptions = [...slideXml.matchAll(/\bdescr="([^"]+)"/g)]
    .map((match) => match[1])
    .filter((value) => value.startsWith(SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX));
  assert.equal(descriptions.length, qids.length);
  const observedQids = descriptions.flatMap((description) => {
    const encoded = description.slice(SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX.length);
    const payload = JSON.parse(Buffer.from(encoded, "base64url").toString("utf8"));
    assert.equal(payload.schemaVersion, "presenton-semantic-raster-ownership/v1");
    assert.equal(payload.slideNumber, 1);
    assert.match(payload.qidSetDigest, /^sha256:[a-f0-9]{64}$/);
    return payload.qids;
  });
  assert.deepEqual(observedQids, qids);
  assert.throws(
    () => assembleAuthoredHybridPptx(skeleton(), [{
      slideNumber: 1,
      backplatePng: backplate,
      elements: [],
      semanticOwnerQids: [qids[0], qids[0]],
    }]),
    /semantic ownership QIDs must be unique/,
  );
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

test("assembler preserves authored breaks, bounds text safely, and never emits CJK as Consolas", async () => {
  const backplate = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: "transparent" },
  }).png().toBuffer();
  const risky = {
    id: "mixed-cjk-mono",
    domPath: "body > h1",
    tagName: "h1",
    sourceIndex: 0,
    zOrder: 1,
    cssZIndex: 1,
    bounds: bounds(1120, 620, 120, 52),
    rotationDeg: 0,
    opacity: 1,
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "120→80억원\n검증",
      paragraphs: ["120→80억원", "검증"],
      style: {
        ...textStyle,
        fontFamily: "Consolas",
        fontFamilies: ["Consolas", "monospace"],
        cjkFallbackFamilies: ["Consolas"],
        fontSizePt: 12,
        fontWeight: 400,
        bold: false,
        lineHeight: { points: 15, multiple: 1.25, source: "computed" },
      },
      runs: [],
    },
  };
  const stable = {
    ...risky,
    id: "embedded-cjk",
    sourceIndex: 1,
    bounds: bounds(800, 540, 180, 32),
    text: {
      ...risky.text,
      plainText: "안정된 본문",
      paragraphs: ["안정된 본문"],
      style: {
        ...risky.text.style,
        fontFamily: "Noto Sans KR",
        fontFamilies: ["Noto Sans KR", "sans-serif"],
        cjkFallbackFamilies: ["Noto Sans KR", "Malgun Gothic"],
      },
    },
  };
  const prepared = await prepareNativeElements([risky, stable]);
  const output = assembleAuthoredHybridPptx(
    skeleton(),
    [{ slideNumber: 1, backplatePng: backplate, elements: prepared }],
    { embeddedTypefaceFamilies: ["Noto Sans KR"] }
  );
  const slideXml = readPptxArchive(output).get("ppt/slides/slide1.xml").toString("utf8");
  const riskyStart = slideXml.indexOf("Presenton hybrid text mixed-cjk-mono");
  const riskyXml = slideXml.slice(riskyStart, slideXml.indexOf("</p:sp>", riskyStart));
  const stableStart = slideXml.indexOf("Presenton hybrid text embedded-cjk");
  const stableXml = slideXml.slice(stableStart, slideXml.indexOf("</p:sp>", stableStart));

  assert.match(riskyXml, /<a:br\/>/);
  assert.doesNotMatch(riskyXml, /typeface="Consolas"/i);
  assert.match(riskyXml, /typeface="Noto Sans KR"/);
  assert.match(riskyXml, /horzOverflow="clip" vertOverflow="clip"/);
  assert.match(riskyXml, /<a:noAutofit\/>/);
  assert.match(stableXml, /<a:noAutofit\/>/);
  const transform = riskyXml.match(/<a:off x="(\d+)" y="(\d+)"\/><a:ext cx="(\d+)" cy="(\d+)"\/>/);
  assert.ok(transform);
  assert.ok(Number(transform[1]) + Number(transform[3]) <= 12_192_000);
  assert.ok(Number(transform[2]) + Number(transform[4]) <= 6_858_000);
  assert.ok(Number(transform[3]) > 120 * 9_525);
});

test("assembler writes provenance as valid, merge-safe PPTX custom properties", () => {
  const entries = readPptxArchive(skeleton());
  entries.set("_rels/.rels", Buffer.from(`<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
</Relationships>`));
  entries.set("docProps/custom.xml", Buffer.from(`<?xml version="1.0"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="Customer.Reference"><vt:lpwstr>keep-me</vt:lpwstr></property>
</Properties>`));
  const output = assembleAuthoredHybridPptx(writePptxArchive(entries), [], {
    provenance: {
      exportAttemptId: "attempt-42",
      presentationRevision: 13,
      canonicalSourceDigest: `sha256:${"a".repeat(64)}`,
      sourceDigest: "b".repeat(64),
    },
  });
  const packaged = readPptxArchive(output);
  const custom = packaged.get("docProps/custom.xml").toString("utf8");
  const rootRelationships = packaged.get("_rels/.rels").toString("utf8");
  const contentTypes = packaged.get("[Content_Types].xml").toString("utf8");

  assert.match(custom, /name="Customer\.Reference"><vt:lpwstr>keep-me<\/vt:lpwstr>/);
  assert.match(custom, /name="Presenton\.ExportAttemptId"><vt:lpwstr>attempt-42<\/vt:lpwstr>/);
  assert.match(custom, /name="Presenton\.PresentationRevision"><vt:i8>13<\/vt:i8>/);
  assert.equal((custom.match(/name="Presenton\./g) ?? []).length, 4);
  assert.equal(new Set([...custom.matchAll(/\bpid="(\d+)"/g)].map((match) => match[1])).size, 5);
  assert.match(rootRelationships, /relationships\/custom-properties" Target="docProps\/custom\.xml"/);
  assert.equal((rootRelationships.match(/relationships\/custom-properties/g) ?? []).length, 1);
  assert.match(contentTypes, /PartName="\/docProps\/custom\.xml" ContentType="application\/vnd\.openxmlformats-officedocument\.custom-properties\+xml"/);
});
