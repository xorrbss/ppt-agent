import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { createPowerPointFontData } from "./pptx-font-data.ts";
import { extractSfntCollectionFace, inspectSfntCollection, inspectSfntFont } from "./font-sfnt-inspection.ts";
import { inspectPowerPointFontCollectionInput } from "./pptx-font-embedding.ts";

const BATANG_TTC = "C:\\Windows\\Fonts\\batang.ttc";

function sfntChecksum(data) {
  let sum = 0;
  for (let offset = 0; offset < Math.ceil(data.length / 4) * 4; offset += 4) {
    let word = 0;
    for (let byte = 0; byte < 4; byte += 1) word = (word * 256 + (data[offset + byte] ?? 0)) >>> 0;
    sum = (sum + word) >>> 0;
  }
  return sum;
}

test("Batang TTC faces 0 and 1 extract to standalone checksum-valid SFNT", { skip: !existsSync(BATANG_TTC) }, () => {
  const source = readFileSync(BATANG_TTC);
  const before = createHash("sha256").update(source).digest("hex");
  const collection = inspectSfntCollection(source);
  assert.ok(collection.faces.length >= 2);
  const expectedFamilies = ["Batang", "BatangChe"];
  for (const faceIndex of [0, 1]) {
    assert.ok(collection.faces[faceIndex].familyNames.some((name) => name.replace(/\s/g, "").toLowerCase() === expectedFamilies[faceIndex].toLowerCase()));
    const receipt = inspectPowerPointFontCollectionInput({ data: source, collectionFaceIndex: faceIndex });
    assert.equal(receipt.state, "collection-face-extracted");
    assert.equal(receipt.reasonCode, null);
    const extracted = extractSfntCollectionFace(source, faceIndex);
    assert.notEqual(extracted.data.subarray(0, 4).toString("ascii"), "ttcf");
    assert.equal(sfntChecksum(extracted.data), 0xb1b0afba);
    const standalone = inspectSfntFont(extracted.data);
    assert.equal(standalone.fsType, collection.faces[faceIndex].fsType);
    assert.deepEqual(standalone.familyNames, collection.faces[faceIndex].familyNames);
    const wrapped = createPowerPointFontData(extracted.data, { charset: 129 });
    assert.ok(wrapped.data.length > extracted.data.length);
    assert.equal(wrapped.metadata.fsType, standalone.fsType);
  }
  assert.equal(createHash("sha256").update(source).digest("hex"), before);
});
