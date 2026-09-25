import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { documentCanvasForOrientation, documentCanvasCanonicalJson, documentCanvasDigest, parseDocumentCanvas } from "./document-canvas.ts";
import { resolveAuthoredCanvas, authoredCanvasPhysicalSize, canvasEmuPerUnit } from "./authored-canvas.ts";

test("A4 physical size is independent of legacy pixels and orientation", () => {
  for (const [orientation, width, height] of [["landscape", 10692000, 7560000], ["portrait", 7560000, 10692000]]) {
    const canvas = resolveAuthoredCanvas({ documentCanvas: documentCanvasForOrientation(orientation) });
    assert.equal(authoredCanvasPhysicalSize(canvas).widthEmu, width);
    assert.equal(authoredCanvasPhysicalSize(canvas).heightEmu, height);
    assert.equal(canvasEmuPerUnit(canvas), 9000);
  }
  assert.deepEqual(authoredCanvasPhysicalSize(resolveAuthoredCanvas({})), {
    widthEmu: 12192000, heightEmu: 6858000, widthPt: 960, heightPt: 540,
  });
});

test("canonical bytes and micro units are independent of numeric spelling", async () => {
  const input = documentCanvasForOrientation("landscape");
  const expected = '["document-canvas/v1","1188","840","10692000","7560000","60000000","60000000","1068000000","720000000"]';
  assert.equal(documentCanvasCanonicalJson(input), expected);
  const fixtures = JSON.parse(readFileSync(new URL("../../../contracts/document-canvas/v1/fixtures.json", import.meta.url)));
  for (const fixture of fixtures.a4) {
    assert.equal(documentCanvasCanonicalJson(fixture.canvas), fixture.canonicalBytes);
    assert.equal(await documentCanvasDigest(fixture.canvas), fixture.digest);
  }
  const zero = { ...input, safeArea: { ...input.safeArea, x: -0 } };
  assert.equal(documentCanvasCanonicalJson(zero), documentCanvasCanonicalJson({ ...zero, safeArea: { ...zero.safeArea, x: 0 } }));
  assert.doesNotThrow(() => parseDocumentCanvas({ ...input, safeArea: { ...input.safeArea, x: 1e-6 } }));
});

test("invalid new metadata fails closed, including mixed legacy metadata", () => {
  const input = documentCanvasForOrientation("landscape");
  for (const bad of [null, {}, { ...input, extra: true }, { ...input, contractVersion: "document-canvas/v2" },
    { ...input, logicalSize: { width: "1188", height: 840 } },
    { ...input, physicalSize: { widthEmu: 10692000, heightEmu: 7550000 } },
    { ...input, safeArea: { ...input.safeArea, x: 1e-7 } },
    { ...input, safeArea: { ...input.safeArea, width: 1188 } },
  ]) assert.throws(() => resolveAuthoredCanvas({ documentCanvas: bad }));
  assert.throws(() => resolveAuthoredCanvas({ documentCanvas: input, authored_vnext: { canvas: resolveAuthoredCanvas({}) } }));
});

test("source A4 keeps exact PPTX EMU with only bounded raster rounding", () => {
  const canvas = parseDocumentCanvas({
    contractVersion: "document-canvas/v1",
    logicalSize: { width: 1188, height: 840 },
    physicalSize: { widthEmu: 10691813, heightEmu: 7559675 },
    safeArea: { x: 60, y: 60, width: 1068, height: 720 },
  });
  assert.deepEqual(canvas.physicalSize, { widthEmu: 10691813, heightEmu: 7559675 });
});
