import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import sharp from "sharp";
import { nativeCoverCrop, nativeCropSourceRectXml, parseNativeObjectPosition } from "./native-image-crop.ts";
import { parseAuthoredNativeTablePayload, prepareDeclaredNativeTables, serializeNativeTableFrame } from "./native-table.ts";
import { embedPowerPointFonts, inspectPowerPointFontCollectionInput } from "./pptx-font-embedding.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { inspectPptxEditability, inspectPptxNativeElementReadback } from "./pptx-quality-inspection.ts";
import { createAuthoredHybridCoverageManifest } from "./export-metadata.ts";
import { writePptxArchive } from "./pptx-archive.ts";

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
  const table = parseAuthoredNativeTablePayload({ schemaVersion: "authored-native-table/v1", headerRows: 1, fontFamily: "Batang", rows: [[{ text: "구분", fill: "#112233", bold: true, fontFamily: "Malgun Gothic", fontSizePt: 12 }, { text: "값" }], [{ text: "A" }, { text: "10" }]] });
  assert.ok(table);
  const xml = serializeNativeTableFrame({ id: "table.sales", domPath: "table", tagName: "TABLE", sourceIndex: 0, zOrder: 2, cssZIndex: null, bounds: { px: { x: 10, y: 20, width: 400, height: 160 }, inches: { x: 0, y: 0, width: 1, height: 1 } }, rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "table", confidence: "safe" }, table }, 7);
  assert.match(xml, /Presenton hybrid table table\.sales/);
  assert.equal((xml.match(/<a:tr\b/g) ?? []).length, 2);
  assert.equal((xml.match(/<a:tc>/g) ?? []).length, 4);
  assert.match(xml, /typeface="Batang"/);
  assert.match(xml, /typeface="Malgun Gothic"/);
  assert.match(xml, /sz="1200"/);
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

test("stage2 eligible table and crop survive assembler, OOXML readback, and coverage metadata", async () => {
  const table = parseAuthoredNativeTablePayload({ rows: [[{ text: "구분" }, { text: "값" }], [{ text: "A" }, { text: "10" }]], headerRows: 1 });
  assert.ok(table);
  const bounds = (x, y, width, height) => ({ px: { x, y, width, height }, inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 } });
  const backplatePng = await sharp({ create: { width: 1280, height: 720, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } } }).png().toBuffer();
  const imagePng = await sharp({ create: { width: 200, height: 100, channels: 3, background: "#2457D6" } }).png().toBuffer();
  const pptx = assembleAuthoredHybridPptx(skeletonPptx(), [{ slideNumber: 1, backplatePng, elements: [
    { kind: "table", source: { id: "table.stage2", domPath: "table", tagName: "TABLE", sourceIndex: 0, zOrder: 1, cssZIndex: 1, bounds: bounds(80, 80, 520, 240), rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "table", confidence: "safe" }, table } },
    { kind: "image", png: imagePng, source: { id: "image.crop", domPath: "img", tagName: "IMG", sourceIndex: 1, zOrder: 2, cssZIndex: 2, bounds: bounds(650, 80, 300, 240), rotationDeg: 0, opacity: 1, classification: { mode: "native", kind: "image", confidence: "safe" }, image: { src: "data:image/png;base64," + imagePng.toString("base64"), alt: "crop", naturalWidth: 200, naturalHeight: 100, objectFit: "cover", objectPosition: "right 25%", crop: { left: .375, top: 0, right: 0, bottom: 0 } } } },
  ] }]);
  const readback = inspectPptxNativeElementReadback(pptx);
  assert.equal(readback.find((entry) => entry.elementId === "table.stage2")?.table?.rows, 2);
  assert.equal(readback.find((entry) => entry.elementId === "image.crop")?.imageCrop?.left, 37500);
  const quality = inspectPptxEditability(pptx);
  assert.equal(quality.slides[0].nativeTableElements, 1);
  assert.equal(quality.slides[0].nativeImageCropElements, 1);
  const manifest = createAuthoredHybridCoverageManifest(quality, { exportAttemptId: "attempt-stage2", presentationRevision: 1, canonicalSourceDigest: "sha256:" + "a".repeat(64), sourceDigest: "sha256:" + "b".repeat(64) });
  assert.equal(manifest.nativeTable, 1);
  assert.equal(manifest.ooxmlReadback.nativeTable, 1);
  assert.equal(manifest.nativeImageCrop, 1);
  assert.equal(manifest.ooxmlReadback.nativeImageCrop, 1);
});

test("font embedding exposes stable TTC extraction and package-reopen receipt", { skip: !existsSync("C:\\Windows\\Fonts\\batang.ttc") }, () => {
  const data = readFileSync("C:\\Windows\\Fonts\\batang.ttc");
  const packaged = embedPowerPointFonts(skeletonPptx(), [{ typeface: "Batang", charset: 129, faces: { regular: { data, source: "windows-system-reviewed:batang.ttc#face0", collectionFaceIndex: 0 } } }]);
  assert.equal(packaged.result.applied, true);
  assert.deepEqual(packaged.result.reopenReceipt, { schemaVersion: "powerpoint-font-reopen-receipt/v1", state: "package-reopened", embeddedFontFiles: 1, embeddedTypefaces: 1, collectionFaces: [inspectPowerPointFontCollectionInput({ data, collectionFaceIndex: 0 })], reasonCode: null });
});
