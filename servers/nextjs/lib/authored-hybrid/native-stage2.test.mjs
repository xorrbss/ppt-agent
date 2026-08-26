import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import sharp from "sharp";
import { nativeCoverCrop, nativeCropSourceRectXml, parseNativeObjectPosition } from "./native-image-crop.ts";
import { parseAuthoredNativeTablePayload, prepareDeclaredNativeTables, serializeNativeTableFrame } from "./native-table.ts";
import { parseAuthoredNativeChartPayload } from "./native-chart.ts";
import { embedPowerPointFonts, inspectPowerPointFontCollectionInput } from "./pptx-font-embedding.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { inspectPptxEditability, inspectPptxNativeElementReadback } from "./pptx-quality-inspection.ts";
import { createAuthoredHybridCoverageManifest } from "./export-metadata.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";
import { parseActiveNativeTablePayload } from "./active-emission.ts";

function skeletonPptx() {
  return writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')],
    ["ppt/presentation.xml", Buffer.from('<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')],
    ["ppt/_rels/presentation.xml.rels", Buffer.from('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')],
    ["ppt/slides/slide1.xml", Buffer.from('<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>')],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>')],
  ]));
}

test("table v1 serializes an editable DrawingML table keyed by QID", () => {
  const headerBorder = { color: "#0B1830", widthPt: 1 };
  const table = parseAuthoredNativeTablePayload({ schemaVersion: "authored-native-table/v1", headerRows: 1, fontFamily: "Batang", rows: [[{ text: "구분", fill: "#112233", bold: true, fontFamily: "Malgun Gothic", fontSizePt: 12 }, { text: "" }], [{ text: "A" }, { text: "10" }]], columnWidths: [120, 280], rowHeightsPt: [28, 36], mergedCells: [{ row: 0, column: 0, rowSpan: 1, columnSpan: 2 }], cellStyles: [[{ fill: "#1463FF", borders: { top: headerBorder, right: headerBorder, bottom: headerBorder, left: headerBorder }, horizontalAlign: "center", verticalAlign: "middle" }, {}], [{ horizontalAlign: "left", verticalAlign: "bottom" }, {}]] });
  assert.ok(table);
  const xml = serializeNativeTableFrame({ id: "table.sales", domPath: "table", tagName: "TABLE", sourceIndex: 0, zOrder: 2, cssZIndex: null, bounds: { px: { x: 10, y: 20, width: 400, height: 160 }, inches: { x: 0, y: 0, width: 1, height: 1 } }, rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "table", confidence: "safe" }, table }, 7);
  assert.match(xml, /Presenton hybrid table table\.sales/);
  assert.equal((xml.match(/<a:tr\b/g) ?? []).length, 2);
  assert.equal((xml.match(/<a:tc\b/g) ?? []).length, 4);
  assert.match(xml, /typeface="Batang"/);
  assert.match(xml, /typeface="Malgun Gothic"/);
  assert.match(xml, /sz="1200"/);
  assert.match(xml, /gridSpan="2"/);
  assert.match(xml, /hMerge="1"/);
  assert.match(xml, /<a:tr h="355600">/);
  assert.match(xml, /<a:tc gridSpan="2">[\s\S]*?<a:tcPr anchor="ctr"><a:lnL w="12700">[\s\S]*?<\/a:lnL><a:lnR w="12700">[\s\S]*?<\/a:lnR><a:lnT w="12700">[\s\S]*?<\/a:lnT><a:lnB w="12700">[\s\S]*?<\/a:lnB><a:solidFill><a:srgbClr val="1463FF"\/><\/a:solidFill><\/a:tcPr><\/a:tc>/);
  assert.match(xml, /<a:tc hMerge="1">[\s\S]*?<a:tcPr anchor="ctr"><a:lnL><a:noFill\/><\/a:lnL><a:lnR w="12700">[\s\S]*?<\/a:lnR><a:lnT w="12700">[\s\S]*?<\/a:lnT><a:lnB w="12700">[\s\S]*?<\/a:lnB><a:solidFill><a:srgbClr val="1463FF"\/><\/a:solidFill><\/a:tcPr><\/a:tc>/);
  assert.match(xml, /<a:tcPr anchor="b"><a:lnL><a:noFill\/><\/a:lnL><a:lnR><a:noFill\/><\/a:lnR><a:lnT w="12700">[\s\S]*?<\/a:lnT><a:lnB><a:noFill\/><\/a:lnB><a:noFill\/><\/a:tcPr>/);
  const archive = readPptxArchive(skeletonPptx());
  const slidePath = "ppt/slides/slide1.xml";
  archive.set(slidePath, Buffer.from(archive.get(slidePath).toString("utf8").replace("</p:spTree>", `${xml}</p:spTree>`)));
  const readback = inspectPptxNativeElementReadback(writePptxArchive(archive));
  assert.deepEqual(readback.find((entry) => entry.elementId === "table.sales")?.table?.cellStyles[0][0].borders.right, { color: "#0B1830", widthPt: 1 });
  assert.equal(readback.find((entry) => entry.elementId === "table.sales")?.table?.cellStyles[0][1].fill, "#1463FF");
  assert.match(xml, /<a:pPr algn="ctr"\/>/);
});

test("table borders serialize in the DrawingML CT_TableCellProperties sequence", () => {
  const table = parseAuthoredNativeTablePayload({
    schemaVersion: "authored-native-table/v1",
    rows: [[{ text: "A" }]],
    cellStyles: [[{ borders: {
      top: { color: "#111111", widthPt: 1 },
      right: { color: "#222222", widthPt: 1 },
      bottom: { color: "#333333", widthPt: 1 },
      left: { color: "#444444", widthPt: 1 },
    } }]],
  });
  assert.ok(table);
  const xml = serializeNativeTableFrame({ id: "table.border-order", domPath: "table", tagName: "TABLE", sourceIndex: 0, zOrder: 0, cssZIndex: null, bounds: { px: { x: 0, y: 0, width: 100, height: 40 }, inches: { x: 0, y: 0, width: 1, height: 1 } }, rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "table", confidence: "safe" }, table }, 7);
  assert.match(xml, /<a:tcPr[^>]*><a:lnL\b[\s\S]*?<a:lnR\b[\s\S]*?<a:lnT\b[\s\S]*?<a:lnB\b[\s\S]*?<a:noFill\/><\/a:tcPr>/);
});

test("declared table admission requires exact browser cell evidence and CSS allowlist", () => {
  const payload = { schemaVersion: "authored-native-table/v1", headerRows: 1, fontFamily: "Arial", fontSizePt: 10, rows: [[{ text: "Name", fill: "#112233", textColor: "#FFFFFF", fontFamily: "Aptos", fontSizePt: 11 }, { text: "Value" }], [{ text: "A" }, { text: "10" }]] };
  const declared = [{ elementId: "table.safe", kind: "table", sourceDigest: "sha256:" + "a".repeat(64), requiredFeatures: ["table", "table-v1", "cell-text", "cell-fill", "cell-font"], payload }];
  const evidence = { rows: [[{ text: "Name", fill: "#112233", textColor: "#FFFFFF", bold: true, fontFamily: "Aptos", fontSizePt: 11 }, { text: "Value", textColor: "#202020", bold: true, fontFamily: "Arial", fontSizePt: 10 }], [{ text: "A", textColor: "#202020", bold: false, fontFamily: "Arial", fontSizePt: 10 }, { text: "10", textColor: "#202020", bold: false, fontFamily: "Arial", fontSizePt: 10 }]] };
  const observed = [{ id: "table.safe", identityAuthority: "declared-qid", domPath: "table", tagName: "table", sourceIndex: 0, cssZIndex: null, boundsPx: { x: 10, y: 20, width: 400, height: 200 }, rotationDeg: 0, opacity: 1, candidateKind: "complex", fallbackReasons: ["complex-table"], tableEvidence: evidence }];
  const admitted = prepareDeclaredNativeTables(declared, observed);
  assert.equal(admitted.tables.length, 1);
  assert.deepEqual([...admitted.rejections], []);
  const changed = structuredClone(observed[0]);
  changed.tableEvidence.rows[1][1].text = "11";
  assert.equal(prepareDeclaredNativeTables(declared, [changed]).rejections.get("table.safe"), "table-observation-mismatch");
  assert.equal(prepareDeclaredNativeTables(declared, [{ ...observed[0], fallbackReasons: ["filter", "complex-table"] }]).rejections.get("table.safe"), "unsupported-table-css");
});

test("table font evidence canonicalizes CSS float noise to milli-point and rejects meaningful drift", () => {
  const payload = { schemaVersion: "authored-native-table/v1", headerRows: 0, fontFamily: "Arial", fontSizePt: 10, rows: [[{ text: "A" }]] };
  const declared = [{ elementId: "table.precision", kind: "table", sourceDigest: "sha256:" + "f".repeat(64), requiredFeatures: ["table", "table-v1", "cell-text", "cell-font"], payload }];
  const observed = [{ id: "table.precision", identityAuthority: "declared-qid", domPath: "table", tagName: "table", sourceIndex: 0, cssZIndex: null, boundsPx: { x: 10, y: 20, width: 200, height: 80 }, rotationDeg: 0, opacity: 1, candidateKind: "complex", fallbackReasons: ["complex-table"], tableEvidence: { rows: [[{ text: "A", textColor: "#202020", bold: false, fontFamily: "Arial", fontSizePt: 9.999975 }]] } }];
  assert.equal(prepareDeclaredNativeTables(declared, observed).tables.length, 1);
  const drifted = structuredClone(observed[0]);
  drifted.tableEvidence.rows[0][0].fontSizePt = 10.01;
  assert.equal(prepareDeclaredNativeTables(declared, [drifted]).rejections.get("table.precision"), "table-observation-mismatch");
  assert.equal(parseAuthoredNativeTablePayload({ ...payload, fontSizePt: 9.999975 }).fontSizePt, 10);
});

test("table v1 accepts server-normalized null optionals without weakening invalid values", () => {
  const payload = {
    schemaVersion: "authored-native-table/v1",
    rows: [[
      { text: "분기", fill: null, textColor: null, bold: false, fontFamily: null, fontSizePt: null },
      { text: "값", fill: "#2563EB", textColor: "#FFFFFF", bold: true, fontFamily: null, fontSizePt: null },
    ]],
    columnWidths: null,
    headerRows: null,
    fontFamily: null,
    fontSizePt: null,
  };
  assert.deepEqual(parseAuthoredNativeTablePayload(payload), {
    schemaVersion: "authored-native-table/v1",
    rows: [[{ text: "분기", bold: false }, { text: "값", fill: "#2563EB", textColor: "#FFFFFF", bold: true }]],
  });
  assert.equal(parseAuthoredNativeTablePayload({ ...payload, fontSizePt: "18" }), null);
});

test("active emission joins durable rows to the exact advanced Scene table receipt", () => {
  const parsed = parseActiveNativeTablePayload(
    { schemaVersion: "authored-native-table/v1", rows: [[{ text: "A" }, { text: "" }], [{ text: "C" }, { text: "D" }]], columnWidths: [120, 120], headerRows: 0 },
    { schemaVersion: "authored-native-scene-declaration/v1", capabilityId: "editable.table.advanced.v1", payload: { cells: [["A", ""], ["C", "D"]], columnWidthsPt: [160, 320], rowHeightsPt: [45, 105], mergedCells: [{ row: 0, column: 0, rowSpan: 1, columnSpan: 2 }], cellStyles: [[{ fill: "#1463FF", borders: { bottom: { color: "#0B1830", widthPt: 1 } }, horizontalAlign: "center", verticalAlign: "middle" }, {}], [{}, {}]] } },
  );
  assert.ok(parsed);
  assert.deepEqual(parsed.columnWidths, [160, 320]);
  assert.deepEqual(parsed.rowHeightsPt, [45, 105]);
  assert.equal(parsed.mergedCells[0].columnSpan, 2);
  assert.equal(parsed.cellStyles[0][0].horizontalAlign, "center");
});

test("cover crop preserves bounded object-position as DrawingML srcRect", () => {
  assert.deepEqual(parseNativeObjectPosition("right 25%"), { x: 1, y: .25 });
  const crop = nativeCoverCrop(2000, 1000, 1000, 1000, "right 25%");
  assert.deepEqual(crop, { left: .5, right: 0, top: 0, bottom: 0 });
  assert.equal(nativeCropSourceRectXml(crop), '<a:srcRect l="50000" t="0" r="0" b="0"/>');
  assert.equal(parseNativeObjectPosition("calc(50%) top"), null);
});

test("TTC packaging fails closed with a stable collection receipt", () => {
  const invalidTtc = Buffer.alloc(12); invalidTtc.write("ttcf", 0, "ascii"); invalidTtc.writeUInt32BE(0x00010000, 4); invalidTtc.writeUInt32BE(0, 8);
  assert.deepEqual(inspectPowerPointFontCollectionInput({ data: invalidTtc, collectionFaceIndex: 0 }), { schemaVersion: "powerpoint-font-collection-receipt/v1", state: "invalid-collection", reasonCode: "pptx_font_collection_invalid", requestedFaceIndex: 0, faces: [] });
});

test("assembler preserves Scene-authoritative advanced text autofit and empty-run identity", async () => {
  const bounds = (x, y, width, height) => ({
    px: { x, y, width, height },
    inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
  });
  const textStyle = {
    fontFamily: "Malgun Gothic",
    fontFamilies: ["Malgun Gothic"],
    cjkFallbackFamilies: ["Malgun Gothic"],
    fontSizePt: 22.5,
    fontWeight: 400,
    bold: false,
    italic: false,
    underline: false,
    strike: false,
    color: { hex: "0F172A", alpha: 1 },
    letterSpacingPt: 0,
    lineHeight: { points: 32.625, multiple: 1.45, source: "computed" },
    horizontalAlignment: "left",
    verticalAlignment: "top",
    direction: "ltr",
    wrapMode: "wrap",
  };
  const advancedText = {
    schemaVersion: "authored-advanced-text-owner/v1",
    text: "긴 text",
    autoFit: "shrink",
    style: { fontFamily: "Malgun Gothic", fontSizePt: 22.5, fontWeight: 400, color: "#0F172A", italic: false, underline: false },
    runs: [
      { runId: "run:stage2.0", text: "긴 ", fontFamily: null, fontSizePt: null, fontWeight: null, color: null, italic: null, underline: null },
      { runId: "run:stage2.empty", text: "", fontFamily: null, fontSizePt: null, fontWeight: null, color: null, italic: null, underline: null },
      { runId: "run:stage2.1", text: "text", fontFamily: null, fontSizePt: null, fontWeight: 700, color: "#1463FF", italic: null, underline: null },
    ],
  };
  const backplatePng = await sharp({
    create: { width: 1280, height: 720, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } },
  }).png().toBuffer();
  const textElement = {
    kind: "text",
    source: {
      id: "s05-shrink",
      domPath: "body > p:nth-of-type(1)",
      tagName: "p",
      sourceIndex: 0,
      zOrder: 1,
      cssZIndex: 1,
      bounds: bounds(90, 180, 1040, 120),
      rotationDeg: 0,
      opacity: 1,
      classification: { mode: "native", kind: "text", confidence: "safe" },
      text: { role: "body", plainText: "긴 text", paragraphs: ["긴 text"], style: textStyle, runs: [] },
    },
  };
  const pptx = assembleAuthoredHybridPptx(skeletonPptx(), [{
    slideNumber: 1,
    backplatePng,
    elements: [textElement],
    nativeOwnership: {
      slideId: "slide:advanced-stage2",
      owners: [{
        elementId: "s05-shrink",
        kind: "text",
        capabilityId: "editable.text.advanced.v1",
        advancedText,
      }],
    },
  }]);
  const slideXml = readPptxArchive(pptx).get("ppt/slides/slide1.xml").toString("utf8");
  assert.match(slideXml, /<a:normAutofit\/>/);
  assert.doesNotMatch(slideXml, /<a:noAutofit\/>/);
  assert.equal((slideXml.match(/<a:r>/g) ?? []).length, 3);
  assert.match(slideXml, /runId="run:stage2\.empty"/);
  assert.match(slideXml, /runId="run:stage2\.empty"[\s\S]*?<a:t xml:space="preserve"><\/a:t>/);
  assert.match(slideXml, /<pt:advancedText\b[^>]*digest="sha256:[0-9a-f]{64}"/);
});

test("stage2 eligible table and crop survive assembler, OOXML readback, and coverage metadata", async () => {
  const table = parseAuthoredNativeTablePayload({ schemaVersion: "authored-native-table/v1", rows: [[{ text: "구분" }, { text: "" }], [{ text: "A" }, { text: "10" }]], headerRows: 1, columnWidths: [180, 340], rowHeightsPt: [42, 138], mergedCells: [{ row: 0, column: 0, rowSpan: 1, columnSpan: 2 }], cellStyles: [[{ fill: "#1463FF", borders: { bottom: { color: "#0B1830", widthPt: 1 } }, horizontalAlign: "center", verticalAlign: "middle" }, {}], [{ horizontalAlign: "left", verticalAlign: "middle" }, { horizontalAlign: "right", verticalAlign: "middle" }]] });
  assert.ok(table);
  const chartBase = { chartStyleVersion: "bounded-native-chart-style/v1", chartStyle: 2, categories: ["Q1", "Q2", "Q3"], title: "Actual product", legend: { visible: true, position: "bottom" }, fontFamily: "Malgun Gothic", fontFace: "Regular", eastAsianTypeface: "Malgun Gothic", fontSizePt: 10, textColor: "#0B1830" };
  const line = parseAuthoredNativeChartPayload({ ...chartBase, chartType: "line", palette: ["#1463FF"], series: [{ name: "Revenue", values: [10, 14, 13], color: "#1463FF", pointColors: ["#1463FF", "#0EA5E9", "#7C3AED"] }] });
  const pie = parseAuthoredNativeChartPayload({ ...chartBase, chartType: "pie", palette: ["#F59E0B"], series: [{ name: "Share", values: [50, 30, 20], color: "#F59E0B", pointColors: ["#F59E0B", "#EF4444", "#10B981"] }] });
  assert.ok(line); assert.ok(pie);
  const bounds = (x, y, width, height) => ({ px: { x, y, width, height }, inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 } });
  const backplatePng = await sharp({ create: { width: 1280, height: 720, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } }).png().toBuffer();
  const imagePng = await sharp({ create: { width: 200, height: 100, channels: 3, background: "#2457D6" } }).png().toBuffer();
  const pptx = assembleAuthoredHybridPptx(skeletonPptx(), [{ slideNumber: 1, backplatePng, elements: [
    { kind: "table", source: { id: "table.stage2", domPath: "table", tagName: "TABLE", sourceIndex: 0, zOrder: 1, cssZIndex: 1, bounds: bounds(80, 80, 520, 240), rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "table", confidence: "safe" }, table } },
    { kind: "chart", source: { id: "chart.line", domPath: "canvas", tagName: "CANVAS", sourceIndex: 1, zOrder: 2, cssZIndex: 2, bounds: bounds(650, 80, 260, 200), rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "chart", confidence: "safe" }, chart: line } },
    { kind: "chart", source: { id: "chart.pie", domPath: "canvas", tagName: "CANVAS", sourceIndex: 2, zOrder: 3, cssZIndex: 3, bounds: bounds(930, 80, 260, 200), rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "chart", confidence: "safe" }, chart: pie } },
    { kind: "image", png: imagePng, source: { id: "image.crop", domPath: "img", tagName: "IMG", sourceIndex: 3, zOrder: 4, cssZIndex: 4, bounds: bounds(650, 320, 300, 240), rotationDeg: 0, opacity: 1, classification: { mode: "native", kind: "image", confidence: "safe" }, image: { src: "data:image/png;base64," + imagePng.toString("base64"), alt: "crop", naturalWidth: 200, naturalHeight: 100, objectFit: "cover", objectPosition: "right 25%", crop: { left: .375, top: 0, right: 0, bottom: 0 } } } },
  ] }]);
  const archive = readPptxArchive(pptx);
  const chartXml = [...archive.entries()].filter(([name]) => /^ppt\/charts\/chart\d+\.xml$/.test(name)).map(([, bytes]) => bytes.toString("utf8")).join("\n");
  assert.match(chartXml, /<c:lineChart>/); assert.match(chartXml, /<c:pieChart>/); assert.match(chartXml, /<c:style val="2"\/>/); assert.match(chartXml, /<a:srgbClr val="EF4444"\/>/);
  const readback = inspectPptxNativeElementReadback(pptx);
  assert.equal(readback.find((entry) => entry.elementId === "table.stage2")?.table?.rows, 2);
  assert.equal(readback.find((entry) => entry.elementId === "image.crop")?.imageCrop?.left, 37500);
  const quality = inspectPptxEditability(pptx);
  assert.equal(quality.slides[0].nativeTableElements, 1);
  assert.equal(quality.slides[0].nativeChartElements, 2);
  assert.equal(quality.slides[0].nativeImageCropElements, 1);
  const manifest = createAuthoredHybridCoverageManifest(quality, { exportAttemptId: "attempt-stage2", presentationRevision: 1, canonicalSourceDigest: "sha256:" + "a".repeat(64), sourceDigest: "sha256:" + "b".repeat(64) });
  assert.equal(manifest.nativeTable, 1);
  assert.equal(manifest.ooxmlReadback.nativeTable, 1);
  assert.equal(manifest.nativeChart, 2);
  assert.equal(manifest.ooxmlReadback.nativeChart, 2);
  assert.equal(manifest.nativeImageCrop, 1);
  assert.equal(manifest.ooxmlReadback.nativeImageCrop, 1);
});

test("font embedding exposes stable TTC extraction and package-reopen receipt", { skip: !existsSync("C:\\Windows\\Fonts\\batang.ttc") }, () => {
  const data = readFileSync("C:\\Windows\\Fonts\\batang.ttc");
  const packaged = embedPowerPointFonts(skeletonPptx(), [{ typeface: "Batang", charset: 129, faces: { regular: { data, source: "windows-system-reviewed:batang.ttc#face0", collectionFaceIndex: 0 } } }]);
  assert.equal(packaged.result.applied, true);
  assert.deepEqual(packaged.result.reopenReceipt, { schemaVersion: "powerpoint-font-reopen-receipt/v1", state: "package-reopened", embeddedFontFiles: 1, embeddedTypefaces: 1, collectionFaces: [inspectPowerPointFontCollectionInput({ data, collectionFaceIndex: 0 })], reasonCode: null });
});
