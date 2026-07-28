import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createPowerPointFontData,
  embedPowerPointFonts,
} from "./pptx-font-embedding.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const CONTENT_TYPES = `<?xml version="1.0"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
</Types>`;
const PRESENTATION = `<?xml version="1.0"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst/>
  <p:sldSz cx="12192000" cy="6858000"/>
  <p:notesSz cx="6858000" cy="9144000"/>
  <p:defaultTextStyle/>
</p:presentation>`;
const RELATIONSHIPS = `<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>
</Relationships>`;

function skeleton() {
  return writePptxArchive(
    new Map([
      ["[Content_Types].xml", Buffer.from(CONTENT_TYPES)],
      ["ppt/presentation.xml", Buffer.from(PRESENTATION)],
      ["ppt/_rels/presentation.xml.rels", Buffer.from(RELATIONSHIPS)],
    ])
  );
}

function decryptEotFontData(eot) {
  let cursor = 82;
  for (let index = 0; index < 4; index += 1) {
    const length = eot.readUInt16LE(cursor);
    cursor += 2 + length + (index < 3 ? 2 : 0);
  }
  const output = Buffer.alloc(eot.length - cursor);
  for (let index = 0; index < output.length; index += 1) {
    output[index] = eot[cursor + index] ^ 0x50;
  }
  return output;
}

const WINDOWS_STATIC_TTF = "C:\\Windows\\Fonts\\arial.ttf";

test("EOT writer records license metadata, XOR flag, and recoverable SFNT bytes", async (t) => {
  let font;
  try {
    font = await readFile(WINDOWS_STATIC_TTF);
  } catch {
    t.skip("Windows Arial static TTF is unavailable");
    return;
  }
  const wrapped = createPowerPointFontData(font);
  assert.equal(wrapped.data.readUInt32LE(0), wrapped.data.length);
  assert.equal(wrapped.data.readUInt32LE(4), font.length);
  assert.equal(wrapped.data.readUInt32LE(8), 0x00010000);
  assert.equal(wrapped.data.readUInt32LE(12) & 0x10000000, 0x10000000);
  assert.equal(wrapped.data.readUInt16LE(34), 0x504c);
  // W3C EOT 1.0 has no padding field after FullName; FontData starts there.
  let fontDataOffset = 82;
  for (let index = 0; index < 4; index += 1) {
    const length = wrapped.data.readUInt16LE(fontDataOffset);
    fontDataOffset += 2 + length + (index < 3 ? 2 : 0);
  }
  assert.equal(wrapped.data[fontDataOffset], font[0] ^ 0x50);
  assert.deepEqual(decryptEotFontData(wrapped.data), font);
});

test("packager writes real PresentationML font parts, relationships, and content type", async (t) => {
  let font;
  try {
    font = await readFile(WINDOWS_STATIC_TTF);
  } catch {
    t.skip("Windows Arial static TTF is unavailable");
    return;
  }
  const embedded = embedPowerPointFonts(skeleton(), [
    {
      typeface: "Arial",
      pitchFamily: 34,
      charset: 0,
      faces: { regular: { data: font, source: WINDOWS_STATIC_TTF } },
    },
  ]);
  const entries = readPptxArchive(embedded.pptx);
  const presentation = entries.get("ppt/presentation.xml").toString("utf8");
  const relationships = entries
    .get("ppt/_rels/presentation.xml.rels")
    .toString("utf8");
  const contentTypes = entries.get("[Content_Types].xml").toString("utf8");

  assert.equal(embedded.result.applied, true);
  assert.equal(embedded.result.embeddedFontFiles, 1);
  assert.equal(embedded.result.embeddedTypefaces, 1);
  assert.match(presentation, /xmlns:r="http:\/\/schemas\.openxmlformats\.org\/officeDocument\/2006\/relationships"/);
  assert.match(presentation, /<p:presentation\b[^>]*\bembedTrueTypeFonts="1"/);
  assert.match(
    presentation,
    /<p:embeddedFontLst><p:embeddedFont><p:font typeface="Arial" pitchFamily="34" charset="0"\/><p:regular r:id="rId3"\/><\/p:embeddedFont><\/p:embeddedFontLst>/
  );
  assert.ok(presentation.indexOf("<p:embeddedFontLst>") < presentation.indexOf("<p:defaultTextStyle"));
  assert.match(
    relationships,
    /Id="rId3" Type="http:\/\/schemas\.openxmlformats\.org\/officeDocument\/2006\/relationships\/font" Target="fonts\/font1\.fntdata"/
  );
  assert.match(
    contentTypes,
    /<Default Extension="fntdata" ContentType="application\/x-fontdata"\/>/
  );
  const fontPart = entries.get("ppt/fonts/font1.fntdata");
  assert.ok(fontPart);
  assert.deepEqual(decryptEotFontData(fontPart), font);
});

test("packager converts unsigned Win32 charsets to signed OOXML bytes", async (t) => {
  let font;
  try {
    font = await readFile(WINDOWS_STATIC_TTF);
  } catch {
    t.skip("Windows Arial static TTF is unavailable");
    return;
  }
  const embedded = embedPowerPointFonts(skeleton(), [
    {
      typeface: "Korean test face",
      pitchFamily: 34,
      charset: 129,
      faces: { regular: { data: font } },
    },
  ]);
  const entries = readPptxArchive(embedded.pptx);
  const presentation = entries.get("ppt/presentation.xml").toString("utf8");
  const fontPart = entries.get("ppt/fonts/font1.fntdata");

  assert.match(
    presentation,
    /<p:font typeface="Korean test face" pitchFamily="34" charset="-127"\/>/
  );
  assert.equal(embedded.result.fonts[0].charset, -127);
  assert.equal(fontPart[26], 129);
});

test("packager is opt-in/no-op for an empty approved face set", () => {
  const source = skeleton();
  const embedded = embedPowerPointFonts(source, []);
  assert.deepEqual(embedded.pptx, source);
  assert.deepEqual(embedded.result, {
    requested: true,
    applied: false,
    embeddedFontFiles: 0,
    embeddedTypefaces: 0,
    fonts: [],
    reason: "No embeddable font faces were supplied.",
  });
});

test("packager rejects variable fonts instead of claiming PowerPoint compatibility", async (t) => {
  let variable;
  try {
    variable = await readFile("C:\\Windows\\Fonts\\NotoSansKR-VF.ttf");
  } catch {
    t.skip("Noto Sans KR variable font is unavailable");
    return;
  }
  assert.throws(
    () =>
      embedPowerPointFonts(skeleton(), [
        {
          typeface: "Noto Sans KR",
          faces: { regular: { data: variable } },
        },
      ]),
    /Variable fonts are not packaged directly/
  );
});

test("packager rejects a mismatched face declaration and existing embedding", async (t) => {
  let font;
  try {
    font = await readFile(WINDOWS_STATIC_TTF);
  } catch {
    t.skip("Windows Arial static TTF is unavailable");
    return;
  }
  assert.throws(
    () =>
      embedPowerPointFonts(skeleton(), [
        { typeface: "Arial", faces: { bold: { data: font } } },
      ]),
    /incompatible weight metadata/
  );
  const once = embedPowerPointFonts(skeleton(), [
    { typeface: "Arial", faces: { regular: { data: font } } },
  ]);
  assert.throws(
    () =>
      embedPowerPointFonts(once.pptx, [
        { typeface: "Arial", faces: { regular: { data: font } } },
      ]),
    /already contains embedded font/
  );
});
