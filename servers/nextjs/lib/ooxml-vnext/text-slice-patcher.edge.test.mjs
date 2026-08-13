import assert from "node:assert/strict";
import test from "node:test";

import { patchTextSlice } from "./text-slice-patcher.ts";

function shape({ id = 7, run, metadata = "" }) {
  return `<p:sp><p:nvSpPr><p:cNvPr id="${id}" name="edge${id}" descr="${metadata}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:p>${run}<a:endParaRPr/></a:p></p:txBody></p:sp>`;
}

function part(shapes, declaration = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>') {
  return `${declaration}\r\n<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:x="urn:test"><p:cSld><p:spTree>${shapes}</p:spTree></p:cSld></p:sld>`;
}

function request(expected, replacement, overrides = {}) {
  return {
    partName: "ppt/slides/slide1.xml",
    selector: { kind: "element", id: 7 },
    expected: { text: expected },
    replacement,
    ...overrides,
  };
}

function assertOnlyApprovedSliceChanged(source, result) {
  const before = Buffer.from(source);
  const after = Buffer.from(result.bytes);
  const { sourceSlice, resultSlice } = result.receipt;
  assert.deepEqual(after.subarray(0, resultSlice.startByte), before.subarray(0, sourceSlice.startByte));
  assert.deepEqual(after.subarray(resultSlice.endByte), before.subarray(sourceSlice.endByte));
  assert.equal(result.receipt.outsideSliceDiffBytes, 0);
  assert.equal(result.receipt.outsideSliceUnchanged, true);
}

test("text edge corpus patches one plain a:t slice and preserves every outside byte", () => {
  const cases = [
    { name: "ascii-lengthens", old: "Q1", replacement: "Quarter one" },
    { name: "utf8-korean-shortens", old: "대한민국 분기 계획", replacement: "계획" },
    { name: "astral-and-combining", old: "🚀 cafe\u0301", replacement: "🛰️ résumé" },
    { name: "entities", encoded: "A &amp; B &lt; C &gt; D", old: "A & B < C > D", replacement: 'R&D <ready> "now"' },
    { name: "empty-replacement", old: "remove me", replacement: "" },
  ];

  for (const entry of cases) {
    const encoded = entry.encoded ?? entry.old;
    const source = Buffer.from(
      part(shape({ id: 7, metadata: "literal > stays quoted", run: `<a:r><a:rPr lang="ko-KR"/><a:t>${encoded}</a:t></a:r>` })),
      "utf8",
    );
    const result = patchTextSlice(source, request(entry.old, entry.replacement));
    assertOnlyApprovedSliceChanged(source, result);
    assert.equal(
      Buffer.from(result.bytes).subarray(result.receipt.resultSlice.startByte, result.receipt.resultSlice.endByte).toString("utf8"),
      entry.replacement.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
      entry.name,
    );
  }
});

test("UTF-8 BOM remains byte-aligned and untouched", () => {
  const body = Buffer.from(part(shape({ run: "<a:r><a:t>BOM text</a:t></a:r>" })), "utf8");
  const source = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), body]);
  const result = patchTextSlice(source, request("BOM text", "BOM replacement"));

  assert.deepEqual(Buffer.from(result.bytes).subarray(0, 3), Buffer.from([0xef, 0xbb, 0xbf]));
  assertOnlyApprovedSliceChanged(source, result);
  assert.equal(result.receipt.sourceSlice.startByte, source.indexOf(Buffer.from("BOM text")));
});

test("part-kind corpus permits only slide, layout, and master XML members", () => {
  const source = Buffer.from(part(shape({ run: "<a:r><a:t>Old</a:t></a:r>" })), "utf8");
  for (const partName of [
    "ppt/slides/slide90.xml",
    "ppt/slideLayouts/slideLayout12.xml",
    "ppt/slideMasters/slideMaster3.xml",
  ]) {
    const result = patchTextSlice(source, request("Old", "New", { partName }));
    assertOnlyApprovedSliceChanged(source, result);
  }
  for (const partName of [
    "ppt/slides/slide0.xml",
    "ppt/slides/slide01.xml",
    "ppt/notesSlides/notesSlide1.xml",
    "ppt/slides/slide1.xml.rels",
    "ppt/slides/../slides/slide1.xml",
  ]) {
    assert.throws(() => patchTextSlice(source, request("Old", "New", { partName })), /part-name-is-not-an-approved-text-part/);
  }
});

test("ambiguous shape and ambiguous text selection fail closed", () => {
  const duplicateShape = Buffer.from(
    part(`${shape({ id: 7, run: "<a:r><a:t>Same</a:t></a:r>" })}${shape({ id: 7, run: "<a:r><a:t>Same</a:t></a:r>" })}`),
  );
  assert.throws(() => patchTextSlice(duplicateShape, request("Same", "Changed")), /selector-shape-count-2/);

  const duplicateText = Buffer.from(
    part(shape({ run: "<a:r><a:t>Same</a:t></a:r><a:r><a:t>Same</a:t></a:r>" })),
  );
  assert.throws(() => patchTextSlice(duplicateText, request("Same", "Changed")), /expected-text-node-count-2/);
});

test("cross-run, rich-child, wrapped, multi-text, and field text fail closed", () => {
  const rejected = [
    {
      name: "cross-run",
      run: "<a:r><a:t>Hello </a:t></a:r><a:r><a:t>world</a:t></a:r>",
      expected: "Hello world",
      reason: /expected-text-node-count-0/,
    },
    {
      name: "line-break-rich-run",
      run: "<a:r><a:t>Hello</a:t><a:br/></a:r>",
      expected: "Hello",
      reason: /plain-run-has-rich-or-unsupported-children/,
    },
    {
      name: "unknown-wrapper",
      run: "<a:r><x:wrapper><a:t>Hello</a:t></x:wrapper></a:r>",
      expected: "Hello",
      reason: /text-is-not-in-a-plain-run/,
    },
    {
      name: "two-text-nodes-one-run",
      run: "<a:r><a:t>Hello</a:t><a:t>Other</a:t></a:r>",
      expected: "Hello",
      reason: /plain-run-has-multiple-text-nodes/,
    },
    {
      name: "field",
      run: '<a:fld id="{00000000-0000-0000-0000-000000000000}" type="slidenum"><a:rPr/><a:t>1</a:t></a:fld>',
      expected: "1",
      reason: /field-text-is-not-mutable/,
    },
    {
      name: "direct-text-outside-a-t",
      run: "<a:r>rogue<a:t>Hello</a:t></a:r>",
      expected: "Hello",
      reason: /plain-run-has-direct-text-outside-a-t/,
    },
  ];

  for (const entry of rejected) {
    const source = Buffer.from(part(shape({ run: entry.run })));
    assert.throws(
      () => patchTextSlice(source, request(entry.expected, "Changed")),
      entry.reason,
      entry.name,
    );
  }
});

test("encoding, entity, XML markup, whitespace, and scalar hazards fail closed", () => {
  const normal = Buffer.from(part(shape({ run: "<a:r><a:t>Old</a:t></a:r>" })));
  const declaredLatin1 = Buffer.from(
    part(shape({ run: "<a:r><a:t>Old</a:t></a:r>" }), '<?xml version="1.0" encoding="windows-1252"?>'),
  );
  assert.throws(() => patchTextSlice(declaredLatin1, request("Old", "New")), /source-declares-a-non-utf8-encoding/);
  assert.throws(() => patchTextSlice(Uint8Array.from([0xff, 0xfe, 0x3c, 0x00]), request("Old", "New")), /source-is-not-utf8/);

  const unsupportedEntity = Buffer.from(part(shape({ run: "<a:r><a:t>A&nbsp;B</a:t></a:r>" })));
  assert.throws(() => patchTextSlice(unsupportedEntity, request("A B", "New")), /a-t-text-contains-an-unsupported-entity/);
  const inlineMarkup = Buffer.from(part(shape({ run: "<a:r><a:t>A<x:mark/>B</a:t></a:r>" })));
  assert.throws(() => patchTextSlice(inlineMarkup, request("AB", "New")), /a-t-text-contains-markup/);

  assert.throws(() => patchTextSlice(normal, request("Old", " leading")), /replacement-whitespace-would-require-xml-space-preserve/);
  assert.throws(() => patchTextSlice(normal, request("Old", "line\nbreak")), /replacement-requires-a-structural-line-break/);
  assert.throws(() => patchTextSlice(normal, request("Old", "bad\ud800")), /replacement-text-contains-an-unpaired-surrogate/);
});

test("xml:space preserve allows boundary whitespace without changing the opening tag", () => {
  const source = Buffer.from(part(shape({ run: '<a:r><a:rPr><a:solidFill><a:schemeClr val="tx1"/></a:solidFill></a:rPr><a:t xml:space="preserve"> old\t</a:t></a:r>' })));
  const result = patchTextSlice(source, request(" old\t", " new\t"));
  assertOnlyApprovedSliceChanged(source, result);
  assert.match(Buffer.from(result.bytes).toString("utf8"), /<a:t xml:space="preserve"> new\t<\/a:t>/);
});
