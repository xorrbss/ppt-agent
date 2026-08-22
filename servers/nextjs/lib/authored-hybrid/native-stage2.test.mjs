import test from "node:test";
import assert from "node:assert/strict";
import { nativeCoverCrop, nativeCropSourceRectXml, parseNativeObjectPosition } from "./native-image-crop.ts";
import { parseAuthoredNativeTablePayload, serializeNativeTableFrame } from "./native-table.ts";
import { inspectPowerPointFontCollectionInput } from "./pptx-font-embedding.ts";

test("table v1 serializes an editable DrawingML table keyed by QID", () => {
  const table = parseAuthoredNativeTablePayload({ schemaVersion: "authored-native-table/v1", headerRows: 1, fontFamily: "Batang", rows: [[{ text: "구분", fill: "#112233", bold: true }, { text: "값" }], [{ text: "A" }, { text: "10" }]] });
  assert.ok(table);
  const xml = serializeNativeTableFrame({ id: "table.sales", domPath: "table", tagName: "TABLE", sourceIndex: 0, zOrder: 2, cssZIndex: null, bounds: { px: { x: 10, y: 20, width: 400, height: 160 }, inches: { x: 0, y: 0, width: 1, height: 1 } }, rotationDeg: 0, opacity: 1, parentElementId: null, classification: { mode: "native", kind: "table", confidence: "safe" }, table }, 7);
  assert.match(xml, /Presenton hybrid table table\.sales/);
  assert.equal((xml.match(/<a:tr\b/g) ?? []).length, 2);
  assert.equal((xml.match(/<a:tc>/g) ?? []).length, 4);
  assert.match(xml, /typeface="Batang"/);
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
