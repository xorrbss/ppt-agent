import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

import { backplatePixelSize } from "./capture-scale.ts";
import { nestPreparedNativeGroups } from "./native-plan.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const { width: BACKPLATE_WIDTH, height: BACKPLATE_HEIGHT } = backplatePixelSize();

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
</Types>`;
const PRESENTATION = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>`;
const SLIDE = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>`;
const RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`;

function skeleton() {
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from(CONTENT_TYPES)],
    ["ppt/presentation.xml", Buffer.from(PRESENTATION)],
    ["ppt/slides/slide1.xml", Buffer.from(SLIDE)],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from(RELS)],
  ]);
  return writePptxArchive(entries);
}

const transparentPng = () =>
  sharp({
    create: { width: BACKPLATE_WIDTH, height: BACKPLATE_HEIGHT, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } },
  })
    .png()
    .toBuffer();

function ownershipQidsIn(xml) {
  return [...xml.matchAll(/name="DevAX raster visual fragment ([^"]+) semantic-owner"/g)].map(
    (match) => match[1],
  );
}

test("banded slides still carry every declared raster owner", async () => {
  // A structural container whose native object was dropped paints on a native
  // band, so no raster island lists it. The slide still declares it as a raster
  // owner, and the readback compares against that declaration.
  const raster = await transparentPng();
  const pptx = assembleAuthoredHybridPptx(skeleton(), [
    {
      slideNumber: 1,
      backplatePng: raster,
      elements: [],
      rasterBands: [
        { zBand: 0, rasterPng: raster, semanticOwnerQids: ["s01-banded-lower"] },
        { zBand: 2, rasterPng: raster, semanticOwnerQids: ["s01-banded-upper"] },
      ],
      semanticOwnerQids: ["s01-banded-lower", "s01-banded-upper", "s01-unbanded-container"],
    },
  ]);
  const xml = readPptxArchive(pptx).get("ppt/slides/slide1.xml").toString("utf8");

  assert.deepEqual(ownershipQidsIn(xml).sort(), [
    "s01-banded-lower",
    "s01-banded-upper",
    "s01-unbanded-container",
  ]);
});

test("a QID a band already attributed is not carried twice", async () => {
  const raster = await transparentPng();
  const pptx = assembleAuthoredHybridPptx(skeleton(), [
    {
      slideNumber: 1,
      backplatePng: raster,
      elements: [],
      rasterBands: [{ zBand: 0, rasterPng: raster, semanticOwnerQids: ["s01-owner"] }],
      semanticOwnerQids: ["s01-owner"],
    },
  ]);
  const xml = readPptxArchive(pptx).get("ppt/slides/slide1.xml").toString("utf8");

  assert.deepEqual(ownershipQidsIn(xml), ["s01-owner"]);
});

test("two bands may not claim the same owner", async () => {
  const raster = await transparentPng();
  assert.throws(
    () =>
      assembleAuthoredHybridPptx(skeleton(), [
        {
          slideNumber: 1,
          backplatePng: raster,
          elements: [],
          rasterBands: [
            { zBand: 0, rasterPng: raster, semanticOwnerQids: ["s01-owner"] },
            { zBand: 2, rasterPng: raster, semanticOwnerQids: ["s01-owner"] },
          ],
        },
      ]),
    /semantic ownership QIDs must be unique/,
  );
});

const leaf = (id) => ({ kind: "shape", source: { id } });
const group = (id, childElementIds) => ({
  kind: "group",
  source: { id },
  childElementIds,
  children: [],
});

test("closed nesting is rebuilt as a tree", () => {
  const nested = nestPreparedNativeGroups([
    group("outer", ["inner", "c"]),
    group("inner", ["a", "b"]),
    leaf("a"),
    leaf("b"),
    leaf("c"),
  ]);

  assert.deepEqual(
    nested.map((item) => item.source.id),
    ["outer"],
  );
  assert.deepEqual(
    nested[0].children.map((item) => item.source.id),
    ["inner", "c"],
  );
  assert.deepEqual(
    nested[0].children[0].children.map((item) => item.source.id),
    ["a", "b"],
  );
});

test("a nested group that lost a child drops its whole chain instead of throwing", () => {
  // "inner" names "b", which layer safety kept out of the selection. Admitting
  // "outer" would close the tree through "inner" and dereference the missing
  // child, which used to throw "Cannot read properties of undefined".
  const nested = nestPreparedNativeGroups([
    group("outer", ["inner", "c"]),
    group("inner", ["a", "b"]),
    leaf("a"),
    leaf("c"),
  ]);

  assert.deepEqual(
    nested.map((item) => item.source.id).sort(),
    ["a", "c"],
  );
});

test("an unrelated closed group survives a sibling chain being dropped", () => {
  const nested = nestPreparedNativeGroups([
    group("broken", ["missing"]),
    group("intact", ["x", "y"]),
    leaf("x"),
    leaf("y"),
  ]);

  assert.deepEqual(
    nested.map((item) => item.source.id),
    ["intact"],
  );
  assert.deepEqual(
    nested[0].children.map((item) => item.source.id),
    ["x", "y"],
  );
});
