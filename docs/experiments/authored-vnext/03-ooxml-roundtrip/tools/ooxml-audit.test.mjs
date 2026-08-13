import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { deflateRawSync } from "node:zlib";

import { comparePackages, inspectPackage, runCli } from "./ooxml-audit.mjs";

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let value = n;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[n] = value >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function buildZip(entries, { deflate = false } = {}) {
  const localRecords = [];
  const centralRecords = [];
  let offset = 0;
  for (const [name, raw] of Object.entries(entries)) {
    const nameBuffer = Buffer.from(name, "utf8");
    const payload = Buffer.isBuffer(raw) ? raw : Buffer.from(raw, "utf8");
    const compressed = deflate ? deflateRawSync(payload) : payload;
    const method = deflate ? 8 : 0;
    const checksum = crc32(payload);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0);
    local.writeUInt16LE(20, 4);
    local.writeUInt16LE(0x0800, 6);
    local.writeUInt16LE(method, 8);
    local.writeUInt32LE(checksum, 14);
    local.writeUInt32LE(compressed.length, 18);
    local.writeUInt32LE(payload.length, 22);
    local.writeUInt16LE(nameBuffer.length, 26);
    localRecords.push(local, nameBuffer, compressed);

    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0);
    central.writeUInt16LE(20, 4);
    central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x0800, 8);
    central.writeUInt16LE(method, 10);
    central.writeUInt32LE(checksum, 16);
    central.writeUInt32LE(compressed.length, 20);
    central.writeUInt32LE(payload.length, 24);
    central.writeUInt16LE(nameBuffer.length, 28);
    central.writeUInt32LE(offset, 42);
    centralRecords.push(central, nameBuffer);
    offset += local.length + nameBuffer.length + compressed.length;
  }
  const centralDirectory = Buffer.concat(centralRecords);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(Object.keys(entries).length, 8);
  end.writeUInt16LE(Object.keys(entries).length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...localRecords, centralDirectory, end]);
}

const contentTypes = `<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="bin" ContentType="application/octet-stream"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml"/>
  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/comments/comment1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.comments+xml"/>
  <Override PartName="/ppt/diagrams/data1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml"/>
  <Override PartName="/ppt/embeddings/oleObject1.bin" ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>
  <Override PartName="/ppt/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/>
</Types>`;

function fixtureEntries({ editedSlide = false, normalizedFormatting = false, removeSlide2 = false } = {}) {
  const entries = {
    "[Content_Types].xml": contentTypes,
    "_rels/.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>`,
    "ppt/presentation.xml": `<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst/></p:presentation>`,
    "ppt/_rels/presentation.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
      <Relationship Id="rId4" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" Target="vbaProject.bin"/>
      <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml" Target="../customXml/item1.xml"/>
    </Relationships>`,
    "ppt/slides/slide1.xml": `<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><a:r><a:rPr${normalizedFormatting ? ' b="0" i="0"' : ""}/><a:t>${editedSlide ? "edited" : "original"}</a:t></a:r></p:spTree></p:cSld><p:timing/></p:sld>`,
    "ppt/slides/_rels/slide1.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
      <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/>
      <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="../embeddings/oleObject1.bin"/>
      <Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="../comments/comment1.xml"/>
      <Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid/" TargetMode="External"/>
    </Relationships>`,
    "ppt/slides/slide2.xml": `<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld/></p:sld>`,
    "ppt/slides/_rels/slide2.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`,
    "ppt/slideMasters/slideMaster1.xml": `<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>`,
    "ppt/slideMasters/_rels/slideMaster1.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`,
    "ppt/slideLayouts/slideLayout1.xml": `<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>`,
    "ppt/slideLayouts/_rels/slideLayout1.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>`,
    "ppt/media/image1.png": Buffer.from([0x89, 0x50, 0x4e, 0x47]),
    "ppt/diagrams/data1.xml": `<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram"/>`,
    "ppt/embeddings/oleObject1.bin": Buffer.from("synthetic OLE evidence"),
    "ppt/comments/comment1.xml": `<p:cmLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>`,
    "ppt/commentAuthors.xml": `<p:cmAuthorLst xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>`,
    "ppt/vbaProject.bin": Buffer.from("synthetic macro evidence"),
    "customXml/item1.xml": `<synthetic>custom XML evidence</synthetic>`,
  };
  if (removeSlide2) delete entries["ppt/slides/slide2.xml"];
  return entries;
}

test("audits topology, relationship closure, and sensitive feature evidence", () => {
  const directory = mkdtempSync(join(tmpdir(), "ooxml-audit-"));
  try {
    const file = join(directory, "synthetic.pptm");
    writeFileSync(file, buildZip(fixtureEntries(), { deflate: true }));
    const report = inspectPackage(file);
    assert.equal(report.validation.validOpcReadBack, true);
    assert.equal(report.topology.slideCount, 2);
    assert.equal(report.topology.slideMasterCount, 1);
    assert.equal(report.topology.slideLayoutCount, 1);
    assert.equal(report.topology.mediaCount, 1);
    assert.equal(report.relationships.missingTargets.length, 0);
    assert.equal(report.inventory.parts.find((part) => part.name === "ppt/commentAuthors.xml").category, "comments");
    for (const feature of ["macros", "externalRelationships", "smartArt", "ole", "animation", "comments", "customXml"]) {
      assert.equal(report.features[feature].present, true, `${feature} should be detected`);
    }
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("rejects a non-canonical ZIP directory path", () => {
  const directory = mkdtempSync(join(tmpdir(), "ooxml-audit-"));
  try {
    const file = join(directory, "unsafe.pptx");
    writeFileSync(file, buildZip({ ...fixtureEntries(), "ppt/transient/../": Buffer.alloc(0) }));
    assert.throws(() => inspectPackage(file), /Non-canonical ZIP entry name/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("reports exact no-op and intentional-edit preservation invariants", () => {
  const directory = mkdtempSync(join(tmpdir(), "ooxml-audit-"));
  try {
    const before = join(directory, "before.pptm");
    const noop = join(directory, "noop.pptm");
    const edited = join(directory, "edited.pptm");
    writeFileSync(before, buildZip(fixtureEntries()));
    writeFileSync(noop, buildZip(fixtureEntries()));
    writeFileSync(edited, buildZip(fixtureEntries({ editedSlide: true })));

    const noopReport = comparePackages(before, noop);
    assert.equal(noopReport.invariants.pass, true);
    assert.equal(noopReport.summary.changedPartCount, 0);
    assert.equal(noopReport.summary.relationshipClosureChangeCount, 0);

    const editReport = comparePackages(before, edited, { allowedTextChanges: ["ppt/slides/slide1.xml"] });
    assert.equal(editReport.invariants.pass, true);
    assert.equal(editReport.summary.changedPartCount, 1);
    assert.equal(editReport.summary.allowedObservedChangeCount, 1);
    assert.equal(editReport.summary.untouchedPayloadChangeCount, 0);
    assert.equal(editReport.partDiff.changed[0].rewrite.classification, "drawing-text-content-only");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("strict text policy rejects formatting normalization inside the target slide", () => {
  const directory = mkdtempSync(join(tmpdir(), "ooxml-audit-"));
  try {
    const before = join(directory, "before.pptm");
    const after = join(directory, "after.pptm");
    writeFileSync(before, buildZip(fixtureEntries()));
    writeFileSync(after, buildZip(fixtureEntries({ editedSlide: true, normalizedFormatting: true })));
    const report = comparePackages(before, after, { allowedTextChanges: ["ppt/slides/slide1.xml"] });
    assert.equal(report.invariants.pass, false);
    assert.equal(report.summary.strictTextChangeViolationCount, 1);
    assert.equal(report.partDiff.strictTextChangeViolations[0].reason, "xml-structural-or-formatting");
    assert.equal(report.partDiff.changed[0].rewrite.changedTextNodeCount, 1);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("fails preservation when a referenced part is missing", () => {
  const directory = mkdtempSync(join(tmpdir(), "ooxml-audit-"));
  try {
    const before = join(directory, "before.pptm");
    const after = join(directory, "after.pptm");
    writeFileSync(before, buildZip(fixtureEntries()));
    writeFileSync(after, buildZip(fixtureEntries({ removeSlide2: true })));
    const report = comparePackages(before, after);
    assert.equal(report.invariants.pass, false);
    assert.equal(report.invariants.missingPartCount, 1);
    assert.equal(report.invariants.missingRelationshipTargetCountAfter, 1);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("CLI emits machine-readable JSON and returns 2 for an invariant violation", () => {
  const directory = mkdtempSync(join(tmpdir(), "ooxml-audit-"));
  try {
    const before = join(directory, "before.pptm");
    const after = join(directory, "after.pptm");
    const output = join(directory, "report.json");
    writeFileSync(before, buildZip(fixtureEntries()));
    writeFileSync(after, buildZip(fixtureEntries({ removeSlide2: true })));
    const exitCode = runCli(["compare", before, after, "--fail-on-violation", "--output", output]);
    assert.equal(exitCode, 2);
    const report = JSON.parse(readFileSync(output, "utf8"));
    assert.equal(report.schemaVersion, "ooxml-audit/v1");
    assert.equal(report.invariants.pass, false);
    assert.throws(() => runCli(["inspect", before, "--output", output]), /Refusing to overwrite/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
